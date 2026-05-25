import os
import platform
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk

from PIL import Image

try:
    import windnd
    WINDND_AVAILABLE = True
except ImportError:
    WINDND_AVAILABLE = False


APP_TITLE = "PNG to WebP Converter Pro"
BG = "#f4f6fb"
CARD = "#ffffff"
TEXT = "#111827"
MUTED = "#6b7280"
BORDER = "#e5e7eb"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
SUCCESS = "#16a34a"
SUCCESS_HOVER = "#15803d"
WARNING = "#f59e0b"
ERROR = "#dc2626"


class ModernButton(tk.Frame):
    def __init__(
        self,
        parent,
        text,
        command,
        bg=ACCENT,
        fg="#ffffff",
        hover_bg=ACCENT_HOVER,
        disabled_bg="#e5e7eb",
        disabled_fg="#9ca3af",
        padx=9,
        pady=5,
    ):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)

        self.command = command
        self.enabled = True
        self.normal_bg = bg
        self.normal_fg = fg
        self.hover_bg = hover_bg
        self.disabled_bg = disabled_bg
        self.disabled_fg = disabled_fg

        self.label = tk.Label(
            self,
            text=text,
            bg=bg,
            fg=fg,
            font=("Segoe UI", 8, "bold"),
            padx=padx,
            pady=pady,
            cursor="hand2",
        )
        self.label.pack(fill="both", expand=True)

        for widget in (self, self.label):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _event):
        if self.enabled and self.command:
            self.command()

    def _enter(self, _event):
        if self.enabled:
            self.configure(bg=self.hover_bg)
            self.label.configure(bg=self.hover_bg)

    def _leave(self, _event):
        if self.enabled:
            self.configure(bg=self.normal_bg)
            self.label.configure(bg=self.normal_bg)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

        if enabled:
            self.configure(bg=self.normal_bg)
            self.label.configure(
                bg=self.normal_bg,
                fg=self.normal_fg,
                cursor="hand2",
            )
        else:
            self.configure(bg=self.disabled_bg)
            self.label.configure(
                bg=self.disabled_bg,
                fg=self.disabled_fg,
                cursor="arrow",
            )


class ModernSlider(tk.Canvas):
    def __init__(self, parent, from_=10, to=100, value=80, command=None, width=200, height=26):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=CARD,
            highlightthickness=0,
            bd=0,
        )

        self.min_val = from_
        self.max_val = to
        self.value = value
        self.command = command
        self.padding = 16
        self.width_value = width
        self.height_value = height
        self.track_width = width - self.padding * 2
        self.enabled = True

        self.create_line(
            self.padding,
            height // 2,
            width - self.padding,
            height // 2,
            fill="#dbeafe",
            width=5,
            capstyle="round",
            tags="track_bg",
        )
        self.create_line(
            self.padding,
            height // 2,
            self.padding,
            height // 2,
            fill=ACCENT,
            width=5,
            capstyle="round",
            tags="track_fg",
        )
        self.create_oval(0, 0, 0, 0, fill="#ffffff", outline="#bfdbfe", width=2, tags="thumb")

        self.bind("<Button-1>", self._move)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)

        self._draw()

    def get(self):
        return self.value

    def set(self, value, notify=False):
        self.value = max(self.min_val, min(self.max_val, float(value)))
        self._draw()
        if notify and self.command:
            self.command(self.value, released=True)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self.itemconfigure("track_bg", fill="#dbeafe" if enabled else "#e5e7eb")
        self.itemconfigure("track_fg", fill=ACCENT if enabled else "#cbd5e1")
        self.itemconfigure("thumb", fill="#ffffff" if enabled else "#f3f4f6", outline="#bfdbfe" if enabled else "#d1d5db")

    def _value_from_x(self, x):
        x = max(self.padding, min(self.width_value - self.padding, x))
        percent = (x - self.padding) / self.track_width
        return self.min_val + percent * (self.max_val - self.min_val)

    def _move(self, event):
        if not self.enabled:
            return
        self.set(self._value_from_x(event.x))
        if self.command:
            self.command(self.value, released=False)

    def _release(self, event):
        if not self.enabled:
            return
        self.set(self._value_from_x(event.x))
        if self.command:
            self.command(self.value, released=True)

    def _draw(self):
        percent = (self.value - self.min_val) / (self.max_val - self.min_val)
        x = self.padding + percent * self.track_width
        y = self.height_value // 2

        self.coords("track_fg", self.padding, y, x, y)
        self.coords("thumb", x - 8, y - 8, x + 8, y + 8)


class WebPConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("820x520")
        self.root.minsize(760, 500)
        self.root.configure(bg=BG)

        self.selected_files: list[Path] = []
        self.file_quality: dict[Path, int] = {}
        self.file_prediction: dict[Path, int] = {}
        self.output_dir: Path | None = None
        self.drop_queue = queue.Queue()
        self.is_converting = False
        self._prediction_job = None
        self._selected_file_for_quality: Path | None = None

        self._setup_styles()
        self._build_ui()

        if WINDND_AVAILABLE:
            self._enable_drag_drop()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "Modern.Horizontal.TProgressbar",
            troughcolor="#dbeafe",
            background=ACCENT,
            bordercolor="#dbeafe",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=8,
        )

    def _build_ui(self):
        self.header = tk.Frame(self.root, bg=CARD, height=58)
        self.header.pack(side="top", fill="x")
        self.header.pack_propagate(False)

        self.title_frame = tk.Frame(self.header, bg=CARD)
        self.title_frame.pack(side="left", padx=12, pady=8)

        self.icon = tk.Label(
            self.title_frame,
            text="🖼️",
            font=("Segoe UI Emoji", 20),
            bg="#eff6ff",
            fg=ACCENT,
            width=2,
        )
        self.icon.pack(side="left", padx=(0, 10))

        self.title_box = tk.Frame(self.title_frame, bg=CARD)
        self.title_box.pack(side="left")

        tk.Label(
            self.title_box,
            text=APP_TITLE,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        drop_text = (
            "Drag & drop PNG або папку з PNG у вікно"
            if WINDND_AVAILABLE
            else "Встанови windnd для drag & drop: pip install windnd"
        )
        tk.Label(
            self.title_box,
            text=drop_text,
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        self.buttons = tk.Frame(self.header, bg=CARD)
        self.buttons.pack(side="right", padx=10, pady=11)

        self.btn_add = ModernButton(
            self.buttons,
            "Додати PNG",
            self.select_files,
            bg=ACCENT,
            hover_bg=ACCENT_HOVER,
        )
        self.btn_add.pack(side="left", padx=3)

        self.btn_folder = ModernButton(
            self.buttons,
            "Зберегти в",
            self.select_output_folder,
            bg="#eef2ff",
            fg=ACCENT,
            hover_bg="#dbeafe",
        )
        self.btn_folder.pack(side="left", padx=3)

        self.btn_clear = ModernButton(
            self.buttons,
            "Очистити",
            self.clear_files,
            bg="#fee2e2",
            fg=ERROR,
            hover_bg="#fecaca",
        )
        self.btn_clear.pack(side="left", padx=3)

        self.btn_convert = ModernButton(
            self.buttons,
            "Конвертувати",
            self.start_conversion,
            bg=SUCCESS,
            hover_bg=SUCCESS_HOVER,
        )
        self.btn_convert.pack(side="left", padx=3)

        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(expand=True, fill="both", padx=10, pady=10)

        self.left_card = self._card(self.main)
        self.left_card.pack(side="left", expand=True, fill="both", padx=(0, 8))

        self.right_card = self._card(self.main, width=280)
        self.right_card.pack(side="right", fill="y", padx=(8, 0))
        self.right_card.pack_propagate(False)

        self._build_left()
        self._build_right()

        self._update_buttons()
        self._update_selected_file_quality_panel()

    def _card(self, parent, width=None):
        frame = tk.Frame(parent, bg=CARD, bd=0, highlightthickness=1, highlightbackground=BORDER)
        if width:
            frame.configure(width=width)
        return frame

    def _build_left(self):
        top = tk.Frame(self.left_card, bg=CARD)
        top.pack(fill="x", padx=10, pady=(8, 5))

        tk.Label(
            top,
            text="Файли",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left")

        self.count_label = tk.Label(
            top,
            text="0 PNG",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 10, "bold"),
        )
        self.count_label.pack(side="right")

        self.drop_zone = tk.Frame(
            self.left_card,
            bg="#eff6ff",
            highlightthickness=1,
            highlightbackground="#bfdbfe",
            height=88,
        )
        self.drop_zone.pack(fill="x", padx=10, pady=(0, 6))
        self.drop_zone.pack_propagate(False)

        self.drop_zone.bind("<Button-1>", lambda _e: self.select_files())

        self.drop_icon = tk.Label(
            self.drop_zone,
            text="📥",
            bg="#eff6ff",
            fg=ACCENT,
            font=("Segoe UI Emoji", 18),
            cursor="hand2",
        )
        self.drop_icon.place(relx=0.5, y=8, anchor="n")
        self.drop_icon.bind("<Button-1>", lambda _e: self.select_files())

        self.drop_label = tk.Label(
            self.drop_zone,
            text="Перетягни PNG сюди",
            bg="#eff6ff",
            fg=ACCENT,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        self.drop_label.place(relx=0.5, y=42, anchor="center")
        self.drop_label.bind("<Button-1>", lambda _e: self.select_files())

        self.drop_sub = tk.Label(
            self.drop_zone,
            text="або натисни для вибору",
            bg="#eff6ff",
            fg=MUTED,
            font=("Segoe UI", 8),
            cursor="hand2",
        )
        self.drop_sub.place(relx=0.5, y=66, anchor="center")
        self.drop_sub.bind("<Button-1>", lambda _e: self.select_files())

        hint = tk.Frame(self.left_card, bg=CARD)
        hint.pack(fill="x", padx=10, pady=(0, 4))

        tk.Label(
            hint,
            text="Клікни файл — зміни якість справа. У списку: розмір → прогноз.",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="left")

        list_wrap = tk.Frame(self.left_card, bg="#f9fafb", highlightthickness=1, highlightbackground=BORDER)
        list_wrap.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        self.files_list = tk.Listbox(
            list_wrap,
            bg="#f9fafb",
            fg=TEXT,
            selectbackground="#dbeafe",
            selectforeground=TEXT,
            font=("Segoe UI", 9),
            bd=0,
            highlightthickness=0,
            activestyle="none",
        )
        self.files_list.pack(side="left", expand=True, fill="both", padx=8, pady=8)
        self.files_list.bind("<<ListboxSelect>>", self.on_file_select)

        scrollbar = ttk.Scrollbar(list_wrap, orient="vertical", command=self.files_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.files_list.configure(yscrollcommand=scrollbar.set)

    def _build_right(self):
        tk.Label(
            self.right_card,
            text="Налаштування",
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 6))

        # Global quality.
        quality_box = tk.Frame(self.right_card, bg="#f9fafb", highlightthickness=1, highlightbackground=BORDER)
        quality_box.pack(fill="x", padx=10, pady=(0, 6))

        q_head = tk.Frame(quality_box, bg="#f9fafb")
        q_head.pack(fill="x", padx=8, pady=(6, 0))

        tk.Label(
            q_head,
            text="Якість за замовчуванням",
            bg="#f9fafb",
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        self.quality_label = tk.Label(
            q_head,
            text="80%",
            bg="#f9fafb",
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        )
        self.quality_label.pack(side="right")

        self.slider = ModernSlider(quality_box, command=self.on_quality_change, width=232, height=26)
        self.slider.pack(padx=6, pady=(0, 2))

        self.btn_apply_global = ModernButton(
            quality_box,
            "Застосувати до всіх файлів",
            self.apply_global_quality_to_all,
            bg="#eef2ff",
            fg=ACCENT,
            hover_bg="#dbeafe",
            padx=10,
            pady=5,
        )
        self.btn_apply_global.pack(anchor="w", padx=8, pady=(0, 6))

        # Per-file quality.
        per_file_box = tk.Frame(self.right_card, bg="#fff7ed", highlightthickness=1, highlightbackground="#fed7aa")
        per_file_box.pack(fill="x", padx=10, pady=(0, 6))

        pf_head = tk.Frame(per_file_box, bg="#fff7ed")
        pf_head.pack(fill="x", padx=8, pady=(6, 0))

        tk.Label(
            pf_head,
            text="Якість вибраного файлу",
            bg="#fff7ed",
            fg="#9a3412",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        self.per_file_quality_label = tk.Label(
            pf_head,
            text="—",
            bg="#fff7ed",
            fg="#9a3412",
            font=("Segoe UI", 9, "bold"),
        )
        self.per_file_quality_label.pack(side="right")

        self.selected_file_label = tk.Label(
            per_file_box,
            text="Файл не вибрано",
            bg="#fff7ed",
            fg=MUTED,
            font=("Segoe UI", 8),
            wraplength=235,
            justify="left",
        )
        self.selected_file_label.pack(anchor="w", padx=8, pady=(0, 1))

        self.per_file_slider = ModernSlider(per_file_box, command=self.on_per_file_quality_change, width=232, height=26)
        self.per_file_slider.pack(padx=6, pady=(0, 5))

        # Збереження.
        output_box = tk.Frame(self.right_card, bg="#f9fafb", highlightthickness=1, highlightbackground=BORDER)
        output_box.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(
            output_box,
            text="Збереження",
            bg="#f9fafb",
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=8, pady=(5, 2))

        self.output_label = tk.Label(
            output_box,
            text="Збереження біля оригіналу",
            bg="#f9fafb",
            fg=MUTED,
            font=("Segoe UI", 8),
            wraplength=235,
            justify="left",
        )
        self.output_label.pack(anchor="w", padx=8, pady=(0, 4))

        self.open_var = tk.IntVar(value=0)
        self.open_check = tk.Checkbutton(
            output_box,
            text="Відкрити після завершення",
            variable=self.open_var,
            bg="#f9fafb",
            fg=MUTED,
            activebackground="#f9fafb",
            font=("Segoe UI", 8),
            bd=0,
            highlightthickness=0,
        )
        self.open_check.pack(anchor="w", padx=4, pady=(0, 5))

        status_box = tk.Frame(self.right_card, bg=CARD)
        status_box.pack(fill="x", padx=10, pady=(0, 0))

        self.status_label = tk.Label(
            status_box,
            text="Готово",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 10, "bold"),
        )
        self.status_label.pack(anchor="w")

        self.estimate_label = tk.Label(
            status_box,
            text="",
            bg=CARD,
            fg=WARNING,
            font=("Segoe UI", 8, "bold"),
            wraplength=250,
            justify="left",
        )
        self.estimate_label.pack(anchor="w", pady=(5, 0))

        self.current_label = tk.Label(
            status_box,
            text="",
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 8),
            wraplength=250,
            justify="left",
        )
        self.current_label.pack(anchor="w", pady=(5, 0))

        self.progress = ttk.Progressbar(
            status_box,
            style="Modern.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress.pack(fill="x", pady=(7, 0))

        self.analytics_label = tk.Label(
            status_box,
            text="",
            bg=CARD,
            fg=SUCCESS,
            font=("Segoe UI", 9, "bold"),
            wraplength=250,
            justify="left",
        )
        self.analytics_label.pack(anchor="w", pady=(7, 0))

    def _enable_drag_drop(self):
        try:
            windnd.hook_dropfiles(self.root.winfo_id(), self._handle_drop)
            self._poll_drop_queue()
        except Exception as exc:
            self.status_label.configure(text=f"Drag & drop error: {exc}", fg=ERROR)

    def _handle_drop(self, files):
        paths = []

        for item in files:
            try:
                path = item.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    path = item.decode("cp1251")
                except Exception:
                    continue

            paths.append(path)

        self.drop_queue.put(paths)

    def _poll_drop_queue(self):
        try:
            while True:
                paths = self.drop_queue.get_nowait()
                self.add_files(paths, source="Drag & drop")
                self.drop_queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._poll_drop_queue)

    def select_files(self):
        if self.is_converting:
            return

        files = filedialog.askopenfilenames(
            title="Вибери PNG",
            filetypes=[("PNG files", "*.png")],
        )

        if files:
            self.add_files(files, source="Вибрано")

    def select_output_folder(self):
        if self.is_converting:
            return

        folder = filedialog.askdirectory(title="Вибери папку збереження")

        if not folder:
            return

        self.output_dir = Path(folder)
        self.output_label.configure(text=self.shorten_path(self.output_dir), fg=SUCCESS)

    def add_files(self, paths, source="Додано"):
        existing = {p.resolve() for p in self.selected_files if p.exists()}
        added = 0
        default_quality = int(self.slider.get())

        for raw in paths:
            path = Path(raw).expanduser()

            if path.is_dir():
                candidates = list(path.glob("*.png"))
            else:
                candidates = [path]

            for candidate in candidates:
                try:
                    normalized = candidate.resolve()
                except Exception:
                    continue

                if not normalized.exists():
                    continue

                if normalized.suffix.lower() != ".png":
                    continue

                if normalized not in existing:
                    self.selected_files.append(normalized)
                    self.file_quality[normalized] = default_quality
                    self.file_prediction.pop(normalized, None)
                    existing.add(normalized)
                    added += 1

        if added:
            self.status_label.configure(text=f"{source}: +{added}", fg=ACCENT)
            self.drop_label.configure(text=f"Додано: {len(self.selected_files)} PNG", fg=SUCCESS)
            self.drop_zone.configure(bg="#f0fdf4", highlightbackground="#bbf7d0")
            self.drop_icon.configure(bg="#f0fdf4")
            self.drop_label.configure(bg="#f0fdf4")
            self.drop_sub.configure(bg="#f0fdf4")
        else:
            self.status_label.configure(text="PNG не знайдено", fg=ERROR)

        self.refresh_files(preserve_scroll=False)
        self.schedule_prediction()

    def clear_files(self):
        if self.is_converting:
            return

        self.selected_files.clear()
        self.file_quality.clear()
        self.file_prediction.clear()
        self._selected_file_for_quality = None

        self.progress["value"] = 0
        self.current_label.configure(text="")
        self.analytics_label.configure(text="")
        self.estimate_label.configure(text="")
        self.status_label.configure(text="Готово", fg=MUTED)

        self.drop_zone.configure(bg="#eff6ff", highlightbackground="#bfdbfe")
        self.drop_icon.configure(bg="#eff6ff")
        self.drop_label.configure(bg="#eff6ff", text="Перетягни PNG сюди", fg=ACCENT)
        self.drop_sub.configure(bg="#eff6ff")

        self.refresh_files(preserve_scroll=False)
        self._update_selected_file_quality_panel()

    def refresh_files(self, preserve_scroll=True):
        selected_index = None

        try:
            scroll_top = self.files_list.yview()[0] if preserve_scroll else 0.0
        except Exception:
            scroll_top = 0.0

        if self._selected_file_for_quality in self.selected_files:
            try:
                selected_index = self.selected_files.index(self._selected_file_for_quality)
            except ValueError:
                selected_index = None

        self.count_label.configure(text=f"{len(self.selected_files)} PNG")
        self.files_list.delete(0, tk.END)

        for index, path in enumerate(self.selected_files, start=1):
            original_size = self.format_size(path.stat().st_size) if path.exists() else "missing"
            predicted_size = self.file_prediction.get(path)

            if predicted_size is None:
                # Жовтий для рядків у стані очікування прогнозу
                entry = f"{index}. {self.truncate_middle(path.name, 32)} · {original_size} → прогноз..."
                self.files_list.insert(tk.END, entry)
                self.files_list.itemconfigure(tk.END, fg=WARNING)
            else:
                # Зелений для рядків з готовим прогнозом
                entry = f"{index}. {self.truncate_middle(path.name, 32)} · {original_size} → {self.format_size(predicted_size)}"
                self.files_list.insert(tk.END, entry)
                self.files_list.itemconfigure(tk.END, fg=SUCCESS)

        if selected_index is not None:
            self.files_list.selection_clear(0, tk.END)
            self.files_list.selection_set(selected_index)
            self.files_list.activate(selected_index)

        if preserve_scroll:
            self.root.after_idle(lambda pos=scroll_top: self.files_list.yview_moveto(pos))

        self._update_buttons()

    def _update_buttons(self):
        has_files = bool(self.selected_files)
        has_selection = self._selected_file_for_quality is not None
        converting = self.is_converting

        self.btn_convert.set_enabled(has_files and not converting)
        self.btn_clear.set_enabled(has_files and not converting)
        self.btn_add.set_enabled(not converting)
        self.btn_folder.set_enabled(not converting)
        self.btn_apply_global.set_enabled(has_files and not converting)
        self.per_file_slider.set_enabled(has_files and not converting and has_selection)

    def on_file_select(self, _event=None):
        selection = self.files_list.curselection()

        if not selection:
            self._selected_file_for_quality = None
        else:
            index = selection[0]
            if 0 <= index < len(self.selected_files):
                self._selected_file_for_quality = self.selected_files[index]

        self._update_selected_file_quality_panel()
        self._update_buttons()

    def _update_selected_file_quality_panel(self):
        selected = self._selected_file_for_quality

        if selected is None or selected not in self.selected_files:
            self.selected_file_label.configure(text="Файл не вибрано")
            self.per_file_quality_label.configure(text="—")
            return

        quality = self.file_quality.get(selected, int(self.slider.get()))
        predicted = self.file_prediction.get(selected)

        original_text = self.format_size(selected.stat().st_size) if selected.exists() else "missing"
        prediction_text = self.format_size(predicted) if predicted is not None else "прогноз ще рахується"

        self.selected_file_label.configure(
            text=f"{self.truncate_middle(selected.name, 34)} | {original_text} → {prediction_text}"
        )
        self.per_file_quality_label.configure(text=f"{quality}%")
        self.per_file_slider.set(quality)

    def on_quality_change(self, value, released=False):
        self.quality_label.configure(text=f"{int(value)}%")

        if released:
            self.schedule_prediction()

    def on_per_file_quality_change(self, value, released=False):
        selected = self._selected_file_for_quality

        if selected is None or selected not in self.selected_files:
            return

        quality = int(value)
        self.file_quality[selected] = quality
        self.file_prediction.pop(selected, None)
        self.per_file_quality_label.configure(text=f"{quality}%")

        if released:
            self.refresh_files()
            self.schedule_prediction()

    def apply_global_quality_to_all(self):
        if self.is_converting or not self.selected_files:
            return

        quality = int(self.slider.get())

        for path in self.selected_files:
            self.file_quality[path] = quality

        self.file_prediction.clear()

        self.status_label.configure(text=f"Якість {quality}% застосовано до всіх", fg=ACCENT)
        self.refresh_files()
        self._update_selected_file_quality_panel()
        self.schedule_prediction()

    def schedule_prediction(self):
        if self._prediction_job is not None:
            self.root.after_cancel(self._prediction_job)

        self._prediction_job = self.root.after(300, self.predict_output_size)

    def predict_output_size(self):
        self._prediction_job = None

        if not self.selected_files:
            self.file_prediction.clear()
            self.estimate_label.configure(text="")
            self.refresh_files()
            return

        files_snapshot = list(self.selected_files)
        quality_snapshot = dict(self.file_quality)
        default_quality = int(self.slider.get())

        # Рахуємо тільки файли без актуального прогнозу
        files_to_predict = [f for f in files_snapshot if f not in self.file_prediction]

        if not files_to_predict:
            self._refresh_estimate_label(files_snapshot)
            return

        self.refresh_files()

        def worker():
            try:
                predicted_by_file, _, _ = self.calculate_predictions(
                    files_to_predict,
                    quality_snapshot,
                    default_quality,
                )

                def apply_prediction():
                    self.file_prediction.update(predicted_by_file)
                    self.refresh_files()
                    self._update_selected_file_quality_panel()
                    self._refresh_estimate_label(files_snapshot)

                self.root.after(0, apply_prediction)
            except Exception:
                self.root.after(0, lambda: self.estimate_label.configure(text=""))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_estimate_label(self, files_snapshot: list[Path]):
        predicted_total = sum(
            self.file_prediction[f] for f in files_snapshot if f in self.file_prediction
        )
        original_total = sum(f.stat().st_size for f in files_snapshot if f.exists())
        self.estimate_label.configure(
            text=(
                f"Загальний прогноз: ~{self.format_size(predicted_total)}"
                f" · оригінал {self.format_size(original_total)}"
            )
        )

    @staticmethod
    def calculate_predictions(files: list[Path], qualities: dict[Path, int], default_quality: int):
        original_total = 0
        predicted_total = 0
        predicted_by_file: dict[Path, int] = {}

        def predict_one(path: Path):
            if not path.exists():
                return path, None
            quality = int(qualities.get(path, default_quality))
            with Image.open(path) as image:
                buffer = BytesIO()
                image.save(buffer, "webp", quality=quality)
            return path, buffer.tell()

        # Паралельний розрахунок — PIL відпускає GIL під час WebP-кодування
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(predict_one, f): f for f in files}
            for future in as_completed(futures):
                path, predicted = future.result()
                if predicted is None:
                    continue
                predicted_by_file[path] = predicted
                predicted_total += predicted
                original_total += path.stat().st_size

        return predicted_by_file, predicted_total, original_total

    def start_conversion(self):
        if self.is_converting or not self.selected_files:
            return

        self.is_converting = True
        self._update_buttons()

        files = list(self.selected_files)
        qualities = dict(self.file_quality)
        output_dir = self.output_dir
        default_quality = int(self.slider.get())
        open_after = self.open_var.get() == 1

        self.progress["value"] = 0
        self.analytics_label.configure(text="")
        self.current_label.configure(text="")
        self.status_label.configure(text="Конвертація...", fg=ACCENT)

        threading.Thread(
            target=self.convert_files,
            args=(files, qualities, output_dir, default_quality, open_after),
            daemon=True,
        ).start()

    def convert_files(self, files, qualities, output_dir, default_quality, open_after):
        results = []
        last_folder = None
        total = len(files)

        for index, source in enumerate(files, start=1):
            quality = int(qualities.get(source, default_quality))
            short_name = source.name if len(source.name) <= 30 else source.name[:27] + "..."

            self.root.after(
                0,
                lambda name=short_name: self.current_label.configure(
                    text=f"Обробка: {name}",
                    fg=MUTED,
                ),
            )

            result = self.convert_one_file(source, output_dir, quality)
            results.append(result)

            if result["output"]:
                last_folder = result["output"].parent

            progress = index / total * 100
            self.root.after(0, lambda p=progress: self.progress.configure(value=p))

            time.sleep(0.03)

        self.root.after(0, lambda: self.finish_conversion(results, last_folder, open_after))

    def convert_one_file(self, source: Path, output_dir: Path | None, quality: int):
        try:
            if not source.exists():
                return {
                    "success": False,
                    "source": source,
                    "output": None,
                    "old": 0,
                    "new": 0,
                    "quality": quality,
                    "error": "Файл не знайдено",
                }

            old_size = source.stat().st_size
            folder = output_dir if output_dir else source.parent
            folder.mkdir(parents=True, exist_ok=True)

            output = self.unique_output_path(folder, source.stem, ".webp")

            with Image.open(source) as image:
                image.save(output, "webp", quality=quality)

            new_size = output.stat().st_size

            return {
                "success": True,
                "source": source,
                "output": output,
                "old": old_size,
                "new": new_size,
                "quality": quality,
                "error": None,
            }

        except Exception as exc:
            old_size = source.stat().st_size if source.exists() else 0

            return {
                "success": False,
                "source": source,
                "output": None,
                "old": old_size,
                "new": 0,
                "quality": quality,
                "error": str(exc),
            }

    def finish_conversion(self, results, last_folder, open_after):
        success = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]

        old_total = sum(r["old"] for r in results)
        new_total = sum(r["new"] for r in success)
        saved = ((old_total - new_total) / old_total * 100) if old_total > 0 else 0

        if success and not failed:
            self.analytics_label.configure(
                text=(
                    f"Готово: {len(success)}/{len(results)}"
                    f" · економія {saved:.1f}%"
                    f" · {self.format_size(old_total)} → {self.format_size(new_total)}"
                ),
                fg=SUCCESS,
            )
            self.status_label.configure(text="Успішно завершено", fg=SUCCESS)
            self.current_label.configure(text="")
        elif success:
            self.analytics_label.configure(
                text=(
                    f"Частково: {len(success)}/{len(results)}"
                    f" · помилок {len(failed)}"
                    f" · {self.format_size(old_total)} → {self.format_size(new_total)}"
                ),
                fg=WARNING,
            )
            self.status_label.configure(text="Завершено з помилками", fg=WARNING)
            self.current_label.configure(
                text=f"Перша помилка: {failed[0]['source'].name} — {failed[0]['error']}",
                fg=ERROR,
            )
        else:
            self.analytics_label.configure(text="Не вдалося конвертувати файли", fg=ERROR)
            self.status_label.configure(text="Помилка", fg=ERROR)
            if failed:
                self.current_label.configure(
                    text=f"Перша помилка: {failed[0]['source'].name} — {failed[0]['error']}",
                    fg=ERROR,
                )

        self.selected_files.clear()
        self.file_quality.clear()
        self.file_prediction.clear()
        self._selected_file_for_quality = None
        self.is_converting = False
        self.refresh_files()
        self._update_selected_file_quality_panel()

        self.drop_zone.configure(bg="#eff6ff", highlightbackground="#bfdbfe")
        self.drop_icon.configure(bg="#eff6ff")
        self.drop_label.configure(bg="#eff6ff", text="Перетягни PNG сюди", fg=ACCENT)
        self.drop_sub.configure(bg="#eff6ff")

        if open_after and last_folder:
            self.open_folder(last_folder)

    @staticmethod
    def unique_output_path(folder: Path, stem: str, suffix: str) -> Path:
        candidate = folder / f"{stem}{suffix}"

        if not candidate.exists():
            return candidate

        counter = 1
        while True:
            candidate = folder / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def format_size(size):
        size = float(size)

        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024

        return f"{size:.1f} PB"

    @staticmethod
    def truncate_middle(text: str, max_len=32):
        if len(text) <= max_len:
            return text
        keep = max_len - 3
        left = keep // 2
        right = keep - left
        return text[:left] + "..." + text[-right:]

    @staticmethod
    def shorten_path(path: Path, max_len=32):
        text = str(path)
        if len(text) <= max_len:
            return text
        return "..." + text[-(max_len - 3):]

    @staticmethod
    def open_folder(folder: Path):
        try:
            system = platform.system()

            if system == "Windows":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            print(f"Open folder error: {exc}")


if __name__ == "__main__":
    root = tk.Tk()
    app = WebPConverterApp(root)
    root.mainloop()
