from __future__ import annotations

import os
import platform
import queue
import subprocess
import threading
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


APP_TITLE = "PNG to WebP Converter"
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

# Максимальний лічильник для unique_output_path
MAX_UNIQUE_COUNTER = 9999


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
    def __init__(self, parent, from_=10, to=100, value=80, command=None, width=200, height=26, bg=CARD):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=bg,
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
        self.itemconfigure(
            "thumb",
            fill="#ffffff" if enabled else "#f3f4f6",
            outline="#bfdbfe" if enabled else "#d1d5db",
        )

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


class MultiColorListbox(tk.Frame):
    """Canvas-based listbox that renders each row with multiple colors per segment."""

    ROW_HEIGHT = 22
    FONT = ("Segoe UI", 9)
    FONT_BOLD = ("Segoe UI", 9, "bold")
    PAD_X = 8
    PAD_Y = 4

    def __init__(self, parent, bg="#f9fafb", select_bg="#dbeafe", **kwargs):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)

        self._bg = bg
        self._select_bg = select_bg
        self._rows: list[tuple] = []   # (index_text, name_text, size_text, arrow, pred_text, state)
        self._selected: int | None = None
        self._on_select_cb = None
        self._scroll_offset = 0        # pixels scrolled from top

        self._canvas = tk.Canvas(
            self,
            bg=bg,
            bd=0,
            highlightthickness=0,
        )
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._on_scroll_cmd)
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", expand=True, fill="both")
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel)
        self._canvas.bind("<Button-5>", self._on_mousewheel)

    # ── public API (mirrors Listbox where needed) ──────────────────────────

    def bind(self, sequence=None, func=None, add=None):
        if sequence == "<<ListboxSelect>>":
            self._on_select_cb = func
        else:
            super().bind(sequence, func, add)

    def delete(self, _start, _end=None):
        self._rows.clear()
        self._selected = None
        self._redraw()

    def insert(self, _index, row_tuple: tuple):
        """row_tuple: (index_str, name_str, size_str, arrow_str, pred_str, state)
        state: 'pending' | 'done'
        """
        self._rows.append(row_tuple)
        self._redraw()

    def selection_clear(self, _first, _last=None):
        self._selected = None
        self._redraw()

    def selection_set(self, index: int):
        self._selected = index
        self._redraw()

    def activate(self, index: int):
        self._ensure_visible(index)

    def curselection(self) -> tuple:
        return (self._selected,) if self._selected is not None else ()

    def yview(self) -> tuple[float, float]:
        total = self._total_height()
        if total == 0:
            return (0.0, 1.0)
        canvas_h = self._canvas.winfo_height()
        top = self._scroll_offset / total
        bottom = min(1.0, (self._scroll_offset + canvas_h) / total)
        return (top, bottom)

    def yview_moveto(self, fraction: float):
        total = self._total_height()
        canvas_h = self._canvas.winfo_height()
        max_offset = max(0, total - canvas_h)
        self._scroll_offset = int(fraction * total)
        self._scroll_offset = max(0, min(self._scroll_offset, max_offset))
        self._redraw()

    def configure(self, yscrollcommand=None, **kwargs):
        pass  # handled internally

    # ── internals ──────────────────────────────────────────────────────────

    def _total_height(self) -> int:
        return len(self._rows) * self.ROW_HEIGHT + self.PAD_Y * 2

    def _on_scroll_cmd(self, *args):
        if args[0] == "moveto":
            self.yview_moveto(float(args[1]))
        elif args[0] == "scroll":
            delta = int(args[1])
            unit = args[2]
            step = self.ROW_HEIGHT if unit == "units" else self._canvas.winfo_height()
            canvas_h = self._canvas.winfo_height()
            total = self._total_height()
            max_offset = max(0, total - canvas_h)
            self._scroll_offset = max(0, min(self._scroll_offset + delta * step, max_offset))
            self._redraw()

    def _on_mousewheel(self, event):
        if event.num == 4:
            delta = -3
        elif event.num == 5:
            delta = 3
        else:
            delta = -1 if event.delta > 0 else 1
        canvas_h = self._canvas.winfo_height()
        total = self._total_height()
        max_offset = max(0, total - canvas_h)
        self._scroll_offset = max(0, min(self._scroll_offset + delta * self.ROW_HEIGHT, max_offset))
        self._redraw()

    def _on_resize(self, _event):
        self._redraw()

    def _on_click(self, event):
        y = event.y + self._scroll_offset - self.PAD_Y
        index = y // self.ROW_HEIGHT
        if 0 <= index < len(self._rows):
            self._selected = index
            self._redraw()
            if self._on_select_cb:
                self._on_select_cb(None)

    def _ensure_visible(self, index: int):
        canvas_h = self._canvas.winfo_height()
        row_top = self.PAD_Y + index * self.ROW_HEIGHT
        row_bot = row_top + self.ROW_HEIGHT
        total = self._total_height()
        max_offset = max(0, total - canvas_h)
        if row_top < self._scroll_offset:
            self._scroll_offset = max(0, row_top)
        elif row_bot > self._scroll_offset + canvas_h:
            self._scroll_offset = min(max_offset, row_bot - canvas_h)
        self._redraw()

    def _update_scrollbar(self):
        top, bottom = self.yview()
        self._scrollbar.set(top, bottom)

    def _redraw(self):
        c = self._canvas
        c.delete("all")
        canvas_w = c.winfo_width()
        if canvas_w <= 1:
            return

        y0 = self.PAD_Y - self._scroll_offset

        for i, row in enumerate(self._rows):
            index_str, name_str, size_str, arrow_str, pred_str, state = row
            row_y = y0 + i * self.ROW_HEIGHT
            row_bot = row_y + self.ROW_HEIGHT

            # Skip rows fully outside viewport
            canvas_h = c.winfo_height()
            if row_bot < 0 or row_y > canvas_h:
                continue

            # Selection highlight
            if i == self._selected:
                c.create_rectangle(
                    0, row_y, canvas_w, row_bot,
                    fill=self._select_bg, outline="",
                )

            text_y = row_y + self.ROW_HEIGHT // 2
            x = self.PAD_X

            # ① index — muted
            x = self._draw_text(c, x, text_y, index_str + " ", MUTED, bold=False)
            # ② filename — black/TEXT
            x = self._draw_text(c, x, text_y, name_str, TEXT, bold=True)
            # ③ separator + original size — black/TEXT
            x = self._draw_text(c, x, text_y, " · " + size_str, TEXT, bold=False)
            # ④ arrow — muted
            x = self._draw_text(c, x, text_y, " " + arrow_str + " ", MUTED, bold=False)
            # ⑤ predicted size — yellow (pending) or green (done)
            pred_color = WARNING if state == "pending" else SUCCESS
            self._draw_text(c, x, text_y, pred_str, pred_color, bold=(state == "done"))

        self._update_scrollbar()

    def _draw_text(self, canvas, x: int, y: int, text: str, color: str, bold: bool) -> int:
        font = self.FONT_BOLD if bold else self.FONT
        tid = canvas.create_text(x, y, text=text, fill=color, font=font, anchor="w")
        bbox = canvas.bbox(tid)
        return bbox[2] if bbox else x


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
        self.drop_queue: queue.Queue = queue.Queue()
        self.is_converting = False
        self._prediction_job = None
        self._selected_file_for_quality: Path | None = None

        # FIX #1: Lock для захисту спільних структур даних від race condition
        self._data_lock = threading.Lock()

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

        self.files_list = MultiColorListbox(
            list_wrap,
            bg="#f9fafb",
            select_bg="#dbeafe",
        )
        self.files_list.pack(expand=True, fill="both", padx=4, pady=4)
        self.files_list.bind("<<ListboxSelect>>", self.on_file_select)

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

        self.per_file_slider = ModernSlider(per_file_box, command=self.on_per_file_quality_change, width=232, height=26, bg="#fff7ed")
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
            text="Поруч з оригіналом",
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
        skipped = 0

        for item in files:
            # FIX #4: Логуємо файли, які не вдалося декодувати
            decoded = None
            for encoding in ("utf-8", "cp1251"):
                try:
                    decoded = item.decode(encoding)
                    break
                except (UnicodeDecodeError, AttributeError):
                    continue

            if decoded is not None:
                paths.append(decoded)
            else:
                skipped += 1

        if skipped:
            self.root.after(
                0,
                lambda n=skipped: self.status_label.configure(
                    text=f"Пропущено {n} файл(ів) з нечитабельним ім'ям",
                    fg=WARNING,
                ),
            )

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
        # FIX #1: Захищаємо читання/запис спільних структур
        with self._data_lock:
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
            self.drop_zone.configure(bg="#f0fdf4", highlightbackground="#bbf7d0")
            self.drop_icon.configure(bg="#f0fdf4")
            self.drop_label.configure(bg="#f0fdf4", text=f"Додано: {len(self.selected_files)} PNG", fg=SUCCESS)
            self.drop_sub.configure(bg="#f0fdf4")
        else:
            self.status_label.configure(text="PNG не знайдено", fg=ERROR)

        self.refresh_files(preserve_scroll=False)
        self.schedule_prediction()

    def clear_files(self):
        if self.is_converting:
            return

        # FIX #1: Захищаємо очищення спільних структур
        with self._data_lock:
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

        # FIX #1: Читаємо спільні структури під локом
        with self._data_lock:
            files_snapshot = list(self.selected_files)
            prediction_snapshot = dict(self.file_prediction)

        if self._selected_file_for_quality in files_snapshot:
            try:
                selected_index = files_snapshot.index(self._selected_file_for_quality)
            except ValueError:
                selected_index = None

        self.count_label.configure(text=f"{len(files_snapshot)} PNG")
        self.files_list.delete(0, tk.END)

        for index, path in enumerate(files_snapshot, start=1):
            original_size = self.format_size(path.stat().st_size) if path.exists() else "missing"
            predicted_size = prediction_snapshot.get(path)

            if predicted_size is None:
                self.files_list.insert(
                    tk.END,
                    (f"{index}.", self.truncate_middle(path.name, 32), original_size, "→", "прогноз...", "pending"),
                )
            else:
                self.files_list.insert(
                    tk.END,
                    (f"{index}.", self.truncate_middle(path.name, 32), original_size, "→", self.format_size(predicted_size), "done"),
                )

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
        self.btn_folder.set_enabled(not converting)
        self.btn_apply_global.set_enabled(has_files and not converting)
        self.per_file_slider.set_enabled(has_files and not converting and has_selection)

    def on_file_select(self, _event=None):
        selection = self.files_list.curselection()

        if not selection:
            self._selected_file_for_quality = None
        else:
            index = selection[0]
            with self._data_lock:
                files_snapshot = list(self.selected_files)
            if 0 <= index < len(files_snapshot):
                self._selected_file_for_quality = files_snapshot[index]

        self._update_selected_file_quality_panel()
        self._update_buttons()

    def _update_selected_file_quality_panel(self):
        selected = self._selected_file_for_quality

        with self._data_lock:
            in_list = selected in self.selected_files
            quality = self.file_quality.get(selected, int(self.slider.get())) if selected else None
            predicted = self.file_prediction.get(selected) if selected else None

        if selected is None or not in_list:
            self.selected_file_label.configure(text="Файл не вибрано")
            self.per_file_quality_label.configure(text="—")
            return

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

    # FIX #3: refresh_files і pop з prediction переміщені виключно у гілку released
    def on_per_file_quality_change(self, value, released=False):
        selected = self._selected_file_for_quality

        with self._data_lock:
            in_list = selected is not None and selected in self.selected_files

        if not in_list:
            return

        quality = int(value)
        self.per_file_quality_label.configure(text=f"{quality}%")

        if released:
            with self._data_lock:
                self.file_quality[selected] = quality
                self.file_prediction.pop(selected, None)
            self.refresh_files()
            self.schedule_prediction()

    def apply_global_quality_to_all(self):
        if self.is_converting or not self.selected_files:
            return

        quality = int(self.slider.get())

        with self._data_lock:
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

        with self._data_lock:
            files_snapshot = list(self.selected_files)
            quality_snapshot = dict(self.file_quality)
            already_predicted = set(self.file_prediction.keys())

        if not files_snapshot:
            with self._data_lock:
                self.file_prediction.clear()
            self.estimate_label.configure(text="")
            self.refresh_files()
            return

        default_quality = int(self.slider.get())

        # FIX #7: files_to_predict обчислюється коректно зі знімка
        files_to_predict = [f for f in files_snapshot if f not in already_predicted]

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
                    with self._data_lock:
                        self.file_prediction.update(predicted_by_file)
                        current_files = list(self.selected_files)
                    self.refresh_files()
                    self._update_selected_file_quality_panel()
                    self._refresh_estimate_label(current_files)

                self.root.after(0, apply_prediction)
            except Exception:
                self.root.after(0, lambda: self.estimate_label.configure(text=""))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_estimate_label(self, files_snapshot: list[Path]):
        # FIX #7: Читаємо prediction під локом для консистентності
        with self._data_lock:
            prediction_snapshot = dict(self.file_prediction)

        predicted_total = sum(
            prediction_snapshot[f] for f in files_snapshot if f in prediction_snapshot
        )
        original_total = sum(f.stat().st_size for f in files_snapshot if f.exists())
        self.estimate_label.configure(
            text=(
                f"Загальний прогноз: ~{self.format_size(predicted_total)}"
                f" · оригінал {self.format_size(original_total)}"
            )
        )

    @staticmethod
    def calculate_predictions(
        files: list[Path],
        qualities: dict[Path, int],
        default_quality: int,
    ) -> tuple[dict[Path, int], int, int]:
        # FIX #7: Повертаємо реальні значення original_total і predicted_total
        original_total = 0
        predicted_total = 0
        predicted_by_file: dict[Path, int] = {}

        def predict_one(path: Path) -> tuple[Path, int | None]:
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

        with self._data_lock:
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

    # FIX #2: Конвертація розпаралелена через ThreadPoolExecutor, прибраний sleep
    def convert_files(
        self,
        files: list[Path],
        qualities: dict[Path, int],
        output_dir: Path | None,
        default_quality: int,
        open_after: bool,
    ):
        total = len(files)
        results: list[dict | None] = [None] * total
        completed = 0
        lock = threading.Lock()
        last_folder: Path | None = None

        def convert_one(index: int, source: Path):
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

            nonlocal completed, last_folder
            with lock:
                results[index] = result
                completed += 1
                if result.get("output"):
                    last_folder = result["output"].parent
                progress = completed / total * 100

            self.root.after(0, lambda p=progress: self.progress.configure(value=p))
            return result

        with ThreadPoolExecutor(max_workers=min(4, total)) as executor:
            futures = {
                executor.submit(convert_one, i, source): i
                for i, source in enumerate(files)
            }
            for future in as_completed(futures):
                future.result()  # Викидає виняток, якщо convert_one впав

        self.root.after(0, lambda: self.finish_conversion(results, last_folder, open_after))

    def convert_one_file(self, source: Path, output_dir: Path | None, quality: int) -> dict:
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

    # FIX #5: Після конвертації зберігаємо файли з помилками у списку
    def finish_conversion(self, results: list[dict], last_folder: Path | None, open_after: bool):
        success = [r for r in results if r.get("success")]
        failed = [r for r in results if not r.get("success")]

        old_total = sum(r.get("old", 0) for r in results)
        new_total = sum(r.get("new", 0) for r in success)
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

            # Очищаємо список лише якщо всі файли конвертовано успішно
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_quality.pop(src, None)
                    self.file_prediction.pop(src, None)

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

            # FIX #5: Видаляємо лише успішно конвертовані; невдалі лишаємо для повтору
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_quality.pop(src, None)
                    self.file_prediction.pop(src, None)

        else:
            self.analytics_label.configure(text="Не вдалося конвертувати файли", fg=ERROR)
            self.status_label.configure(text="Помилка", fg=ERROR)
            if failed:
                self.current_label.configure(
                    text=f"Перша помилка: {failed[0]['source'].name} — {failed[0]['error']}",
                    fg=ERROR,
                )
            # Файли лишаються у списку — можна спробувати знову

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

    # FIX #6: Обмежуємо лічильник, щоб уникнути нескінченного циклу
    @staticmethod
    def unique_output_path(folder: Path, stem: str, suffix: str) -> Path:
        candidate = folder / f"{stem}{suffix}"

        if not candidate.exists():
            return candidate

        for counter in range(1, MAX_UNIQUE_COUNTER + 1):
            candidate = folder / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate

        raise FileExistsError(
            f"Не вдалося знайти унікальне ім'я для '{stem}{suffix}' "
            f"після {MAX_UNIQUE_COUNTER} спроб"
        )

    @staticmethod
    def format_size(size: float) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024

        return f"{size:.1f} PB"

    @staticmethod
    def truncate_middle(text: str, max_len: int = 32) -> str:
        if len(text) <= max_len:
            return text
        keep = max_len - 3
        left = keep // 2
        right = keep - left
        return text[:left] + "..." + text[-right:]

    @staticmethod
    def shorten_path(path: Path, max_len: int = 32) -> str:
        text = str(path)
        if len(text) <= max_len:
            return text
        return "..." + text[-(max_len - 3):]

    @staticmethod
    def open_folder(folder: Path) -> None:
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
