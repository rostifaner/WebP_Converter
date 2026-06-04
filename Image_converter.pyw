from __future__ import annotations

import ctypes
import io
import os
import platform
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk

from PIL import Image, ImageTk


# ===== Cairo DLL bootstrap (Windows) =====
def _setup_cairo_dll() -> bool:
    """На Windows завантажуємо libcairo-2.dll з локальної папки cairo_dll/.
    Повертає True якщо DLL завантажено, False інакше."""
    if sys.platform != "win32":
        return True
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        script_dir = os.getcwd()
    dll_dir = os.path.join(script_dir, "cairo_dll")
    if os.path.exists(os.path.join(dll_dir, "bin")):
        dll_dir = os.path.join(dll_dir, "bin")
    cairo_dll = os.path.join(dll_dir, "libcairo-2.dll")
    if not os.path.exists(cairo_dll):
        return False
    try:
        os.add_dll_directory(dll_dir)
        os.environ["PATH"] = dll_dir + os.pathsep + os.environ["PATH"]
        cwd = os.getcwd()
        os.chdir(dll_dir)
        try:
            ctypes.CDLL(cairo_dll)
        except Exception:
            pass
        finally:
            os.chdir(cwd)
        return True
    except Exception:
        return False


CAIRO_OK = _setup_cairo_dll()


# ===== Optional dependencies =====
try:
    import cairosvg  # type: ignore
    CAIROSVG_OK = True
except Exception:
    cairosvg = None  # type: ignore
    CAIROSVG_OK = False

try:
    import windnd  # type: ignore
    WINDND_AVAILABLE = True
except ImportError:
    WINDND_AVAILABLE = False


# ===== Utilities =====
def _supported_input_extensions() -> set[str]:
    """Усі розширення, які Pillow вміє читати у цій збірці."""
    try:
        Image.init()
        return {
            ext.lower()
            for ext, fmt in Image.registered_extensions().items()
            if fmt in Image.OPEN
        }
    except Exception:
        return {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}


SUPPORTED_INPUT_EXTS = _supported_input_extensions()
INPUT_FILETYPE_PATTERN = " ".join(sorted("*" + e for e in SUPPORTED_INPUT_EXTS))
SVG_EXTS = {".svg"}


def _plural_files(n: int) -> str:
    if n % 100 in (11, 12, 13, 14):
        return "файлів"
    last = n % 10
    if last == 1:
        return "файл"
    if 2 <= last <= 4:
        return "файли"
    return "файлів"


def _fmt_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _truncate_middle(text: str, max_len: int = 32) -> str:
    if len(text) <= max_len:
        return text
    keep = max_len - 3
    left = keep // 2
    right = keep - left
    return text[:left] + "..." + text[-right:]


def _shorten_path(path: Path, max_len: int = 48) -> str:
    text = str(path)
    if len(text) <= max_len:
        return text
    return "..." + text[-(max_len - 3):]


def _open_folder(folder: Path) -> None:
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


APP_TITLE = "Image Converter"
CPU_COUNT = os.cpu_count() or 4
WORKERS = min(8, max(2, CPU_COUNT))
THUMB_SIZE = 80
MAX_OUTPUT_PIXELS = 100_000_000  # ~100MP захист для SVG-рендеру


# ===== Dark theme palette (GitHub-style) =====
BG              = "#0d1117"
CARD            = "#161b22"
CARD_ALT        = "#1c2128"
RAISED          = "#21262d"
BORDER          = "#30363d"
TEXT            = "#e6edf3"
MUTED           = "#7d8590"
DIM             = "#484f58"

ACCENT          = "#58a6ff"
ACCENT_HOVER    = "#79b8ff"
ACCENT_BG       = "#0c2d6b"
ACCENT_BG_HOVER = "#1f3a8a"
ACCENT_BORDER   = "#1f6feb"

SUCCESS         = "#3fb950"
SUCCESS_HOVER   = "#56d364"
SUCCESS_BG      = "#0c2818"
SUCCESS_BORDER  = "#238636"

WARNING         = "#d29922"
WARNING_BG      = "#2d2008"

ERROR           = "#f85149"
ERROR_HOVER     = "#ff7b72"
ERROR_BG        = "#3f0d0d"
ERROR_BORDER    = "#a40e26"

PERFILE_BG      = "#22180d"
PERFILE_BORDER  = "#894c00"
PERFILE_TEXT    = "#f0b656"

ON_FILLED       = "#0d1117"


# ============================================================
# Shared widgets
# ============================================================
class ModernButton(tk.Frame):
    def __init__(self, parent, text, command, bg=ACCENT, fg=ON_FILLED,
                 hover_bg=ACCENT_HOVER, disabled_bg=RAISED, disabled_fg=DIM,
                 padx=10, pady=5, font_size=8):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.command = command
        self.enabled = True
        self.normal_bg = bg
        self.normal_fg = fg
        self.hover_bg = hover_bg
        self.disabled_bg = disabled_bg
        self.disabled_fg = disabled_fg

        self.label = tk.Label(
            self, text=text, bg=bg, fg=fg,
            font=("Segoe UI", font_size, "bold"),
            padx=padx, pady=pady, cursor="hand2",
        )
        self.label.pack(fill="both", expand=True)

        for w in (self, self.label):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _click(self, _e):
        if self.enabled and self.command:
            self.command()

    def _enter(self, _e):
        if self.enabled:
            self.configure(bg=self.hover_bg)
            self.label.configure(bg=self.hover_bg)

    def _leave(self, _e):
        if self.enabled:
            self.configure(bg=self.normal_bg)
            self.label.configure(bg=self.normal_bg)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if enabled:
            self.configure(bg=self.normal_bg)
            self.label.configure(bg=self.normal_bg, fg=self.normal_fg, cursor="hand2")
        else:
            self.configure(bg=self.disabled_bg)
            self.label.configure(bg=self.disabled_bg, fg=self.disabled_fg, cursor="arrow")


class ModernSlider(tk.Canvas):
    """Слайдер з повною перемалівкою — без слідів і артефактів."""

    def __init__(self, parent, from_=10, to=100, value=80, command=None,
                 width=200, height=26, bg=CARD):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.min_val = from_
        self.max_val = to
        self.value = value
        self.command = command
        self.padding = 16
        self.width_value = width
        self.height_value = height
        self.enabled = True

        self.bind("<Button-1>", self._move)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _current_width(self) -> int:
        w = self.winfo_width()
        return w if w > 1 else self.width_value

    def get(self):
        return self.value

    def set(self, value, notify=False):
        self.value = max(self.min_val, min(self.max_val, float(value)))
        self._draw()
        if notify and self.command:
            self.command(self.value, released=True)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._draw()

    def _value_from_x(self, x):
        w = self._current_width()
        track_left = self.padding
        track_right = w - self.padding
        track_w = max(1, track_right - track_left)
        x = max(track_left, min(track_right, x))
        percent = (x - track_left) / track_w
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
        self.delete("all")
        w = self._current_width()
        if w <= 1:
            return
        y = self.height_value // 2
        thumb_r = 8
        track_left = self.padding
        track_right = w - self.padding
        if track_right - track_left <= 0:
            return

        self.create_line(track_left, y, track_right, y,
                         fill=BORDER if self.enabled else RAISED,
                         width=5, capstyle="round")

        if not self.enabled:
            return

        thumb_min_x = track_left + thumb_r
        thumb_max_x = track_right - thumb_r
        if self.max_val == self.min_val:
            percent = 0.0
        else:
            percent = (self.value - self.min_val) / (self.max_val - self.min_val)
        thumb_x = thumb_min_x + percent * max(0, thumb_max_x - thumb_min_x)

        if thumb_x > track_left:
            self.create_rectangle(track_left, y - 2, thumb_x, y + 3,
                                  fill=ACCENT, outline="")

        self.create_oval(thumb_x - thumb_r, y - thumb_r,
                         thumb_x + thumb_r, y + thumb_r,
                         fill=TEXT, outline=ACCENT_BORDER, width=2)


class MethodSelector(tk.Frame):
    """Сегментований контрол 0..6."""
    def __init__(self, parent, value=4, command=None, bg=CARD):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.command = command
        self.value = value
        self.enabled = True
        self._cells: dict[int, tk.Label] = {}
        for v in range(0, 7):
            cell = tk.Label(self, text=str(v), width=2,
                            font=("Segoe UI", 8, "bold"),
                            bd=0, padx=2, pady=3, cursor="hand2")
            cell.pack(side="left", padx=1)
            cell.bind("<Button-1>", lambda _e, val=v: self._select(val))
            self._cells[v] = cell
        self._render()

    def get(self) -> int:
        return self.value

    def set(self, value: int):
        if value in self._cells:
            self.value = value
            self._render()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._render()

    def _select(self, value: int):
        if not self.enabled or value == self.value:
            return
        self.value = value
        self._render()
        if self.command:
            self.command(value)

    def _render(self):
        for v, cell in self._cells.items():
            if not self.enabled:
                cell.configure(bg=RAISED, fg=DIM, cursor="arrow")
            elif v == self.value:
                cell.configure(bg=ACCENT, fg=ON_FILLED, cursor="hand2")
            else:
                cell.configure(bg=ACCENT_BG, fg=ACCENT_HOVER, cursor="hand2")


class MultiColorListbox(tk.Frame):
    """Canvas-listbox: кожен рядок з кількома кольоровими сегментами."""
    ROW_HEIGHT = 18
    FONT = ("Segoe UI", 8)
    FONT_BOLD = ("Segoe UI", 8, "bold")
    PAD_X = 8
    PAD_Y = 4

    def __init__(self, parent, bg=CARD, select_bg=ACCENT_BG, on_delete=None, **kwargs):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self._bg = bg
        self._select_bg = select_bg
        self._on_delete_cb = on_delete
        self._rows: list[tuple] = []
        self._selected: int | None = None
        self._on_select_cb = None
        self._scroll_offset = 0
        self._hover_delete_idx: int | None = None

        self._canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._on_scroll_cmd)
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", expand=True, fill="both")
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", self._on_canvas_leave)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel)
        self._canvas.bind("<Button-5>", self._on_mousewheel)

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
        canvas_h = self._canvas.winfo_height()
        if total <= 0 or total <= canvas_h:
            return (0.0, 1.0)
        top = self._scroll_offset / total
        bottom = min(1.0, (self._scroll_offset + canvas_h) / total)
        return (top, bottom)

    def yview_moveto(self, fraction: float):
        total = self._total_height()
        canvas_h = self._canvas.winfo_height()
        max_offset = max(0, total - canvas_h)
        if max_offset <= 0:
            self._scroll_offset = 0
        else:
            self._scroll_offset = int(float(fraction) * total)
            self._scroll_offset = max(0, min(self._scroll_offset, max_offset))
        self._redraw()

    def configure(self, yscrollcommand=None, **kwargs):
        if yscrollcommand is not None:
            self._canvas.configure(yscrollcommand=yscrollcommand)
        if kwargs:
            super().configure(**kwargs)
    config = configure

    def _total_height(self) -> int:
        return len(self._rows) * self.ROW_HEIGHT + self.PAD_Y * 2

    def _on_scroll_cmd(self, *args):
        if args[0] == "moveto":
            self.yview_moveto(float(args[1]))
        elif args[0] == "scroll":
            try:
                delta = int(float(args[1]))
            except (ValueError, IndexError):
                return
            unit = args[2] if len(args) > 2 else "units"
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
        canvas_w = self._canvas.winfo_width()
        y = event.y + self._scroll_offset - self.PAD_Y
        index = y // self.ROW_HEIGHT
        if 0 <= index < len(self._rows):
            # Якщо клік у зоні ✕ справа — видалити
            if self._on_delete_cb is not None and event.x >= canvas_w - 28:
                self._on_delete_cb(int(index))
                return
            self._selected = index
            self._redraw()
            if self._on_select_cb:
                self._on_select_cb(None)

    def _on_motion(self, event):
        if self._on_delete_cb is None:
            return
        canvas_w = self._canvas.winfo_width()
        y = event.y + self._scroll_offset - self.PAD_Y
        index = y // self.ROW_HEIGHT
        in_delete = (
            0 <= index < len(self._rows)
            and event.x >= canvas_w - 28
        )
        new_hover = int(index) if in_delete else None
        if new_hover != self._hover_delete_idx:
            self._hover_delete_idx = new_hover
            self._canvas.configure(cursor="hand2" if new_hover is not None else "")
            self._redraw()

    def _on_canvas_leave(self, _event):
        if self._hover_delete_idx is not None:
            self._hover_delete_idx = None
            self._canvas.configure(cursor="")
            self._redraw()

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
        canvas_h = c.winfo_height()
        for i, row in enumerate(self._rows):
            index_str, name_str, size_str, arrow_str, pred_str, state = row
            row_y = y0 + i * self.ROW_HEIGHT
            row_bot = row_y + self.ROW_HEIGHT
            if row_bot < 0 or row_y > canvas_h:
                continue
            if i == self._selected:
                c.create_rectangle(0, row_y, canvas_w, row_bot,
                                   fill=self._select_bg, outline="")
            text_y = row_y + self.ROW_HEIGHT // 2
            x = self.PAD_X
            x = self._draw_text(c, x, text_y, index_str + " ", MUTED, bold=False)
            x = self._draw_text(c, x, text_y, name_str, TEXT, bold=True)
            if size_str:
                x = self._draw_text(c, x, text_y, " · " + size_str, MUTED, bold=False)
            if arrow_str:
                x = self._draw_text(c, x, text_y, " " + arrow_str + " ", DIM, bold=False)
            elif pred_str:
                x = self._draw_text(c, x, text_y, " · ", DIM, bold=False)
            pred_color = WARNING if state == "pending" else SUCCESS
            if state == "error":
                pred_color = ERROR
            self._draw_text(c, x, text_y, pred_str, pred_color, bold=(state == "done"))
            # ✕ для видалення справа
            if self._on_delete_cb is not None:
                del_color = ERROR_HOVER if i == self._hover_delete_idx else MUTED
                c.create_text(canvas_w - 14, text_y, text="✕",
                              fill=del_color, font=self.FONT_BOLD, anchor="center")
        self._update_scrollbar()

    def _draw_text(self, canvas, x: int, y: int, text: str, color: str, bold: bool) -> int:
        font = self.FONT_BOLD if bold else self.FONT
        tid = canvas.create_text(x, y, text=text, fill=color, font=font, anchor="w")
        bbox = canvas.bbox(tid)
        return bbox[2] if bbox else x


class MultiCheckSelector(tk.Frame):
    """Ряд toggle-кнопок: кожна вмикається/вимикається незалежно."""
    def __init__(self, parent, options, variables, command=None, bg=CARD,
                 padx=6, pady=4, font_size=8):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.options = list(options)
        self.variables = variables  # dict {value: IntVar}
        self.command = command
        self.enabled = True
        self._cells: dict = {}
        for value in self.options:
            cell = tk.Label(self, text=str(value),
                            font=("Segoe UI", font_size, "bold"),
                            bd=0, padx=padx, pady=pady, cursor="hand2")
            cell.pack(side="left", padx=1)
            cell.bind("<Button-1>", lambda _e, v=value: self._toggle(v))
            self._cells[value] = cell
            self.variables[value].trace_add("write", lambda *_: self._render())
        self._render()

    def _toggle(self, value):
        if not self.enabled:
            return
        var = self.variables[value]
        var.set(0 if var.get() else 1)
        if self.command:
            self.command(value)

    def _render(self):
        for value, cell in self._cells.items():
            checked = self.variables[value].get() == 1
            if not self.enabled:
                cell.configure(bg=RAISED, fg=DIM, cursor="arrow")
            elif checked:
                cell.configure(bg=ACCENT, fg=ON_FILLED, cursor="hand2")
            else:
                cell.configure(bg=ACCENT_BG, fg=ACCENT_HOVER, cursor="hand2")

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._render()


class SegmentedSelector(tk.Frame):
    """Сегмент-радіо: одна опція з кількох (через StringVar)."""
    def __init__(self, parent, options, variable, command=None, bg=CARD,
                 padx=8, pady=4, font_size=8):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.options = options  # [(value, label), ...]
        self.variable = variable
        self.command = command
        self.enabled = True
        self._cells: dict = {}
        for value, label in options:
            cell = tk.Label(self, text=label,
                            font=("Segoe UI", font_size, "bold"),
                            bd=0, padx=padx, pady=pady, cursor="hand2")
            cell.pack(side="left", padx=1)
            cell.bind("<Button-1>", lambda _e, v=value: self._select(v))
            self._cells[value] = cell
        self.variable.trace_add("write", lambda *_: self._render())
        self._render()

    def _select(self, value):
        if not self.enabled or self.variable.get() == value:
            return
        self.variable.set(value)
        if self.command:
            self.command(value)

    def _render(self):
        current = self.variable.get()
        for value, cell in self._cells.items():
            if not self.enabled:
                cell.configure(bg=RAISED, fg=DIM, cursor="arrow")
            elif value == current:
                cell.configure(bg=ACCENT, fg=ON_FILLED, cursor="hand2")
            else:
                cell.configure(bg=ACCENT_BG, fg=ACCENT_HOVER, cursor="hand2")

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._render()


# ============================================================
# WebP tab
# ============================================================
class WebPTab(tk.Frame):
    """Конвертер растрових зображень → WebP."""

    def __init__(self, parent, root_window):
        super().__init__(parent, bg=BG)
        self.root = root_window  # для .after, .winfo_id, тощо

        # --- shared state ---
        self.selected_files: list[Path] = []
        self.file_quality: dict[Path, int] = {}
        self.file_prediction: dict[Path, int] = {}
        self.file_root: dict[Path, Path] = {}
        self.output_dir: Path | None = None
        self.drop_queue: queue.Queue = queue.Queue()
        self.is_converting = False
        self._prediction_job = None
        self._selected_file_for_quality: Path | None = None

        # Encoding settings.
        self.default_quality_value = 80
        self.method_value = 4
        self.lossless_var = tk.IntVar(value=0)
        self.strip_exif_var = tk.IntVar(value=1)
        self.recursive_var = tk.IntVar(value=0)
        self.overwrite_var = tk.IntVar(value=0)
        self.preserve_structure_var = tk.IntVar(value=0)
        self.open_var = tk.IntVar(value=0)
        self.suffix_var = tk.StringVar(value="")

        # Thumbnail state.
        self._current_thumbnail: ImageTk.PhotoImage | None = None
        self._thumbnail_job_id = 0
        self._last_thumb_path: Path | None = None

        self._data_lock = threading.Lock()
        self._build_ui()
        self.root.after(120, self._poll_drop_queue)

    # ---------- main UI ----------
    def _build_ui(self):
        # ===== Header =====
        header = tk.Frame(self, bg=CARD, height=40)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        title_box = tk.Frame(header, bg=CARD)
        title_box.pack(side="left", padx=10, pady=5)
        tk.Label(title_box, text="🖼", bg=CARD, fg=ACCENT,
                 font=("Segoe UI Emoji", 14)).pack(side="left", padx=(0, 6))
        tk.Label(title_box, text="IMG to WEBP", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        buttons = tk.Frame(header, bg=CARD)
        buttons.pack(side="right", padx=8, pady=6)

        self.btn_clear = ModernButton(buttons, "Очистити", self.clear_files,
                                      bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER)
        self.btn_clear.pack(side="left", padx=3)
        self.btn_convert = ModernButton(buttons, "Конвертувати", self.start_conversion,
                                        bg=SUCCESS, hover_bg=SUCCESS_HOVER)
        self.btn_convert.pack(side="left", padx=3)

        # ===== Main split =====
        main = tk.Frame(self, bg=BG)
        main.pack(expand=True, fill="both", padx=10, pady=10)

        sidebar = tk.Frame(main, bg=BG, width=300)
        sidebar.pack(side="right", fill="y", padx=(8, 0))
        sidebar.pack_propagate(False)

        content = tk.Frame(main, bg=BG)
        content.pack(side="left", expand=True, fill="both")

        # Drop zone
        self.drop_zone = tk.Frame(content, bg=CARD_ALT,
                                  highlightthickness=1, highlightbackground=ACCENT_BORDER,
                                  height=56)
        self.drop_zone.pack(fill="x", pady=(0, 8))
        self.drop_zone.pack_propagate(False)
        self.drop_zone.bind("<Button-1>", lambda _e: self.select_files())

        self.drop_icon = tk.Label(self.drop_zone, text="📥", bg=CARD_ALT, fg=ACCENT,
                                  font=("Segoe UI Emoji", 16), cursor="hand2")
        self.drop_icon.pack(side="left", padx=(16, 8), pady=8)
        self.drop_icon.bind("<Button-1>", lambda _e: self.select_files())

        self.labels_box = tk.Frame(self.drop_zone, bg=CARD_ALT)
        self.labels_box.pack(side="left", fill="y", pady=8)
        self.drop_label = tk.Label(self.labels_box, text="Перетягни зображення сюди",
                                   bg=CARD_ALT, fg=ACCENT,
                                   font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.drop_label.pack(anchor="w")
        self.drop_label.bind("<Button-1>", lambda _e: self.select_files())
        self.drop_sub = tk.Label(self.labels_box, text="або клікни для вибору",
                                 bg=CARD_ALT, fg=MUTED,
                                 font=("Segoe UI", 8), cursor="hand2")
        self.drop_sub.pack(anchor="w")
        self.drop_sub.bind("<Button-1>", lambda _e: self.select_files())

        # File list
        list_card = tk.Frame(content, bg=CARD,
                             highlightthickness=1, highlightbackground=BORDER)
        list_card.pack(expand=True, fill="both")

        list_head = tk.Frame(list_card, bg=CARD)
        list_head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(list_head, text="Файли", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.count_label = tk.Label(list_head, text=f"0 {_plural_files(0)}",
                                    bg=CARD, fg=MUTED, font=("Segoe UI", 8))
        self.count_label.pack(side="right")

        list_wrap = tk.Frame(list_card, bg=CARD_ALT,
                             highlightthickness=1, highlightbackground=BORDER)
        list_wrap.pack(expand=True, fill="both", padx=10, pady=(0, 8))
        self.files_list = MultiColorListbox(list_wrap, bg=CARD_ALT, select_bg=ACCENT_BG, on_delete=self.delete_file)
        self.files_list.pack(expand=True, fill="both", padx=2, pady=2)
        self.files_list.bind("<<ListboxSelect>>", self.on_file_select)

        # Per-file panel
        self.perfile_frame = tk.Frame(content, bg=PERFILE_BG,
                                      highlightthickness=1,
                                      highlightbackground=PERFILE_BORDER)
        self.perfile_frame.pack(fill="x", pady=(8, 0))

        thumb_container = tk.Frame(self.perfile_frame, bg=PERFILE_BG,
                                   width=THUMB_SIZE, height=THUMB_SIZE)
        thumb_container.pack(side="left", padx=8, pady=6)
        thumb_container.pack_propagate(False)
        self.thumbnail_label = tk.Label(thumb_container, text="",
                                        bg=PERFILE_BG, fg=MUTED,
                                        font=("Segoe UI", 8))
        self.thumbnail_label.pack(expand=True, fill="both")

        pf_text_box = tk.Frame(self.perfile_frame, bg=PERFILE_BG)
        pf_text_box.pack(side="left", fill="both", expand=True, padx=(2, 8), pady=6)

        self.selected_file_label = tk.Label(pf_text_box, text="Файл не вибрано",
                                            bg=PERFILE_BG, fg=PERFILE_TEXT,
                                            font=("Segoe UI", 9, "bold"),
                                            anchor="w", justify="left")
        self.selected_file_label.pack(fill="x")

        slider_row = tk.Frame(pf_text_box, bg=PERFILE_BG)
        slider_row.pack(fill="x", pady=(4, 0))
        tk.Label(slider_row, text="Якість:", bg=PERFILE_BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left")
        self.per_file_slider = ModernSlider(slider_row,
                                            command=self.on_per_file_quality_change,
                                            width=240, height=20, bg=PERFILE_BG)
        self.per_file_slider.pack(side="left", padx=6)
        self.per_file_quality_label = tk.Label(slider_row, text="—",
                                               bg=PERFILE_BG, fg=PERFILE_TEXT,
                                               font=("Segoe UI", 9, "bold"),
                                               width=4)
        self.per_file_quality_label.pack(side="left")

        # Status
        status_card = tk.Frame(content, bg=CARD,
                               highlightthickness=1, highlightbackground=BORDER)
        status_card.pack(fill="x", pady=(8, 0))
        status_inner = tk.Frame(status_card, bg=CARD)
        status_inner.pack(fill="x", padx=10, pady=6)

        self.status_label = tk.Label(status_inner, text="Готово", bg=CARD, fg=MUTED,
                                     font=("Segoe UI", 9, "bold"),
                                     anchor="w", justify="left")
        self.status_label.pack(fill="x")

        self.estimate_label = tk.Label(status_inner, text="",
                                       bg=CARD, fg=WARNING,
                                       font=("Segoe UI", 8, "bold"),
                                       anchor="w", justify="left")
        self.estimate_label.pack(fill="x", pady=(2, 0))

        self.current_label = tk.Label(status_inner, text="", bg=CARD, fg=MUTED,
                                      font=("Segoe UI", 8), anchor="w", justify="left")
        self.current_label.pack(fill="x", pady=(2, 0))

        self.progress = ttk.Progressbar(status_inner, style="Modern.Horizontal.TProgressbar",
                                        orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))

        self.analytics_label = tk.Label(status_inner, text="", bg=CARD, fg=SUCCESS,
                                        font=("Segoe UI", 9, "bold"),
                                        anchor="w", justify="left")
        self.analytics_label.pack(fill="x", pady=(4, 0))

        # Sidebar with settings
        self._build_settings(sidebar)

        self._update_buttons()
        self._update_selected_file_quality_panel()

    def _build_settings(self, parent):
        card = tk.Frame(parent, bg=CARD,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True)

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Label(inner, text="КОДУВАННЯ", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        q_head = tk.Frame(inner, bg=CARD)
        q_head.pack(fill="x", pady=(4, 0))
        tk.Label(q_head, text="Якість за замовчуванням",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(side="left")
        self.quality_value_label = tk.Label(
            q_head, text=f"{self.default_quality_value}%",
            bg=CARD, fg=ACCENT, font=("Segoe UI", 9, "bold"),
        )
        self.quality_value_label.pack(side="right")

        self.quality_slider = ModernSlider(
            inner, value=self.default_quality_value,
            command=self.on_default_quality_change,
            width=270, height=22, bg=CARD,
        )
        self.quality_slider.pack(fill="x", pady=(2, 4))

        self.btn_apply = ModernButton(
            inner, "Застосувати до всіх файлів",
            self.apply_quality_to_all,
            bg=ACCENT_BG, fg=ACCENT_HOVER, hover_bg=ACCENT_BG_HOVER, padx=8,
        )
        self.btn_apply.pack(anchor="w", pady=(0, 6))

        tk.Label(inner, text="Метод (0 — швидко, 6 — менший розмір):",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(anchor="w")
        self.method_sel = MethodSelector(inner, value=self.method_value,
                                          command=self.on_method_change, bg=CARD)
        self.method_sel.pack(anchor="w", pady=(2, 4))

        self._cb(inner, "Без втрат (lossless)", self.lossless_var,
                 self.on_lossless_change).pack(anchor="w")
        self._cb(inner, "Видалити EXIF (геолокація, модель камери)",
                 self.strip_exif_var).pack(anchor="w")

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))

        tk.Label(inner, text="ВХІД / ВИХІД", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        folder_row = tk.Frame(inner, bg=CARD)
        folder_row.pack(fill="x", pady=(4, 0))
        tk.Label(folder_row, text="Папка збереження:",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(side="left")
        ModernButton(folder_row, "Вибрати…", self.select_output_folder,
                     bg=ACCENT_BG, fg=ACCENT_HOVER, hover_bg=ACCENT_BG_HOVER,
                     padx=8).pack(side="right")
        self.btn_clear_output = ModernButton(
            folder_row, "✕", self.clear_output_folder,
            bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER, padx=6,
        )
        self.btn_clear_output.pack(side="right", padx=(0, 4))

        self.output_label = tk.Label(
            inner, text="Поруч з оригіналом",
            bg=CARD, fg=MUTED, font=("Segoe UI", 8),
            wraplength=260, anchor="w", justify="left",
        )
        self.output_label.pack(fill="x", pady=(2, 4))

        suf_row = tk.Frame(inner, bg=CARD)
        suf_row.pack(fill="x", pady=(0, 2))
        tk.Label(suf_row, text="Суфікс імені:",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(side="left")
        ModernButton(suf_row, "✕", self.clear_suffix,
                     bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER,
                     padx=6).pack(side="right")
        entry = tk.Entry(suf_row, textvariable=self.suffix_var,
                         font=("Segoe UI", 9), bd=1, relief="solid",
                         bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        tk.Label(inner, text='напр. "-min" → image-min.webp',
                 bg=CARD, fg=MUTED, font=("Segoe UI", 7)
                 ).pack(anchor="w", pady=(0, 4))

        self._cb(inner, "Включати вкладені папки",
                 self.recursive_var).pack(anchor="w")
        self._cb(inner, "Перезаписувати існуючі файли",
                 self.overwrite_var).pack(anchor="w")
        self._cb(inner, "Зберігати структуру папок",
                 self.preserve_structure_var).pack(anchor="w")
        self._cb(inner, "Відкрити папку після завершення",
                 self.open_var).pack(anchor="w")

    def _cb(self, parent, text, var, command=None):
        return tk.Checkbutton(
            parent, text=text, variable=var, command=command,
            bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=TEXT,
            selectcolor=BG, font=("Segoe UI", 8),
            bd=0, highlightthickness=0,
        )

    # ---------- settings callbacks ----------
    def on_default_quality_change(self, value, released=False):
        self.default_quality_value = int(value)
        self.quality_value_label.configure(text=f"{int(value)}%")
        if released:
            with self._data_lock:
                for p in self.selected_files:
                    if p not in self.file_quality:
                        self.file_prediction.pop(p, None)
            self._update_selected_file_quality_panel()
            self.schedule_prediction()

    def apply_quality_to_all(self):
        if self.is_converting or not self.selected_files:
            return
        q = int(self.default_quality_value)
        with self._data_lock:
            for p in self.selected_files:
                self.file_quality[p] = q
            self.file_prediction.clear()
        self.status_label.configure(text=f"Якість {q}% застосовано до всіх", fg=ACCENT)
        self.refresh_files()
        self._update_selected_file_quality_panel()
        self.schedule_prediction()

    def on_lossless_change(self):
        with self._data_lock:
            self.file_prediction.clear()
        self.schedule_prediction()

    def on_method_change(self, _v):
        self.method_value = self.method_sel.get()
        with self._data_lock:
            self.file_prediction.clear()
        self.schedule_prediction()

    # ---------- drag&drop ----------
    def queue_drop(self, paths):
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

    # ---------- file selection / state ----------
    def select_files(self):
        if self.is_converting:
            return
        files = filedialog.askopenfilenames(
            title="Вибери зображення",
            filetypes=[("Зображення", INPUT_FILETYPE_PATTERN), ("Усі файли", "*.*")],
        )
        if files:
            self.add_files(files, source="Вибрано")

    def select_output_folder(self):
        if self.is_converting:
            return
        folder = filedialog.askdirectory(title="Вибери папку збереження")
        if not folder:
            return
        self.output_dir = Path(folder).resolve()
        self.output_label.configure(text=_shorten_path(self.output_dir), fg=SUCCESS)
        self._update_buttons()

    def clear_output_folder(self):
        if self.is_converting or self.output_dir is None:
            return
        self.output_dir = None
        self.output_label.configure(text="Поруч з оригіналом", fg=MUTED)
        self._update_buttons()

    def clear_suffix(self):
        if self.is_converting:
            return
        self.suffix_var.set("")

    def add_files(self, paths, source="Додано"):
        recursive = self.recursive_var.get() == 1
        skipped_unsupported = 0

        with self._data_lock:
            existing = {p.resolve() for p in self.selected_files if p.exists()}
            added = 0

            for raw in paths:
                path = Path(raw).expanduser()
                root_for_this = path if path.is_dir() else path.parent

                if path.is_dir():
                    try:
                        iterator = path.rglob("*") if recursive else path.iterdir()
                        candidates = [
                            p for p in iterator
                            if p.is_file() and p.suffix.lower() in SUPPORTED_INPUT_EXTS
                        ]
                    except Exception:
                        candidates = []
                else:
                    candidates = [path]

                for candidate in candidates:
                    if candidate.suffix.lower() not in SUPPORTED_INPUT_EXTS:
                        skipped_unsupported += 1
                        continue
                    try:
                        normalized = candidate.resolve()
                    except Exception:
                        continue
                    if not normalized.exists():
                        continue
                    if normalized not in existing:
                        self.selected_files.append(normalized)
                        try:
                            self.file_root[normalized] = root_for_this.resolve()
                        except Exception:
                            self.file_root[normalized] = root_for_this
                        self.file_prediction.pop(normalized, None)
                        existing.add(normalized)
                        added += 1

        if added:
            extra = f" · пропущено: {skipped_unsupported}" if skipped_unsupported else ""
            self.status_label.configure(text=f"{source}: +{added}{extra}", fg=ACCENT)
            self.drop_zone.configure(bg=SUCCESS_BG, highlightbackground=SUCCESS_BORDER)
            self.drop_icon.configure(bg=SUCCESS_BG, fg=SUCCESS)
            self.labels_box.configure(bg=SUCCESS_BG)
            n = len(self.selected_files)
            self.drop_label.configure(bg=SUCCESS_BG, fg=SUCCESS,
                                      text=f"Додано: {n} {_plural_files(n)}")
            self.drop_sub.configure(bg=SUCCESS_BG)
        else:
            msg = "Зображень не знайдено"
            if skipped_unsupported:
                msg += f" (пропущено непідтримуваних: {skipped_unsupported})"
            self.status_label.configure(text=msg, fg=ERROR)

        self.refresh_files(preserve_scroll=False)
        self.schedule_prediction()

    def clear_files(self):
        if self.is_converting:
            return
        with self._data_lock:
            self.selected_files.clear()
            self.file_quality.clear()
            self.file_prediction.clear()
            self.file_root.clear()
            self._selected_file_for_quality = None

        self._current_thumbnail = None
        self._thumbnail_job_id += 1
        self._last_thumb_path = None

        self.progress["value"] = 0
        self.current_label.configure(text="")
        self.analytics_label.configure(text="")
        self.estimate_label.configure(text="")
        self.status_label.configure(text="Готово", fg=MUTED)

        self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
        self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
        self.labels_box.configure(bg=CARD_ALT)
        self.drop_label.configure(bg=CARD_ALT, fg=ACCENT,
                                  text="Перетягни зображення сюди")
        self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)

        self.refresh_files(preserve_scroll=False)
        self._update_selected_file_quality_panel()

    def refresh_files(self, preserve_scroll=True):
        selected_index = None
        try:
            scroll_top = self.files_list.yview()[0] if preserve_scroll else 0.0
        except Exception:
            scroll_top = 0.0

        with self._data_lock:
            files_snapshot = list(self.selected_files)
            prediction_snapshot = dict(self.file_prediction)

        if self._selected_file_for_quality in files_snapshot:
            try:
                selected_index = files_snapshot.index(self._selected_file_for_quality)
            except ValueError:
                selected_index = None

        self.count_label.configure(
            text=f"{len(files_snapshot)} {_plural_files(len(files_snapshot))}"
        )
        self.files_list.delete(0, tk.END)
        for index, path in enumerate(files_snapshot, start=1):
            original_size = _fmt_size(path.stat().st_size) if path.exists() else "missing"
            predicted_size = prediction_snapshot.get(path)
            if predicted_size is None:
                self.files_list.insert(
                    tk.END,
                    (f"{index}.", _truncate_middle(path.name, 32),
                     original_size, "→", "прогноз...", "pending"),
                )
            else:
                self.files_list.insert(
                    tk.END,
                    (f"{index}.", _truncate_middle(path.name, 32),
                     original_size, "→", _fmt_size(predicted_size), "done"),
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
        has_output = self.output_dir is not None
        converting = self.is_converting
        self.btn_convert.set_enabled(has_files and not converting)
        self.btn_clear.set_enabled(has_files and not converting)
        self.btn_apply.set_enabled(has_files and not converting)
        self.quality_slider.set_enabled(not converting)
        self.method_sel.set_enabled(not converting)
        self.per_file_slider.set_enabled(has_files and not converting and has_selection)
        if hasattr(self, "btn_clear_output"):
            self.btn_clear_output.set_enabled(has_output and not converting)

    # ---------- thumbnail ----------
    def _request_thumbnail(self, path: Path | None):
        if self._last_thumb_path == path:
            return
        self._last_thumb_path = path
        self._thumbnail_job_id += 1
        job_id = self._thumbnail_job_id

        if path is None or not path.exists():
            self._current_thumbnail = None
            self.thumbnail_label.configure(image="", text="")
            return

        self.thumbnail_label.configure(image="", text="…")

        def worker():
            thumb = None
            try:
                with Image.open(path) as img:
                    if img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGBA")
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample = Image.LANCZOS
                    img.thumbnail((THUMB_SIZE, THUMB_SIZE), resample)
                    thumb = img.copy()
            except Exception:
                thumb = None

            def apply():
                if job_id != self._thumbnail_job_id:
                    return
                if thumb is None:
                    self._current_thumbnail = None
                    self.thumbnail_label.configure(image="", text="(no preview)")
                else:
                    photo = ImageTk.PhotoImage(thumb)
                    self._current_thumbnail = photo
                    self.thumbnail_label.configure(image=photo, text="")
            self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- per-file panel ----------
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

    def delete_file(self, index: int):
        if self.is_converting:
            return
        with self._data_lock:
            if not (0 <= index < len(self.selected_files)):
                return
            path = self.selected_files.pop(index)
            self.file_quality.pop(path, None)
            self.file_prediction.pop(path, None)
            self.file_root.pop(path, None)
            if self._selected_file_for_quality == path:
                self._selected_file_for_quality = None
        n = len(self.selected_files)
        self.status_label.configure(text=f"Видалено: {path.name}", fg=ACCENT)
        if n == 0:
            self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
            self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
            self.labels_box.configure(bg=CARD_ALT)
            self.drop_label.configure(bg=CARD_ALT, fg=ACCENT,
                                      text="Перетягни зображення сюди")
            self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)
        else:
            self.drop_label.configure(text=f"Додано: {n} {_plural_files(n)}")
        self.refresh_files()
        self._update_selected_file_quality_panel()
        self.schedule_prediction()

    def _update_selected_file_quality_panel(self):
        selected = self._selected_file_for_quality
        if selected is None:
            self.selected_file_label.configure(text="Файл не вибрано", fg=MUTED)
            self.per_file_quality_label.configure(text="—")
            self._request_thumbnail(None)
            return

        with self._data_lock:
            in_list = selected in self.selected_files
            quality = self.file_quality.get(selected, self.default_quality_value)
            predicted = self.file_prediction.get(selected)

        if not in_list:
            self.selected_file_label.configure(text="Файл не вибрано", fg=MUTED)
            self.per_file_quality_label.configure(text="—")
            self._request_thumbnail(None)
            return

        original_text = _fmt_size(selected.stat().st_size) if selected.exists() else "missing"
        prediction_text = _fmt_size(predicted) if predicted is not None else "..."
        self.selected_file_label.configure(
            text=f"{_truncate_middle(selected.name, 34)}   {original_text} → {prediction_text}",
            fg=PERFILE_TEXT,
        )
        self.per_file_quality_label.configure(text=f"{quality}%")
        self.per_file_slider.set(quality)
        self._request_thumbnail(selected)

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

    # ---------- prediction ----------
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

        default_quality = self.default_quality_value
        lossless = self.lossless_var.get() == 1
        method = self.method_value

        files_to_predict = [f for f in files_snapshot if f not in already_predicted]
        if not files_to_predict:
            self._refresh_estimate_label(files_snapshot)
            return

        self.refresh_files()

        def worker():
            try:
                predicted_by_file = self._calculate_predictions(
                    files_to_predict, quality_snapshot, default_quality,
                    lossless, method, WORKERS,
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
        with self._data_lock:
            prediction_snapshot = dict(self.file_prediction)
        predicted_total = sum(prediction_snapshot[f] for f in files_snapshot if f in prediction_snapshot)
        original_total = sum(f.stat().st_size for f in files_snapshot if f.exists())
        self.estimate_label.configure(
            text=(f"Загальний прогноз: ~{_fmt_size(predicted_total)}"
                  f" · оригінал {_fmt_size(original_total)}")
        )

    @staticmethod
    def _calculate_predictions(files: list[Path], qualities: dict[Path, int],
                                default_quality: int, lossless: bool = False,
                                method: int = 4, workers: int = WORKERS,
                                ) -> dict[Path, int]:
        predicted_by_file: dict[Path, int] = {}

        def predict_one(path: Path) -> tuple[Path, int | None]:
            if not path.exists():
                return path, None
            quality = int(qualities.get(path, default_quality))
            try:
                with Image.open(path) as image:
                    if image.mode not in ("RGB", "RGBA"):
                        image = image.convert("RGBA")
                    buffer = BytesIO()
                    if lossless:
                        image.save(buffer, "webp", lossless=True, method=method)
                    else:
                        image.save(buffer, "webp", quality=quality, method=method)
                return path, buffer.tell()
            except Exception:
                return path, None

        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(predict_one, f): f for f in files}
            for future in as_completed(futures):
                path, predicted = future.result()
                if predicted is None:
                    continue
                predicted_by_file[path] = predicted
        return predicted_by_file

    # ---------- conversion ----------
    def start_conversion(self):
        if self.is_converting or not self.selected_files:
            return
        self.is_converting = True
        self._update_buttons()

        with self._data_lock:
            files = list(self.selected_files)
            qualities = dict(self.file_quality)
            file_root_snapshot = dict(self.file_root)

        output_dir = self.output_dir
        default_quality = self.default_quality_value
        open_after = self.open_var.get() == 1
        lossless = self.lossless_var.get() == 1
        method = self.method_value
        strip_exif = self.strip_exif_var.get() == 1
        overwrite = self.overwrite_var.get() == 1
        preserve_structure = self.preserve_structure_var.get() == 1
        suffix = self.suffix_var.get().strip()

        self.progress["value"] = 0
        self.analytics_label.configure(text="")
        self.current_label.configure(text="")
        self.status_label.configure(text="Конвертація...", fg=ACCENT)

        threading.Thread(target=self.convert_files, args=(
            files, qualities, output_dir, default_quality, open_after,
            lossless, method, strip_exif, overwrite, preserve_structure,
            suffix, file_root_snapshot,
        ), daemon=True).start()

    def convert_files(self, files, qualities, output_dir, default_quality, open_after,
                      lossless, method, strip_exif, overwrite, preserve_structure,
                      suffix, file_root):
        total = len(files)
        results: list[dict | None] = [None] * total
        completed = 0
        lock = threading.Lock()
        last_folder: Path | None = None

        output_map: dict[Path, Path] = {}
        used: set[Path] = set(files)
        for source in files:
            if output_dir and preserve_structure and source in file_root:
                root = file_root[source]
                try:
                    rel = source.relative_to(root)
                    folder = output_dir / rel.parent
                except ValueError:
                    folder = output_dir
            else:
                folder = output_dir if output_dir else source.parent
            base_name = f"{source.stem}{suffix}.webp"
            out = folder / base_name
            n = 1
            while out in used or (not overwrite and out.exists()):
                out = folder / f"{source.stem}{suffix}_{n}.webp"
                n += 1
            used.add(out)
            output_map[source] = out

        def convert_one(index, source):
            quality = int(qualities.get(source, default_quality))
            result = self.convert_one_file(
                source, output_map[source], quality, lossless, method, strip_exif,
            )
            nonlocal completed, last_folder
            with lock:
                results[index] = result
                completed += 1
                done = completed
                if result.get("output"):
                    last_folder = result["output"].parent
                progress = done / total * 100
            short_name = source.name if len(source.name) <= 28 else source.name[:25] + "..."

            def update_ui(p=progress, d=done, name=short_name):
                self.progress.configure(value=p)
                self.current_label.configure(
                    text=f"Конвертовано {d}/{total}: {name}", fg=MUTED,
                )
            self.root.after(0, update_ui)
            return result

        with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, total))) as executor:
            futures = {executor.submit(convert_one, i, source): i
                       for i, source in enumerate(files)}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception:
                    pass

        self.root.after(0, lambda: self.finish_conversion(results, last_folder, open_after))

    def convert_one_file(self, source, output, quality, lossless, method=4, strip_exif=False):
        try:
            if not source.exists():
                return {"success": False, "source": source, "output": None,
                        "old": 0, "new": 0, "quality": quality,
                        "error": "Файл не знайдено"}

            old_size = source.stat().st_size
            output.parent.mkdir(parents=True, exist_ok=True)

            with Image.open(source) as image:
                exif_bytes = image.info.get("exif", b"") if not strip_exif else b""
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA")
                save_kwargs: dict = {"method": method}
                if lossless:
                    save_kwargs["lossless"] = True
                else:
                    save_kwargs["quality"] = quality
                if exif_bytes:
                    save_kwargs["exif"] = exif_bytes
                image.save(output, "webp", **save_kwargs)

            return {"success": True, "source": source, "output": output,
                    "old": old_size, "new": output.stat().st_size,
                    "quality": quality, "error": None}
        except Exception as exc:
            return {"success": False, "source": source, "output": None,
                    "old": source.stat().st_size if source.exists() else 0,
                    "new": 0, "quality": quality, "error": str(exc)}

    def finish_conversion(self, results, last_folder, open_after):
        success = [r for r in results if r and r.get("success")]
        failed = [r for r in results if not r or not r.get("success")]
        old_total = sum(r.get("old", 0) for r in results if r)
        new_total = sum(r.get("new", 0) for r in success)
        saved = ((old_total - new_total) / old_total * 100) if old_total > 0 else 0

        if success and not failed:
            self.analytics_label.configure(
                text=(f"Готово: {len(success)}/{len(results)} · "
                      f"економія {saved:.1f}% · "
                      f"{_fmt_size(old_total)} → {_fmt_size(new_total)}"),
                fg=SUCCESS,
            )
            self.status_label.configure(text="Успішно завершено", fg=SUCCESS)
            self.current_label.configure(text="")
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_quality.pop(src, None)
                    self.file_prediction.pop(src, None)
                    self.file_root.pop(src, None)
        elif success:
            self.analytics_label.configure(
                text=(f"Частково: {len(success)}/{len(results)} · "
                      f"помилок {len(failed)} · "
                      f"{_fmt_size(old_total)} → {_fmt_size(new_total)}"),
                fg=WARNING,
            )
            self.status_label.configure(text="Завершено з помилками", fg=WARNING)
            first_error = "Невідома помилка"
            if failed and failed[0]:
                src_name = failed[0].get("source", Path("Unknown")).name
                first_error = f"{src_name} — {failed[0].get('error')}"
            self.current_label.configure(text=f"Перша помилка: {first_error}", fg=ERROR)
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_quality.pop(src, None)
                    self.file_prediction.pop(src, None)
                    self.file_root.pop(src, None)
        else:
            self.analytics_label.configure(text="Не вдалося конвертувати файли", fg=ERROR)
            self.status_label.configure(text="Помилка", fg=ERROR)
            if failed:
                first = failed[0]
                if first:
                    src_name = first.get("source", Path("Unknown")).name
                    self.current_label.configure(
                        text=f"Перша помилка: {src_name} — {first.get('error')}", fg=ERROR,
                    )

        self._selected_file_for_quality = None
        self.is_converting = False
        self.refresh_files()
        self._update_selected_file_quality_panel()

        self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
        self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
        self.labels_box.configure(bg=CARD_ALT)
        self.drop_label.configure(bg=CARD_ALT, fg=ACCENT,
                                  text="Перетягни зображення сюди")
        self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)

        if open_after and last_folder:
            _open_folder(last_folder)


# ============================================================
# SVG → PNG tab
# ============================================================
class SvgToPngTab(tk.Frame):
    """Конвертер SVG → PNG (батч, через cairosvg)."""

    def __init__(self, parent, root_window):
        super().__init__(parent, bg=BG)
        self.root = root_window

        # --- state ---
        self.selected_files: list[Path] = []
        self.file_dims: dict[Path, tuple[int, int]] = {}        # native SVG dims
        self.file_predicted_dims: dict[Path, tuple[int, int]] = {}  # output dims
        self.file_root: dict[Path, Path] = {}
        self.file_overrides: dict[Path, dict] = {}  # per-file налаштування розмірів
        self.output_dir: Path | None = None
        self.drop_queue: queue.Queue = queue.Queue()
        self.is_converting = False
        self._selected_file: Path | None = None
        self._prediction_job = None
        self._dims_job_id = 0
        self._loading_perfile = False  # щоб trace не реагував при заповненні полів

        # Settings (sidebar — глобальні для всіх файлів)
        self.use_scale_var = tk.IntVar(value=0)
        self.keep_ratio_var = tk.IntVar(value=1)
        self.width_var = tk.StringVar(value="")
        self.height_var = tk.StringVar(value="")
        self.scale_var = tk.StringVar(value="1")
        self.recursive_var = tk.IntVar(value=0)
        self.overwrite_var = tk.IntVar(value=0)
        self.preserve_structure_var = tk.IntVar(value=0)
        self.open_var = tk.IntVar(value=0)
        self.suffix_var = tk.StringVar(value="")

        # Per-file (для виділеного файла знизу)
        self.pf_width_var = tk.StringVar(value="")
        self.pf_height_var = tk.StringVar(value="")
        self.pf_scale_var = tk.StringVar(value="1")
        self.pf_use_scale_var = tk.IntVar(value=0)
        self.pf_keep_ratio_var = tk.IntVar(value=1)

        # Preview state (для шахівки)
        self._current_preview: ImageTk.PhotoImage | None = None
        self._preview_job_id = 0
        self._last_preview_path: Path | None = None

        # Re-predict on settings text changes (sidebar)
        for v in (self.width_var, self.height_var, self.scale_var):
            v.trace_add("write", lambda *_: self._on_setting_text_change())
        # Per-file text changes
        for v in (self.pf_width_var, self.pf_height_var, self.pf_scale_var):
            v.trace_add("write", lambda *_: self._on_perfile_change())
        # Зміна суфікса → перемалювати список файлів (бо там показуємо очікуване ім'я)
        self.suffix_var.trace_add("write", lambda *_: self.refresh_files())

        self._data_lock = threading.Lock()

        # Якщо cairosvg/cairo недоступні — показуємо банер замість UI
        if not CAIROSVG_OK or not CAIRO_OK:
            self._build_error_ui()
            return

        self._build_ui()
        self.root.after(120, self._poll_drop_queue)

    def _build_error_ui(self):
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(expand=True, fill="both", padx=20, pady=20)

        card = tk.Frame(wrap, bg=ERROR_BG,
                        highlightthickness=1, highlightbackground=ERROR_BORDER)
        card.pack(expand=True, fill="both")
        inner = tk.Frame(card, bg=ERROR_BG)
        inner.pack(expand=True, padx=24, pady=24)

        tk.Label(inner, text="⚠", bg=ERROR_BG, fg=ERROR,
                 font=("Segoe UI Emoji", 32)).pack()
        tk.Label(inner, text="SVG-конвертер недоступний",
                 bg=ERROR_BG, fg=ERROR_HOVER,
                 font=("Segoe UI", 12, "bold")).pack(pady=(8, 4))

        msgs = []
        if not CAIRO_OK:
            msgs.append("libcairo-2.dll не знайдено у папці cairo_dll/")
        if not CAIROSVG_OK:
            msgs.append("Бібліотека cairosvg не встановлена (pip install cairosvg)")
        msg = "\n".join(msgs) or "Невідома помилка"
        tk.Label(inner, text=msg, bg=ERROR_BG, fg=TEXT,
                 font=("Segoe UI", 9), justify="left").pack(pady=(0, 4))

    # ---------- main UI ----------
    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg=CARD, height=40)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        title_box = tk.Frame(header, bg=CARD)
        title_box.pack(side="left", padx=10, pady=5)
        tk.Label(title_box, text="🎨", bg=CARD, fg=ACCENT,
                 font=("Segoe UI Emoji", 14)).pack(side="left", padx=(0, 6))
        tk.Label(title_box, text="SVG to PNG", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        buttons = tk.Frame(header, bg=CARD)
        buttons.pack(side="right", padx=8, pady=6)
        self.btn_clear = ModernButton(buttons, "Очистити", self.clear_files,
                                      bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER)
        self.btn_clear.pack(side="left", padx=3)
        self.btn_convert = ModernButton(buttons, "Конвертувати", self.start_conversion,
                                        bg=SUCCESS, hover_bg=SUCCESS_HOVER)
        self.btn_convert.pack(side="left", padx=3)

        # Main split
        main = tk.Frame(self, bg=BG)
        main.pack(expand=True, fill="both", padx=10, pady=10)

        sidebar = tk.Frame(main, bg=BG, width=300)
        sidebar.pack(side="right", fill="y", padx=(8, 0))
        sidebar.pack_propagate(False)

        content = tk.Frame(main, bg=BG)
        content.pack(side="left", expand=True, fill="both")

        # Drop zone
        self.drop_zone = tk.Frame(content, bg=CARD_ALT,
                                  highlightthickness=1, highlightbackground=ACCENT_BORDER,
                                  height=56)
        self.drop_zone.pack(fill="x", pady=(0, 8))
        self.drop_zone.pack_propagate(False)
        self.drop_zone.bind("<Button-1>", lambda _e: self.select_files())

        self.drop_icon = tk.Label(self.drop_zone, text="📥", bg=CARD_ALT, fg=ACCENT,
                                  font=("Segoe UI Emoji", 16), cursor="hand2")
        self.drop_icon.pack(side="left", padx=(16, 8), pady=8)
        self.drop_icon.bind("<Button-1>", lambda _e: self.select_files())

        self.labels_box = tk.Frame(self.drop_zone, bg=CARD_ALT)
        self.labels_box.pack(side="left", fill="y", pady=8)
        self.drop_label = tk.Label(self.labels_box, text="Перетягни SVG сюди",
                                   bg=CARD_ALT, fg=ACCENT,
                                   font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.drop_label.pack(anchor="w")
        self.drop_label.bind("<Button-1>", lambda _e: self.select_files())
        self.drop_sub = tk.Label(self.labels_box, text="або клікни для вибору",
                                 bg=CARD_ALT, fg=MUTED,
                                 font=("Segoe UI", 8), cursor="hand2")
        self.drop_sub.pack(anchor="w")
        self.drop_sub.bind("<Button-1>", lambda _e: self.select_files())

        # File list
        list_card = tk.Frame(content, bg=CARD,
                             highlightthickness=1, highlightbackground=BORDER)
        list_card.pack(expand=True, fill="both")

        list_head = tk.Frame(list_card, bg=CARD)
        list_head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(list_head, text="Файли", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.count_label = tk.Label(list_head, text=f"0 {_plural_files(0)}",
                                    bg=CARD, fg=MUTED, font=("Segoe UI", 8))
        self.count_label.pack(side="right")

        list_wrap = tk.Frame(list_card, bg=CARD_ALT,
                             highlightthickness=1, highlightbackground=BORDER)
        list_wrap.pack(expand=True, fill="both", padx=10, pady=(0, 8))
        self.files_list = MultiColorListbox(list_wrap, bg=CARD_ALT, select_bg=ACCENT_BG, on_delete=self.delete_file)
        self.files_list.pack(expand=True, fill="both", padx=2, pady=2)
        self.files_list.bind("<<ListboxSelect>>", self.on_file_select)

        # Прев'ю з шахівкою на весь контейнер
        self.preview_frame = tk.Frame(content, bg=PERFILE_BG,
                                      highlightthickness=1,
                                      highlightbackground=PERFILE_BORDER,
                                      height=160)
        self.preview_frame.pack(fill="x", pady=(8, 0))
        self.preview_frame.pack_propagate(False)
        self.preview_label = tk.Label(self.preview_frame, text="",
                                      bg=PERFILE_BG, fg=MUTED,
                                      font=("Segoe UI", 8))
        self.preview_label.pack(expand=True, fill="both", padx=4, pady=4)

        # Per-file налаштування розмірів для виділеного файла (на місці статус-панелі)
        self.perfile_frame = tk.Frame(content, bg=PERFILE_BG,
                                      highlightthickness=1,
                                      highlightbackground=PERFILE_BORDER)
        self.perfile_frame.pack(fill="x", pady=(8, 0))

        pf_inner = tk.Frame(self.perfile_frame, bg=PERFILE_BG)
        pf_inner.pack(fill="x", padx=10, pady=8)

        self.pf_title_label = tk.Label(
            pf_inner, text="Файл не вибрано (налаштування для конкретного файла)",
            bg=PERFILE_BG, fg=MUTED,
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left",
        )
        self.pf_title_label.pack(fill="x")

        pf_row = tk.Frame(pf_inner, bg=PERFILE_BG)
        pf_row.pack(fill="x", pady=(6, 0))

        tk.Label(pf_row, text="W:", bg=PERFILE_BG, fg=PERFILE_TEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.pf_entry_w = tk.Entry(
            pf_row, textvariable=self.pf_width_var, width=7,
            font=("Segoe UI", 9), bd=1, relief="solid",
            bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.pf_entry_w.pack(side="left", padx=(4, 10))

        tk.Label(pf_row, text="H:", bg=PERFILE_BG, fg=PERFILE_TEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.pf_entry_h = tk.Entry(
            pf_row, textvariable=self.pf_height_var, width=7,
            font=("Segoe UI", 9), bd=1, relief="solid",
            bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.pf_entry_h.pack(side="left", padx=(4, 10))

        tk.Label(pf_row, text="S:", bg=PERFILE_BG, fg=PERFILE_TEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.pf_entry_s = tk.Entry(
            pf_row, textvariable=self.pf_scale_var, width=7,
            font=("Segoe UI", 9), bd=1, relief="solid",
            bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.pf_entry_s.pack(side="left", padx=(4, 10))

        pf_chk_row = tk.Frame(pf_inner, bg=PERFILE_BG)
        pf_chk_row.pack(fill="x", pady=(4, 0))
        tk.Checkbutton(
            pf_chk_row, text="Use scale", variable=self.pf_use_scale_var,
            command=self._on_perfile_change,
            bg=PERFILE_BG, fg=PERFILE_TEXT,
            activebackground=PERFILE_BG, activeforeground=PERFILE_TEXT,
            disabledforeground=DIM,
            selectcolor=BG, font=("Segoe UI", 8),
            bd=0, highlightthickness=0,
        ).pack(side="left")
        tk.Checkbutton(
            pf_chk_row, text="Keep ratio", variable=self.pf_keep_ratio_var,
            command=self._on_perfile_change,
            bg=PERFILE_BG, fg=PERFILE_TEXT,
            activebackground=PERFILE_BG, activeforeground=PERFILE_TEXT,
            disabledforeground=DIM,
            selectcolor=BG, font=("Segoe UI", 8),
            bd=0, highlightthickness=0,
        ).pack(side="left", padx=(12, 0))

        self.btn_pf_reset = ModernButton(
            pf_chk_row, "Скинути до загальних", self._reset_perfile,
            bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER, padx=8,
        )
        self.btn_pf_reset.pack(side="right")

        # Компактний 1-line footer: прогрес + status (без великої панелі статусу)
        self.progress = ttk.Progressbar(content, style="Modern.Horizontal.TProgressbar",
                                        orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 0))

        footer = tk.Frame(content, bg=BG)
        footer.pack(fill="x", pady=(4, 0))
        self.status_label = tk.Label(footer, text="Готово", bg=BG, fg=MUTED,
                                     font=("Segoe UI", 8, "bold"))
        self.status_label.pack(side="left")
        self.current_label = tk.Label(footer, text="", bg=BG, fg=MUTED,
                                      font=("Segoe UI", 8))
        self.current_label.pack(side="left", padx=(8, 0))
        self.analytics_label = tk.Label(footer, text="", bg=BG, fg=SUCCESS,
                                        font=("Segoe UI", 8, "bold"))
        self.analytics_label.pack(side="right")

        # Sidebar
        self._build_settings(sidebar)

        self._update_buttons()
        self._update_selected_panel()

    def _build_settings(self, parent):
        card = tk.Frame(parent, bg=CARD,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Label(inner, text="РОЗМІРИ", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        self._cb(inner, "Використати масштаб (замість width/height)",
                 self.use_scale_var, self._on_mode_change).pack(anchor="w", pady=(4, 6))

        # Усі поля видимі; нерелевантні дезактивуються залежно від use_scale
        w_row = tk.Frame(inner, bg=CARD)
        w_row.pack(fill="x", pady=(0, 4))
        tk.Label(w_row, text="Width (px):", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9), width=11, anchor="w").pack(side="left")
        self.entry_w = tk.Entry(w_row, textvariable=self.width_var,
                                font=("Segoe UI", 9), bd=1, relief="solid", width=8,
                                bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
                                highlightthickness=1, highlightbackground=BORDER,
                                highlightcolor=ACCENT)
        self.entry_w.pack(side="left", padx=(6, 0))

        h_row = tk.Frame(inner, bg=CARD)
        h_row.pack(fill="x", pady=(0, 4))
        tk.Label(h_row, text="Height (px):", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9), width=11, anchor="w").pack(side="left")
        self.entry_h = tk.Entry(h_row, textvariable=self.height_var,
                                font=("Segoe UI", 9), bd=1, relief="solid", width=8,
                                bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
                                highlightthickness=1, highlightbackground=BORDER,
                                highlightcolor=ACCENT)
        self.entry_h.pack(side="left", padx=(6, 0))

        self.cb_keep_ratio = self._cb(
            inner, "Keep aspect ratio (коли задано обидва)",
            self.keep_ratio_var, lambda: self._schedule_prediction())
        self.cb_keep_ratio.pack(anchor="w", pady=(2, 4))

        s_row = tk.Frame(inner, bg=CARD)
        s_row.pack(fill="x", pady=(0, 4))
        tk.Label(s_row, text="Scale:", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9), width=11, anchor="w").pack(side="left")
        self.entry_s = tk.Entry(s_row, textvariable=self.scale_var,
                                font=("Segoe UI", 9), bd=1, relief="solid", width=8,
                                bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
                                highlightthickness=1, highlightbackground=BORDER,
                                highlightcolor=ACCENT)
        self.entry_s.pack(side="left", padx=(6, 0))
        tk.Label(inner, text="2.0 = подвійний розмір",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 7)
                 ).pack(anchor="w", pady=(0, 4))

        self._update_sidebar_entry_states()

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))

        # Output
        tk.Label(inner, text="ВХІД / ВИХІД", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        folder_row = tk.Frame(inner, bg=CARD)
        folder_row.pack(fill="x", pady=(4, 0))
        tk.Label(folder_row, text="Папка збереження:",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(side="left")
        ModernButton(folder_row, "Вибрати…", self.select_output_folder,
                     bg=ACCENT_BG, fg=ACCENT_HOVER, hover_bg=ACCENT_BG_HOVER,
                     padx=8).pack(side="right")
        self.btn_clear_output = ModernButton(
            folder_row, "✕", self.clear_output_folder,
            bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER, padx=6,
        )
        self.btn_clear_output.pack(side="right", padx=(0, 4))

        self.output_label = tk.Label(
            inner, text="Поруч з оригіналом",
            bg=CARD, fg=MUTED, font=("Segoe UI", 8),
            wraplength=260, anchor="w", justify="left",
        )
        self.output_label.pack(fill="x", pady=(2, 4))

        suf_row = tk.Frame(inner, bg=CARD)
        suf_row.pack(fill="x", pady=(0, 2))
        tk.Label(suf_row, text="Суфікс імені:",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(side="left")
        ModernButton(suf_row, "✕", self.clear_suffix,
                     bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER,
                     padx=6).pack(side="right")
        entry = tk.Entry(suf_row, textvariable=self.suffix_var,
                         font=("Segoe UI", 9), bd=1, relief="solid",
                         bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM, insertbackground=TEXT,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        tk.Label(inner, text='напр. "@2x" → icon@2x.png',
                 bg=CARD, fg=MUTED, font=("Segoe UI", 7)
                 ).pack(anchor="w", pady=(0, 4))

        self._cb(inner, "Включати вкладені папки",
                 self.recursive_var).pack(anchor="w")
        self._cb(inner, "Перезаписувати існуючі файли",
                 self.overwrite_var).pack(anchor="w")
        self._cb(inner, "Зберігати структуру папок",
                 self.preserve_structure_var).pack(anchor="w")
        self._cb(inner, "Відкрити папку після завершення",
                 self.open_var).pack(anchor="w")

    def _cb(self, parent, text, var, command=None):
        return tk.Checkbutton(
            parent, text=text, variable=var, command=command,
            bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=TEXT,
            disabledforeground=DIM,
            selectcolor=BG, font=("Segoe UI", 8),
            bd=0, highlightthickness=0,
        )

    # ---------- settings callbacks ----------
    def _on_mode_change(self):
        self._update_sidebar_entry_states()
        self._schedule_prediction()

    def _update_sidebar_entry_states(self):
        """Дезактивує W/H/Keep ratio при use_scale=1; S при use_scale=0."""
        use_scale = self.use_scale_var.get() == 1
        wh_state = "disabled" if use_scale else "normal"
        s_state = "normal" if use_scale else "disabled"
        self.entry_w.configure(state=wh_state)
        self.entry_h.configure(state=wh_state)
        self.cb_keep_ratio.configure(state=wh_state)
        self.entry_s.configure(state=s_state)

    def _update_perfile_entry_states(self):
        """Аналогічно для per-file полів."""
        if self._selected_file is None:
            return
        use_scale = self.pf_use_scale_var.get() == 1
        wh_state = "disabled" if use_scale else "normal"
        s_state = "normal" if use_scale else "disabled"
        self.pf_entry_w.configure(state=wh_state)
        self.pf_entry_h.configure(state=wh_state)
        self.pf_entry_s.configure(state=s_state)

    def _on_setting_text_change(self):
        # Debounce — текст міняється при кожному введенні символа
        self._schedule_prediction()

    # ---------- drag&drop ----------
    def queue_drop(self, paths):
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

    # ---------- file selection ----------
    def select_files(self):
        if self.is_converting:
            return
        files = filedialog.askopenfilenames(
            title="Вибери SVG",
            filetypes=[("SVG", "*.svg"), ("Усі файли", "*.*")],
        )
        if files:
            self.add_files(files, source="Вибрано")

    def select_output_folder(self):
        if self.is_converting:
            return
        folder = filedialog.askdirectory(title="Вибери папку збереження")
        if not folder:
            return
        self.output_dir = Path(folder).resolve()
        self.output_label.configure(text=_shorten_path(self.output_dir), fg=SUCCESS)
        self._update_buttons()

    def clear_output_folder(self):
        if self.is_converting or self.output_dir is None:
            return
        self.output_dir = None
        self.output_label.configure(text="Поруч з оригіналом", fg=MUTED)
        self._update_buttons()

    def clear_suffix(self):
        if self.is_converting:
            return
        self.suffix_var.set("")

    def add_files(self, paths, source="Додано"):
        recursive = self.recursive_var.get() == 1
        skipped = 0

        with self._data_lock:
            existing = {p.resolve() for p in self.selected_files if p.exists()}
            added = 0
            for raw in paths:
                path = Path(raw).expanduser()
                root_for_this = path if path.is_dir() else path.parent
                if path.is_dir():
                    try:
                        iterator = path.rglob("*") if recursive else path.iterdir()
                        candidates = [p for p in iterator
                                      if p.is_file() and p.suffix.lower() in SVG_EXTS]
                    except Exception:
                        candidates = []
                else:
                    candidates = [path]
                for c in candidates:
                    if c.suffix.lower() not in SVG_EXTS:
                        skipped += 1
                        continue
                    try:
                        n = c.resolve()
                    except Exception:
                        continue
                    if not n.exists():
                        continue
                    if n not in existing:
                        self.selected_files.append(n)
                        try:
                            self.file_root[n] = root_for_this.resolve()
                        except Exception:
                            self.file_root[n] = root_for_this
                        existing.add(n)
                        added += 1

        if added:
            extra = f" · пропущено: {skipped}" if skipped else ""
            self.status_label.configure(text=f"{source}: +{added}{extra}", fg=ACCENT)
            self.drop_zone.configure(bg=SUCCESS_BG, highlightbackground=SUCCESS_BORDER)
            self.drop_icon.configure(bg=SUCCESS_BG, fg=SUCCESS)
            self.labels_box.configure(bg=SUCCESS_BG)
            n = len(self.selected_files)
            self.drop_label.configure(bg=SUCCESS_BG, fg=SUCCESS,
                                      text=f"Додано: {n} {_plural_files(n)}")
            self.drop_sub.configure(bg=SUCCESS_BG)
        else:
            msg = "SVG не знайдено"
            if skipped:
                msg += f" (пропущено непідтримуваних: {skipped})"
            self.status_label.configure(text=msg, fg=ERROR)

        self.refresh_files(preserve_scroll=False)
        self._load_dims_for_new_files()

    def clear_files(self):
        if self.is_converting:
            return
        with self._data_lock:
            self.selected_files.clear()
            self.file_dims.clear()
            self.file_predicted_dims.clear()
            self.file_root.clear()
            self.file_overrides.clear()
            self._selected_file = None

        self.progress["value"] = 0
        self.current_label.configure(text="")
        self.analytics_label.configure(text="")
        self.status_label.configure(text="Готово", fg=MUTED)

        self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
        self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
        self.labels_box.configure(bg=CARD_ALT)
        self.drop_label.configure(bg=CARD_ALT, fg=ACCENT, text="Перетягни SVG сюди")
        self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)

        self.refresh_files(preserve_scroll=False)
        self._update_selected_panel()

    def refresh_files(self, preserve_scroll=True):
        selected_index = None
        try:
            scroll_top = self.files_list.yview()[0] if preserve_scroll else 0.0
        except Exception:
            scroll_top = 0.0

        with self._data_lock:
            files_snapshot = list(self.selected_files)
            dims_snap = dict(self.file_dims)
            pred_snap = dict(self.file_predicted_dims)

        if self._selected_file in files_snapshot:
            try:
                selected_index = files_snapshot.index(self._selected_file)
            except ValueError:
                selected_index = None

        self.count_label.configure(
            text=f"{len(files_snapshot)} {_plural_files(len(files_snapshot))}"
        )
        suffix = self.suffix_var.get().strip()
        self.files_list.delete(0, tk.END)
        for index, path in enumerate(files_snapshot, start=1):
            expected_name = f"{path.stem}{suffix}.png"
            pred = pred_snap.get(path)
            if pred is None:
                pred_text, state = "...", "pending"
            elif pred == ("error",):
                pred_text, state = "помилка", "error"
            elif isinstance(pred, tuple) and len(pred) == 2:
                pred_text, state = f"{pred[0]}×{pred[1]}", "done"
            else:
                pred_text, state = "?", "pending"
            self.files_list.insert(
                tk.END,
                (f"{index}.", _truncate_middle(expected_name, 36),
                 "", "", pred_text, state),
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
        has_output = self.output_dir is not None
        converting = self.is_converting
        self.btn_convert.set_enabled(has_files and not converting)
        self.btn_clear.set_enabled(has_files and not converting)
        if hasattr(self, "btn_clear_output"):
            self.btn_clear_output.set_enabled(has_output and not converting)

    # ---------- per-file panel ----------
    def on_file_select(self, _event=None):
        sel = self.files_list.curselection()
        if not sel:
            self._selected_file = None
        else:
            idx = sel[0]
            with self._data_lock:
                files = list(self.selected_files)
            if 0 <= idx < len(files):
                self._selected_file = files[idx]
        self._update_selected_panel()

    def _update_selected_panel(self):
        """Заповнює per-file поля + оновлює прев'ю для виділеного файла."""
        sel = self._selected_file
        self._loading_perfile = True
        try:
            if sel is None or not sel.exists():
                self.pf_title_label.configure(
                    text="Файл не вибрано (налаштування для конкретного файла)",
                    fg=MUTED)
                self.pf_width_var.set("")
                self.pf_height_var.set("")
                self.pf_scale_var.set("")
                self.pf_use_scale_var.set(0)
                self.pf_keep_ratio_var.set(1)
                self._set_perfile_enabled(False)
                self._request_preview(None)
                return

            override = self.file_overrides.get(sel)
            has_override = override is not None
            settings = override if has_override else self._read_settings()

            label = _truncate_middle(sel.name, 50)
            if has_override:
                label += "  ·  per-file"
            self.pf_title_label.configure(text=label,
                                          fg=PERFILE_TEXT if has_override else MUTED)
            self.pf_width_var.set(str(settings.get("width") or ""))
            self.pf_height_var.set(str(settings.get("height") or ""))
            scale = settings.get("scale")
            self.pf_scale_var.set(str(scale) if scale is not None else "")
            self.pf_use_scale_var.set(1 if settings.get("mode") == "scale" else 0)
            self.pf_keep_ratio_var.set(1 if settings.get("keep_ratio", True) else 0)
            # ПІСЛЯ vars — оновити стани entries (враховує поточний use_scale)
            self._set_perfile_enabled(True)
            self._request_preview(sel)
        finally:
            self._loading_perfile = False

    # ---------- preview ----------
    @staticmethod
    def _checkerboard(w, h, sz=8):
        ts = sz * 2
        tile = Image.new("RGBA", (ts, ts), (255, 255, 255, 255))
        tile.paste(Image.new("RGBA", (sz, sz), (220, 220, 225, 255)), (0, 0))
        tile.paste(Image.new("RGBA", (sz, sz), (220, 220, 225, 255)), (sz, sz))
        cols, rows = -(-w // ts), -(-h // ts)
        row_img = Image.new("RGBA", (ts * cols, ts))
        for c in range(cols):
            row_img.paste(tile, (c * ts, 0))
        full = Image.new("RGBA", (ts * cols, ts * rows))
        for r in range(rows):
            full.paste(row_img, (0, r * ts))
        return full.crop((0, 0, w, h))

    PREVIEW_MAX_W = 600
    PREVIEW_MAX_H = 150

    def _request_preview(self, path):
        if self._last_preview_path == path:
            return
        self._last_preview_path = path
        self._preview_job_id += 1
        job_id = self._preview_job_id

        if path is None or not path.exists():
            self._current_preview = None
            self.preview_label.configure(image="", text="")
            return

        self.preview_label.configure(image="", text="…")

        with self._data_lock:
            cached_native = self.file_dims.get(path)

        def worker():
            preview = None
            try:
                if cached_native:
                    ow, oh = cached_native
                else:
                    with Image.open(io.BytesIO(cairosvg.svg2png(url=str(path)))) as native_img:
                        ow, oh = native_img.size
                r = min(self.PREVIEW_MAX_W / ow, self.PREVIEW_MAX_H / oh)
                pw, ph = max(1, int(ow * r)), max(1, int(oh * r))
                with Image.open(io.BytesIO(
                    cairosvg.svg2png(url=str(path),
                                     output_width=pw,
                                     output_height=ph))) as img:
                    bg = self._checkerboard(pw, ph)
                    bg.alpha_composite(img if img.mode == "RGBA" else img.convert("RGBA"))
                    preview = bg.copy()
            except Exception:
                preview = None

            def apply():
                if job_id != self._preview_job_id:
                    return
                if preview is None:
                    self._current_preview = None
                    self.preview_label.configure(image="", text="(no preview)")
                else:
                    photo = ImageTk.PhotoImage(preview)
                    self._current_preview = photo
                    self.preview_label.configure(image=photo, text="")
            self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _set_perfile_enabled(self, enabled: bool):
        if not enabled:
            for entry in (self.pf_entry_w, self.pf_entry_h, self.pf_entry_s):
                entry.configure(state="disabled")
        else:
            self._update_perfile_entry_states()
        if hasattr(self, "btn_pf_reset"):
            self.btn_pf_reset.set_enabled(enabled and self._selected_file in self.file_overrides)

    def _on_perfile_change(self):
        """Викликається при зміні per-file полів — записує override."""
        if self._loading_perfile or self.is_converting:
            return
        sel = self._selected_file
        if sel is None or not sel.exists():
            return
        try:
            w_text = self.pf_width_var.get().strip()
            w = int(w_text) if w_text else None
            if w is not None and w <= 0:
                w = None
        except ValueError:
            w = None
        try:
            h_text = self.pf_height_var.get().strip()
            h = int(h_text) if h_text else None
            if h is not None and h <= 0:
                h = None
        except ValueError:
            h = None
        try:
            s_text = self.pf_scale_var.get().strip()
            s = float(s_text) if s_text else None
            if s is not None and s <= 0:
                s = None
        except ValueError:
            s = None

        self.file_overrides[sel] = {
            "mode": "scale" if self.pf_use_scale_var.get() else "fixed",
            "width": w, "height": h, "scale": s,
            "keep_ratio": self.pf_keep_ratio_var.get() == 1,
        }
        self.pf_title_label.configure(
            text=_truncate_middle(sel.name, 50) + "  ·  per-file",
            fg=PERFILE_TEXT)
        if hasattr(self, "btn_pf_reset"):
            self.btn_pf_reset.set_enabled(True)
        self._update_perfile_entry_states()
        self._schedule_prediction()

    def _reset_perfile(self):
        """Видалити override для виділеного файла — повернутись до глобальних."""
        sel = self._selected_file
        if sel is None or self.is_converting:
            return
        if sel in self.file_overrides:
            del self.file_overrides[sel]
        self._update_selected_panel()
        self._schedule_prediction()

    def delete_file(self, index: int):
        if self.is_converting:
            return
        with self._data_lock:
            if not (0 <= index < len(self.selected_files)):
                return
            path = self.selected_files.pop(index)
            self.file_dims.pop(path, None)
            self.file_predicted_dims.pop(path, None)
            self.file_root.pop(path, None)
            self.file_overrides.pop(path, None)
            if self._selected_file == path:
                self._selected_file = None
        n = len(self.selected_files)
        self.status_label.configure(text=f"Видалено: {path.name}", fg=ACCENT)
        if n == 0:
            self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
            self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
            self.labels_box.configure(bg=CARD_ALT)
            self.drop_label.configure(bg=CARD_ALT, fg=ACCENT, text="Перетягни SVG сюди")
            self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)
        else:
            self.drop_label.configure(text=f"Додано: {n} {_plural_files(n)}")
        self.refresh_files()
        self._update_selected_panel()
        self._schedule_prediction()

    # ---------- dims loading ----------
    def _load_dims_for_new_files(self):
        """Завантажує нативні розміри SVG у фоні (для всіх ще не завантажених)."""
        with self._data_lock:
            files = [f for f in self.selected_files if f not in self.file_dims]
        if not files:
            self._schedule_prediction()
            return

        self._dims_job_id += 1
        job_id = self._dims_job_id

        def worker():
            results: dict[Path, tuple[int, int]] = {}

            def load_one(p):
                try:
                    with Image.open(io.BytesIO(cairosvg.svg2png(url=str(p)))) as im:
                        return p, im.size
                except Exception:
                    return p, None

            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futs = {ex.submit(load_one, f): f for f in files}
                for fut in as_completed(futs):
                    p, size = fut.result()
                    if size is not None:
                        results[p] = size

            def apply():
                if job_id != self._dims_job_id:
                    return
                with self._data_lock:
                    self.file_dims.update(results)
                self.refresh_files()
                self._update_selected_panel()
                self._schedule_prediction()
            self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- prediction ----------
    def _schedule_prediction(self):
        if self._prediction_job is not None:
            try:
                self.root.after_cancel(self._prediction_job)
            except Exception:
                pass
        self._prediction_job = self.root.after(250, self._compute_predictions)

    def _compute_predictions(self):
        self._prediction_job = None
        with self._data_lock:
            files = list(self.selected_files)
            dims = dict(self.file_dims)
            overrides = dict(self.file_overrides)
        global_settings = self._read_settings()

        new_pred: dict[Path, tuple] = {}
        for f in files:
            native = dims.get(f)
            if native is None:
                continue
            settings_for_f = overrides.get(f, global_settings)
            pred = self._compute_one_dim(native, settings_for_f)
            new_pred[f] = pred if pred else ("error",)

        with self._data_lock:
            self.file_predicted_dims = new_pred
        self.refresh_files()

    def _read_settings(self) -> dict:
        """Зчитує налаштування з UI у dict для рендеру/прогнозу."""
        out: dict = {"mode": "scale" if self.use_scale_var.get() else "fixed",
                     "keep_ratio": self.keep_ratio_var.get() == 1}
        if out["mode"] == "scale":
            try:
                s = float(self.scale_var.get())
                if s <= 0:
                    raise ValueError
                out["scale"] = s
            except (ValueError, AttributeError):
                out["scale"] = None
        else:
            try:
                w_text = self.width_var.get().strip()
                out["width"] = int(w_text) if w_text else None
                if out["width"] is not None and out["width"] <= 0:
                    out["width"] = None
            except (ValueError, AttributeError):
                out["width"] = None
            try:
                h_text = self.height_var.get().strip()
                out["height"] = int(h_text) if h_text else None
                if out["height"] is not None and out["height"] <= 0:
                    out["height"] = None
            except (ValueError, AttributeError):
                out["height"] = None
        return out

    @staticmethod
    def _compute_one_dim(native: tuple[int, int], s: dict) -> tuple[int, int] | None:
        ow, oh = native
        if s["mode"] == "scale":
            if s.get("scale"):
                return max(1, int(ow * s["scale"])), max(1, int(oh * s["scale"]))
            return ow, oh
        # fixed
        w, h = s.get("width"), s.get("height")
        if w and h:
            if s["keep_ratio"]:
                r = min(w / ow, h / oh)
                return max(1, int(ow * r)), max(1, int(oh * r))
            return w, h
        if w:
            return w, max(1, int(w * oh / ow))
        if h:
            return max(1, int(h * ow / oh)), h
        return ow, oh

    # ---------- conversion ----------
    def start_conversion(self):
        if self.is_converting or not self.selected_files:
            return

        # Перевірка налаштувань
        settings = self._read_settings()
        if settings["mode"] == "scale" and settings.get("scale") is None:
            self.status_label.configure(text="Невірний коефіцієнт масштабу", fg=ERROR)
            return
        if (settings["mode"] == "fixed"
                and settings.get("width") is None
                and settings.get("height") is None):
            self.status_label.configure(
                text="Вкажіть Width або Height (або переключися на Scale)", fg=ERROR)
            return

        # Перевірка MAX_OUTPUT_PIXELS (з урахуванням per-file overrides)
        with self._data_lock:
            files = list(self.selected_files)
            dims = dict(self.file_dims)
            file_root_snap = dict(self.file_root)
            overrides_snap = dict(self.file_overrides)

        for f in files:
            native = dims.get(f)
            if native is None:
                continue
            settings_for_f = overrides_snap.get(f, settings)
            pred = self._compute_one_dim(native, settings_for_f)
            if pred and pred[0] * pred[1] > MAX_OUTPUT_PIXELS:
                self.status_label.configure(
                    text=f"Занадто великий вихід для {f.name}: {pred[0]}×{pred[1]} > {MAX_OUTPUT_PIXELS:,}",
                    fg=ERROR)
                return

        self.is_converting = True
        self._update_buttons()

        output_dir = self.output_dir
        open_after = self.open_var.get() == 1
        overwrite = self.overwrite_var.get() == 1
        preserve_structure = self.preserve_structure_var.get() == 1
        suffix = self.suffix_var.get().strip()

        self.progress["value"] = 0
        self.analytics_label.configure(text="")
        self.current_label.configure(text="")
        self.status_label.configure(text="Конвертація...", fg=ACCENT)

        threading.Thread(target=self._convert_files,
                         args=(files, dims, settings, overrides_snap, output_dir, open_after,
                               overwrite, preserve_structure, suffix, file_root_snap),
                         daemon=True).start()

    def _convert_files(self, files, dims, global_settings, overrides, output_dir, open_after,
                       overwrite, preserve_structure, suffix, file_root):
        total = len(files)
        results: list[dict | None] = [None] * total
        completed = 0
        lock = threading.Lock()
        last_folder: Path | None = None

        # Build output paths
        output_map: dict[Path, Path] = {}
        used: set[Path] = set(files)
        for source in files:
            if output_dir and preserve_structure and source in file_root:
                root = file_root[source]
                try:
                    rel = source.relative_to(root)
                    folder = output_dir / rel.parent
                except ValueError:
                    folder = output_dir
            else:
                folder = output_dir if output_dir else source.parent
            base_name = f"{source.stem}{suffix}.png"
            out = folder / base_name
            n = 1
            while out in used or (not overwrite and out.exists()):
                out = folder / f"{source.stem}{suffix}_{n}.png"
                n += 1
            used.add(out)
            output_map[source] = out

        def convert_one(idx, source):
            native = dims.get(source)
            settings = overrides.get(source, global_settings)
            kw: dict = {"url": str(source), "write_to": str(output_map[source])}
            try:
                output_map[source].parent.mkdir(parents=True, exist_ok=True)
                if settings["mode"] == "scale":
                    if settings.get("scale") is None:
                        raise ValueError("scale не задано для цього файла")
                    kw["scale"] = settings["scale"]
                else:
                    if native is None:
                        with Image.open(io.BytesIO(cairosvg.svg2png(url=str(source)))) as im:
                            native = im.size
                    w, h = settings.get("width"), settings.get("height")
                    if not w and not h:
                        raise ValueError("Width/Height не задано для цього файла")
                    if w and h:
                        if settings.get("keep_ratio", True):
                            ow, oh = native
                            r = min(w / ow, h / oh)
                            kw["output_width"] = max(1, int(ow * r))
                            kw["output_height"] = max(1, int(oh * r))
                        else:
                            kw["output_width"] = w
                            kw["output_height"] = h
                    elif w:
                        kw["output_width"] = w
                    elif h:
                        kw["output_height"] = h
                cairosvg.svg2png(**kw)
                result = {"success": True, "source": source, "output": output_map[source],
                          "error": None}
            except Exception as exc:
                result = {"success": False, "source": source, "output": None,
                          "error": str(exc)}

            nonlocal completed, last_folder
            with lock:
                results[idx] = result
                completed += 1
                done = completed
                if result.get("output"):
                    last_folder = result["output"].parent
                progress = done / total * 100
            short = source.name if len(source.name) <= 28 else source.name[:25] + "..."
            self.root.after(0, lambda p=progress, d=done, n=short:
                            (self.progress.configure(value=p),
                             self.current_label.configure(text=f"Конвертовано {d}/{total}: {n}")))
            return result

        with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, total))) as ex:
            futs = {ex.submit(convert_one, i, s): i for i, s in enumerate(files)}
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception:
                    pass

        self.root.after(0, lambda: self._finish_conversion(results, last_folder, open_after))

    def _finish_conversion(self, results, last_folder, open_after):
        success = [r for r in results if r and r.get("success")]
        failed = [r for r in results if not r or not r.get("success")]

        if success and not failed:
            self.analytics_label.configure(
                text=f"Готово: {len(success)}/{len(results)}", fg=SUCCESS)
            self.status_label.configure(text="Успішно завершено", fg=SUCCESS)
            self.current_label.configure(text="")
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_dims.pop(src, None)
                    self.file_predicted_dims.pop(src, None)
                    self.file_overrides.pop(src, None)
                    self.file_root.pop(src, None)
        elif success:
            self.analytics_label.configure(
                text=f"Частково: {len(success)}/{len(results)} · помилок {len(failed)}",
                fg=WARNING)
            self.status_label.configure(text="Завершено з помилками", fg=WARNING)
            if failed and failed[0]:
                src_name = failed[0].get("source", Path("Unknown")).name
                self.current_label.configure(
                    text=f"Перша помилка: {src_name} — {failed[0].get('error')}", fg=ERROR)
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_dims.pop(src, None)
                    self.file_predicted_dims.pop(src, None)
                    self.file_overrides.pop(src, None)
                    self.file_root.pop(src, None)
        else:
            self.analytics_label.configure(text="Не вдалося конвертувати файли", fg=ERROR)
            self.status_label.configure(text="Помилка", fg=ERROR)
            if failed and failed[0]:
                src_name = failed[0].get("source", Path("Unknown")).name
                self.current_label.configure(
                    text=f"Перша помилка: {src_name} — {failed[0].get('error')}", fg=ERROR)

        self._selected_file = None
        self.is_converting = False
        self.refresh_files()
        self._update_selected_panel()

        self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
        self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
        self.labels_box.configure(bg=CARD_ALT)
        self.drop_label.configure(bg=CARD_ALT, fg=ACCENT, text="Перетягни SVG сюди")
        self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)

        if open_after and last_folder:
            _open_folder(last_folder)


# ============================================================
# ICO tab
# ============================================================
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICO_INPUT_EXTS = SUPPORTED_INPUT_EXTS | {".svg"}


def _ico_resample(method: str):
    """Повертає Pillow resample constant для назви."""
    name = {"lanczos": "LANCZOS", "bicubic": "BICUBIC", "nearest": "NEAREST"}.get(method, "LANCZOS")
    try:
        return getattr(Image.Resampling, name)
    except AttributeError:
        return getattr(Image, name)


def _parse_hex_color(text: str):
    """#rrggbb → (r, g, b, 255) або None при помилці."""
    text = text.strip()
    if not text.startswith("#") or len(text) != 7:
        return None
    try:
        r = int(text[1:3], 16)
        g = int(text[3:5], 16)
        b = int(text[5:7], 16)
        return (r, g, b, 255)
    except ValueError:
        return None


class IcoTab(tk.Frame):
    """Конвертер зображень/SVG → ICO."""

    def __init__(self, parent, root_window):
        super().__init__(parent, bg=BG)
        self.root = root_window

        # --- state ---
        self.selected_files: list[Path] = []
        self.file_root: dict[Path, Path] = {}
        self.file_size_overrides: dict[Path, set[int]] = {}  # per-file: набір розмірів
        self.output_dir: Path | None = None
        self.drop_queue: queue.Queue = queue.Queue()
        self.is_converting = False
        self._selected_file: Path | None = None
        self._loading_perfile = False

        # Global settings
        # Розміри (multi-check). За замовчуванням: 48×48 окремим файлом
        self.size_vars: dict[int, tk.IntVar] = {
            s: tk.IntVar(value=1 if s == 48 else 0) for s in ICO_SIZES
        }
        self.custom_size_var = tk.StringVar(value="")
        self.mode_var = tk.StringVar(value="separate")       # multi / separate
        self.square_fit_var = tk.StringVar(value="pad")      # pad / crop / stretch
        self.bg_fill_var = tk.StringVar(value="transparent") # transparent / white / black / custom
        self.bg_fill_custom_var = tk.StringVar(value="#ffffff")
        self.resize_method_var = tk.StringVar(value="lanczos")  # lanczos / bicubic / nearest
        self.recursive_var = tk.IntVar(value=0)
        self.overwrite_var = tk.IntVar(value=0)
        self.preserve_structure_var = tk.IntVar(value=0)
        self.open_var = tk.IntVar(value=0)
        self.suffix_var = tk.StringVar(value="")

        # Per-file (тільки розміри)
        self.pf_size_vars: dict[int, tk.IntVar] = {
            s: tk.IntVar(value=0) for s in ICO_SIZES
        }
        self.pf_custom_size_var = tk.StringVar(value="")

        # Preview state
        self._current_preview = None
        self._preview_job_id = 0
        self._last_preview_path: Path | None = None

        # Traces
        for v in self.size_vars.values():
            v.trace_add("write", lambda *_: self._on_settings_change())
        self.custom_size_var.trace_add("write", lambda *_: self._on_settings_change())
        for v in (self.mode_var, self.square_fit_var, self.bg_fill_var,
                  self.bg_fill_custom_var, self.resize_method_var):
            v.trace_add("write", lambda *_: self._on_settings_change())
        self.suffix_var.trace_add("write", lambda *_: self.refresh_files())
        for v in self.pf_size_vars.values():
            v.trace_add("write", lambda *_: self._on_perfile_change())
        self.pf_custom_size_var.trace_add("write", lambda *_: self._on_perfile_change())

        self._data_lock = threading.Lock()
        self._build_ui()
        self.root.after(120, self._poll_drop_queue)

    # ---------- main UI ----------
    def _build_ui(self):
        header = tk.Frame(self, bg=CARD, height=40)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        title_box = tk.Frame(header, bg=CARD)
        title_box.pack(side="left", padx=10, pady=5)
        tk.Label(title_box, text="🪟", bg=CARD, fg=ACCENT,
                 font=("Segoe UI Emoji", 14)).pack(side="left", padx=(0, 6))
        tk.Label(title_box, text="IMG to ICO", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        buttons = tk.Frame(header, bg=CARD)
        buttons.pack(side="right", padx=8, pady=6)
        self.btn_clear = ModernButton(buttons, "Очистити", self.clear_files,
                                      bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER)
        self.btn_clear.pack(side="left", padx=3)
        self.btn_convert = ModernButton(buttons, "Конвертувати", self.start_conversion,
                                        bg=SUCCESS, hover_bg=SUCCESS_HOVER)
        self.btn_convert.pack(side="left", padx=3)

        main = tk.Frame(self, bg=BG)
        main.pack(expand=True, fill="both", padx=10, pady=10)

        sidebar = tk.Frame(main, bg=BG, width=320)
        sidebar.pack(side="right", fill="y", padx=(8, 0))
        sidebar.pack_propagate(False)

        content = tk.Frame(main, bg=BG)
        content.pack(side="left", expand=True, fill="both")

        # Drop zone
        self.drop_zone = tk.Frame(content, bg=CARD_ALT,
                                  highlightthickness=1, highlightbackground=ACCENT_BORDER,
                                  height=56)
        self.drop_zone.pack(fill="x", pady=(0, 8))
        self.drop_zone.pack_propagate(False)
        self.drop_zone.bind("<Button-1>", lambda _e: self.select_files())

        self.drop_icon = tk.Label(self.drop_zone, text="📥", bg=CARD_ALT, fg=ACCENT,
                                  font=("Segoe UI Emoji", 16), cursor="hand2")
        self.drop_icon.pack(side="left", padx=(16, 8), pady=8)
        self.drop_icon.bind("<Button-1>", lambda _e: self.select_files())

        self.labels_box = tk.Frame(self.drop_zone, bg=CARD_ALT)
        self.labels_box.pack(side="left", fill="y", pady=8)
        self.drop_label = tk.Label(self.labels_box, text="Перетягни зображення/SVG сюди",
                                   bg=CARD_ALT, fg=ACCENT,
                                   font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.drop_label.pack(anchor="w")
        self.drop_label.bind("<Button-1>", lambda _e: self.select_files())
        self.drop_sub = tk.Label(self.labels_box, text="або клікни для вибору",
                                 bg=CARD_ALT, fg=MUTED,
                                 font=("Segoe UI", 8), cursor="hand2")
        self.drop_sub.pack(anchor="w")
        self.drop_sub.bind("<Button-1>", lambda _e: self.select_files())

        # File list
        list_card = tk.Frame(content, bg=CARD,
                             highlightthickness=1, highlightbackground=BORDER)
        list_card.pack(expand=True, fill="both")

        list_head = tk.Frame(list_card, bg=CARD)
        list_head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(list_head, text="Файли", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.count_label = tk.Label(list_head, text=f"0 {_plural_files(0)}",
                                    bg=CARD, fg=MUTED, font=("Segoe UI", 8))
        self.count_label.pack(side="right")

        list_wrap = tk.Frame(list_card, bg=CARD_ALT,
                             highlightthickness=1, highlightbackground=BORDER)
        list_wrap.pack(expand=True, fill="both", padx=10, pady=(0, 8))
        self.files_list = MultiColorListbox(list_wrap, bg=CARD_ALT, select_bg=ACCENT_BG,
                                            on_delete=self.delete_file)
        self.files_list.pack(expand=True, fill="both", padx=2, pady=2)
        self.files_list.bind("<<ListboxSelect>>", self.on_file_select)

        # Preview з шахівкою на весь контейнер
        self.preview_frame = tk.Frame(content, bg=PERFILE_BG,
                                      highlightthickness=1,
                                      highlightbackground=PERFILE_BORDER,
                                      height=160)
        self.preview_frame.pack(fill="x", pady=(8, 0))
        self.preview_frame.pack_propagate(False)
        self.preview_label = tk.Label(self.preview_frame, text="",
                                      bg=PERFILE_BG, fg=MUTED, font=("Segoe UI", 8))
        self.preview_label.pack(expand=True, fill="both", padx=4, pady=4)

        # Per-file: тільки розміри + reset
        self.perfile_frame = tk.Frame(content, bg=PERFILE_BG,
                                      highlightthickness=1,
                                      highlightbackground=PERFILE_BORDER)
        self.perfile_frame.pack(fill="x", pady=(8, 0))

        pf_inner = tk.Frame(self.perfile_frame, bg=PERFILE_BG)
        pf_inner.pack(fill="x", padx=10, pady=8)

        self.pf_title_label = tk.Label(
            pf_inner, text="Файл не вибрано (розміри для конкретного файла)",
            bg=PERFILE_BG, fg=MUTED,
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left",
        )
        self.pf_title_label.pack(fill="x")

        pf_sizes_row = tk.Frame(pf_inner, bg=PERFILE_BG)
        pf_sizes_row.pack(fill="x", pady=(6, 0))
        self.pf_size_selector = MultiCheckSelector(
            pf_sizes_row, ICO_SIZES, self.pf_size_vars, bg=PERFILE_BG,
        )
        self.pf_size_selector.pack(side="left")

        pf_custom_row = tk.Frame(pf_inner, bg=PERFILE_BG)
        pf_custom_row.pack(fill="x", pady=(4, 0))
        tk.Label(pf_custom_row, text="Custom:", bg=PERFILE_BG, fg=PERFILE_TEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.pf_custom_entry = tk.Entry(
            pf_custom_row, textvariable=self.pf_custom_size_var, width=12,
            font=("Segoe UI", 9), bd=1, relief="solid",
            bg=RAISED, fg=TEXT, disabledbackground=PERFILE_BG, disabledforeground=DIM,
            insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.pf_custom_entry.pack(side="left", padx=(6, 0))
        tk.Label(pf_custom_row, text="(через кому, напр. 96,512)",
                 bg=PERFILE_BG, fg=MUTED, font=("Segoe UI", 7)).pack(side="left", padx=(6, 0))

        self.btn_pf_reset = ModernButton(
            pf_custom_row, "Скинути до загальних", self._reset_perfile,
            bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER, padx=8,
        )
        self.btn_pf_reset.pack(side="right")

        # Footer: status + progress
        self.progress = ttk.Progressbar(content, style="Modern.Horizontal.TProgressbar",
                                        orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 0))

        footer = tk.Frame(content, bg=BG)
        footer.pack(fill="x", pady=(4, 0))
        self.status_label = tk.Label(footer, text="Готово", bg=BG, fg=MUTED,
                                     font=("Segoe UI", 8, "bold"))
        self.status_label.pack(side="left")
        self.current_label = tk.Label(footer, text="", bg=BG, fg=MUTED,
                                      font=("Segoe UI", 8))
        self.current_label.pack(side="left", padx=(8, 0))
        self.analytics_label = tk.Label(footer, text="", bg=BG, fg=SUCCESS,
                                        font=("Segoe UI", 8, "bold"))
        self.analytics_label.pack(side="right")

        self._build_settings(sidebar)
        self._update_buttons()
        self._update_selected_panel()

    # ---------- sidebar (settings) ----------
    def _build_settings(self, parent):
        card = tk.Frame(parent, bg=CARD,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=10, pady=10)

        # РОЗМІРИ
        tk.Label(inner, text="РОЗМІРИ", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        sizes_box = tk.Frame(inner, bg=CARD)
        sizes_box.pack(fill="x", pady=(4, 0))
        MultiCheckSelector(sizes_box, ICO_SIZES, self.size_vars,
                           bg=CARD).pack(anchor="w")

        custom_row = tk.Frame(inner, bg=CARD)
        custom_row.pack(fill="x", pady=(6, 0))
        tk.Label(custom_row, text="Custom:", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9), width=8, anchor="w").pack(side="left")
        self.entry_custom = tk.Entry(
            custom_row, textvariable=self.custom_size_var, width=14,
            font=("Segoe UI", 9), bd=1, relief="solid",
            bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM,
            insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.entry_custom.pack(side="left", padx=(4, 0))
        tk.Label(inner, text="через кому, напр. 96,512",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 7)).pack(anchor="w", pady=(2, 0))

        # ПРЕСЕТИ
        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))
        tk.Label(inner, text="ПРЕСЕТИ", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        presets_row1 = tk.Frame(inner, bg=CARD)
        presets_row1.pack(fill="x", pady=(4, 0))
        ModernButton(presets_row1, "Favicon", lambda: self._apply_preset("favicon"),
                     bg=ACCENT_BG, fg=ACCENT, hover_bg=ACCENT_BORDER, padx=8
                     ).pack(side="left", padx=(0, 4))
        ModernButton(presets_row1, "Windows", lambda: self._apply_preset("windows"),
                     bg=ACCENT_BG, fg=ACCENT, hover_bg=ACCENT_BORDER, padx=8
                     ).pack(side="left", padx=4)
        presets_row2 = tk.Frame(inner, bg=CARD)
        presets_row2.pack(fill="x", pady=(4, 0))
        ModernButton(presets_row2, "Full set", lambda: self._apply_preset("full"),
                     bg=ACCENT_BG, fg=ACCENT, hover_bg=ACCENT_BORDER, padx=8
                     ).pack(side="left", padx=(0, 4))
        ModernButton(presets_row2, "Pixel art", lambda: self._apply_preset("pixelart"),
                     bg=ACCENT_BG, fg=ACCENT, hover_bg=ACCENT_BORDER, padx=8
                     ).pack(side="left", padx=4)

        # РЕЖИМ
        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))
        tk.Label(inner, text="ВИХІД", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        tk.Label(inner, text="Режим:", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 2))
        SegmentedSelector(inner,
                          [("multi", "Multi-resolution"), ("separate", "Окремі файли")],
                          self.mode_var, bg=CARD).pack(anchor="w")

        tk.Label(inner, text="Аспект:", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 2))
        SegmentedSelector(inner,
                          [("pad", "Pad"), ("crop", "Crop"), ("stretch", "Stretch")],
                          self.square_fit_var, bg=CARD).pack(anchor="w")

        tk.Label(inner, text="Фон:", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 2))
        SegmentedSelector(inner,
                          [("transparent", "Прозорий"), ("white", "Білий"),
                           ("black", "Чорний"), ("custom", "Custom")],
                          self.bg_fill_var,
                          command=lambda _v: self._update_bg_custom_state(),
                          bg=CARD).pack(anchor="w")

        bg_custom_row = tk.Frame(inner, bg=CARD)
        bg_custom_row.pack(fill="x", pady=(4, 0))
        tk.Label(bg_custom_row, text="Колір:", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9), width=8, anchor="w").pack(side="left")
        self.entry_bg_custom = tk.Entry(
            bg_custom_row, textvariable=self.bg_fill_custom_var, width=10,
            font=("Segoe UI", 9), bd=1, relief="solid",
            bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM,
            insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        self.entry_bg_custom.pack(side="left", padx=(4, 0))

        tk.Label(inner, text="Метод resize:", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 2))
        SegmentedSelector(inner,
                          [("lanczos", "Lanczos"), ("bicubic", "Bicubic"),
                           ("nearest", "Nearest")],
                          self.resize_method_var, bg=CARD).pack(anchor="w")

        # ПАПКА / СУФІКС / FLAGS
        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))
        tk.Label(inner, text="ВХІД / ВИХІД", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        folder_row = tk.Frame(inner, bg=CARD)
        folder_row.pack(fill="x", pady=(4, 0))
        tk.Label(folder_row, text="Папка збереження:",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(side="left")
        ModernButton(folder_row, "Вибрати…", self.select_output_folder,
                     bg=ACCENT_BG, fg=ACCENT_HOVER, hover_bg=ACCENT_BG_HOVER,
                     padx=8).pack(side="right")
        self.btn_clear_output = ModernButton(
            folder_row, "✕", self.clear_output_folder,
            bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER, padx=6,
        )
        self.btn_clear_output.pack(side="right", padx=(0, 4))

        self.output_label = tk.Label(
            inner, text="Поруч з оригіналом",
            bg=CARD, fg=MUTED, font=("Segoe UI", 8),
            wraplength=260, anchor="w", justify="left",
        )
        self.output_label.pack(fill="x", pady=(2, 4))

        suf_row = tk.Frame(inner, bg=CARD)
        suf_row.pack(fill="x", pady=(0, 2))
        tk.Label(suf_row, text="Суфікс імені:",
                 bg=CARD, fg=TEXT, font=("Segoe UI", 9)).pack(side="left")
        ModernButton(suf_row, "✕", self.clear_suffix,
                     bg=ERROR_BG, fg=ERROR, hover_bg=ERROR_BORDER,
                     padx=6).pack(side="right")
        suffix_input = tk.Entry(
            suf_row, textvariable=self.suffix_var,
            font=("Segoe UI", 9), bd=1, relief="solid",
            bg=RAISED, fg=TEXT, disabledbackground=CARD, disabledforeground=DIM,
            insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        suffix_input.pack(side="left", fill="x", expand=True, padx=(6, 6))
        tk.Label(inner, text='напр. "@2x" → icon@2x.ico',
                 bg=CARD, fg=MUTED, font=("Segoe UI", 7)
                 ).pack(anchor="w", pady=(0, 4))

        self._cb(inner, "Включати вкладені папки",
                 self.recursive_var).pack(anchor="w")
        self._cb(inner, "Перезаписувати існуючі файли",
                 self.overwrite_var).pack(anchor="w")
        self._cb(inner, "Зберігати структуру папок",
                 self.preserve_structure_var).pack(anchor="w")
        self._cb(inner, "Відкрити папку після завершення",
                 self.open_var).pack(anchor="w")

        self._update_bg_custom_state()

    def _cb(self, parent, text, var, command=None):
        return tk.Checkbutton(
            parent, text=text, variable=var, command=command,
            bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=TEXT,
            disabledforeground=DIM,
            selectcolor=BG, font=("Segoe UI", 8),
            bd=0, highlightthickness=0,
        )

    def _update_bg_custom_state(self):
        if not hasattr(self, "entry_bg_custom"):
            return
        is_custom = self.bg_fill_var.get() == "custom"
        self.entry_bg_custom.configure(state="normal" if is_custom else "disabled")

    # ---------- settings callbacks ----------
    def _on_settings_change(self):
        self._update_bg_custom_state()
        self._schedule_prediction()
        if self._selected_file is not None:
            self._request_preview(self._selected_file)

    def _apply_preset(self, name: str):
        preset_sizes = {
            "favicon": {16, 32, 48},
            "windows": {16, 32, 48, 256},
            "full": set(ICO_SIZES),
            "pixelart": {16, 32, 64},
        }.get(name, set())
        for s, var in self.size_vars.items():
            var.set(1 if s in preset_sizes else 0)
        # Метод ресайзу: nearest для pixel art, інакше повертаємо стандарт
        self.resize_method_var.set("nearest" if name == "pixelart" else "lanczos")
        self.custom_size_var.set("")
        self.status_label.configure(text=f"Пресет: {name}", fg=ACCENT)

    # ---------- DnD queue ----------
    def queue_drop(self, paths: list[Path]):
        if self.is_converting:
            return
        self.drop_queue.put(paths)

    def _poll_drop_queue(self):
        try:
            while True:
                paths = self.drop_queue.get_nowait()
                if paths:
                    self._handle_drop(paths)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_drop_queue)

    def _handle_drop(self, paths):
        if self.is_converting:
            return
        accepted: list[Path] = []
        roots: dict[Path, Path] = {}
        skipped = 0
        recursive = self.recursive_var.get() == 1
        for raw in paths:
            try:
                p = raw if isinstance(raw, Path) else Path(raw)
            except Exception:
                skipped += 1
                continue
            if p.is_file() and p.suffix.lower() in ICO_INPUT_EXTS:
                accepted.append(p)
                roots[p] = p.parent
            elif p.is_dir():
                pattern = "**/*" if recursive else "*"
                for f in p.glob(pattern):
                    if f.is_file() and f.suffix.lower() in ICO_INPUT_EXTS:
                        accepted.append(f)
                        roots[f] = p
            else:
                skipped += 1
        self.add_files(accepted, roots, skipped_files=skipped)

    # ---------- file management ----------
    def select_files(self):
        if self.is_converting:
            return
        ftypes = [("Зображення/SVG",
                   " ".join(f"*{e}" for e in sorted(ICO_INPUT_EXTS))),
                  ("Всі файли", "*.*")]
        files = filedialog.askopenfilenames(title="Виберіть файли", filetypes=ftypes)
        if files:
            paths = [Path(f) for f in files]
            self.add_files(paths, {p: p.parent for p in paths})

    def add_files(self, paths: list[Path], roots: dict[Path, Path],
                  skipped_files: int = 0):
        with self._data_lock:
            existing = set(self.selected_files)
            added: list[Path] = []
            for p in paths:
                if p in existing or p.suffix.lower() not in ICO_INPUT_EXTS:
                    continue
                self.selected_files.append(p)
                self.file_root[p] = roots.get(p, p.parent)
                existing.add(p)
                added.append(p)
            count = len(self.selected_files)
        if added:
            self.drop_label.configure(text=f"Додано: {count} {_plural_files(count)}",
                                      fg=SUCCESS)
            self.drop_zone.configure(highlightbackground=SUCCESS_BORDER)
        elif skipped_files:
            self.status_label.configure(
                text=f"Пропущено {skipped_files} непідтримуваних", fg=WARNING)
        self.refresh_files()
        self._schedule_prediction()
        self._update_buttons()

    def clear_files(self):
        if self.is_converting:
            return
        with self._data_lock:
            self.selected_files.clear()
            self.file_root.clear()
            self.file_size_overrides.clear()
            self._selected_file = None
        self.progress["value"] = 0
        self.current_label.configure(text="")
        self.analytics_label.configure(text="")
        self.status_label.configure(text="Готово", fg=MUTED)
        self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
        self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
        self.labels_box.configure(bg=CARD_ALT)
        self.drop_label.configure(bg=CARD_ALT, fg=ACCENT,
                                  text="Перетягни зображення/SVG сюди")
        self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)
        self.refresh_files()
        self._update_selected_panel()
        self._update_buttons()

    def delete_file(self, index: int):
        if self.is_converting:
            return
        with self._data_lock:
            if not (0 <= index < len(self.selected_files)):
                return
            path = self.selected_files.pop(index)
            self.file_root.pop(path, None)
            self.file_size_overrides.pop(path, None)
            if self._selected_file == path:
                self._selected_file = None
        self.status_label.configure(text=f"Видалено: {path.name}", fg=ACCENT)
        n = len(self.selected_files)
        if n == 0:
            self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
            self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
            self.labels_box.configure(bg=CARD_ALT)
            self.drop_label.configure(bg=CARD_ALT, fg=ACCENT,
                                      text="Перетягни зображення/SVG сюди")
            self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)
        else:
            self.drop_label.configure(text=f"Додано: {n} {_plural_files(n)}")
        self.refresh_files()
        self._update_selected_panel()
        self._schedule_prediction()
        self._update_buttons()

    def refresh_files(self, preserve_scroll: bool = True):
        selected_index = None
        try:
            scroll_top = self.files_list.yview()[0] if preserve_scroll else 0.0
        except Exception:
            scroll_top = 0.0

        with self._data_lock:
            files = list(self.selected_files)
            overrides = dict(self.file_size_overrides)

        if self._selected_file in files:
            try:
                selected_index = files.index(self._selected_file)
            except ValueError:
                selected_index = None

        global_sizes = self._get_global_sizes()
        global_mode = self.mode_var.get()
        suffix = self.suffix_var.get().strip()

        self.files_list.delete(0, tk.END)
        for index, f in enumerate(files, start=1):
            file_sizes = overrides.get(f, global_sizes)
            sizes_str = ",".join(str(s) for s in sorted(file_sizes)) if file_sizes else "—"
            if global_mode == "multi":
                expected_name = f"{f.stem}{suffix}.ico"
                pred_text = sizes_str
            else:
                expected_name = f"{f.stem}{suffix}_<n>.ico"
                pred_text = f"{len(file_sizes)} файл(ів) · {sizes_str}"
            badge = " · per-file" if f in overrides else ""
            self.files_list.insert(
                tk.END,
                (f"{index}.", _truncate_middle(expected_name, 36) + badge,
                 "", "", pred_text, "done"),
            )

        if selected_index is not None:
            self.files_list.selection_clear(0, tk.END)
            self.files_list.selection_set(selected_index)
            self.files_list.activate(selected_index)

        if preserve_scroll:
            try:
                self.files_list.yview_moveto(scroll_top)
            except Exception:
                pass

        self.count_label.configure(text=f"{len(files)} {_plural_files(len(files))}")

    # ---------- output folder / suffix ----------
    def select_output_folder(self):
        if self.is_converting:
            return
        folder = filedialog.askdirectory(title="Вибери папку збереження")
        if not folder:
            return
        self.output_dir = Path(folder).resolve()
        self.output_label.configure(text=_shorten_path(self.output_dir), fg=SUCCESS)
        self._update_buttons()

    def clear_output_folder(self):
        if self.is_converting or self.output_dir is None:
            return
        self.output_dir = None
        self.output_label.configure(text="Поруч з оригіналом", fg=MUTED)
        self._update_buttons()

    def clear_suffix(self):
        if self.is_converting:
            return
        self.suffix_var.set("")

    # ---------- buttons ----------
    def _update_buttons(self):
        has_files = bool(self.selected_files)
        has_output = self.output_dir is not None
        converting = self.is_converting
        self.btn_convert.set_enabled(has_files and not converting)
        self.btn_clear.set_enabled(has_files and not converting)
        if hasattr(self, "btn_clear_output"):
            self.btn_clear_output.set_enabled(has_output and not converting)

    # ---------- size resolution ----------
    def _parse_custom_sizes(self, text: str) -> set[int]:
        result: set[int] = set()
        for chunk in text.replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                v = int(chunk)
            except ValueError:
                continue
            if 1 <= v <= 1024:
                result.add(v)
        return result

    def _get_global_sizes(self) -> set[int]:
        sizes = {s for s, v in self.size_vars.items() if v.get() == 1}
        sizes |= self._parse_custom_sizes(self.custom_size_var.get())
        return sizes

    def _get_perfile_sizes(self) -> set[int]:
        sizes = {s for s, v in self.pf_size_vars.items() if v.get() == 1}
        sizes |= self._parse_custom_sizes(self.pf_custom_size_var.get())
        return sizes

    # ---------- per-file ----------
    def on_file_select(self, _event=None):
        sel = self.files_list.curselection()
        if not sel:
            self._selected_file = None
        else:
            idx = sel[0]
            with self._data_lock:
                files = list(self.selected_files)
            if 0 <= idx < len(files):
                self._selected_file = files[idx]
        self._update_selected_panel()

    def _update_selected_panel(self):
        sel = self._selected_file
        self._loading_perfile = True
        try:
            if sel is None:
                self.pf_title_label.configure(
                    text="Файл не вибрано (розміри для конкретного файла)",
                    fg=MUTED)
                for v in self.pf_size_vars.values():
                    v.set(0)
                self.pf_custom_size_var.set("")
                self.pf_size_selector.set_enabled(False)
                self.pf_custom_entry.configure(state="disabled")
                if hasattr(self, "btn_pf_reset"):
                    self.btn_pf_reset.set_enabled(False)
                self._request_preview(None)
                return

            override = self.file_size_overrides.get(sel)
            has_override = override is not None
            sizes = override if has_override else self._get_global_sizes()

            label = _truncate_middle(sel.name, 50)
            if has_override:
                label += "  ·  per-file"
            self.pf_title_label.configure(text=label,
                                          fg=PERFILE_TEXT if has_override else MUTED)
            # Заповнюємо чекбокси; нестандартні відправляємо в custom
            standard_sizes = set(ICO_SIZES)
            for s, v in self.pf_size_vars.items():
                v.set(1 if s in sizes else 0)
            custom_extra = sorted(sizes - standard_sizes)
            self.pf_custom_size_var.set(",".join(str(x) for x in custom_extra))
            self.pf_size_selector.set_enabled(True)
            self.pf_custom_entry.configure(state="normal")
            if hasattr(self, "btn_pf_reset"):
                self.btn_pf_reset.set_enabled(has_override)
            self._request_preview(sel)
        finally:
            self._loading_perfile = False

    def _on_perfile_change(self):
        if self._loading_perfile or self.is_converting:
            return
        sel = self._selected_file
        if sel is None:
            return
        self.file_size_overrides[sel] = self._get_perfile_sizes()
        self.pf_title_label.configure(
            text=_truncate_middle(sel.name, 50) + "  ·  per-file",
            fg=PERFILE_TEXT)
        if hasattr(self, "btn_pf_reset"):
            self.btn_pf_reset.set_enabled(True)
        self.refresh_files(preserve_scroll=True)
        self._request_preview(sel)

    def _reset_perfile(self):
        sel = self._selected_file
        if sel is None or self.is_converting:
            return
        if sel in self.file_size_overrides:
            del self.file_size_overrides[sel]
        self._update_selected_panel()
        self.refresh_files(preserve_scroll=True)

    # ---------- preview ----------
    @staticmethod
    def _checkerboard(w, h, sz=8):
        ts = sz * 2
        tile = Image.new("RGBA", (ts, ts), (255, 255, 255, 255))
        tile.paste(Image.new("RGBA", (sz, sz), (220, 220, 225, 255)), (0, 0))
        tile.paste(Image.new("RGBA", (sz, sz), (220, 220, 225, 255)), (sz, sz))
        cols, rows = -(-w // ts), -(-h // ts)
        row_img = Image.new("RGBA", (ts * cols, ts))
        for c in range(cols):
            row_img.paste(tile, (c * ts, 0))
        full = Image.new("RGBA", (ts * cols, ts * rows))
        for r in range(rows):
            full.paste(row_img, (0, r * ts))
        return full.crop((0, 0, w, h))

    PREVIEW_MAX_W = 600
    PREVIEW_MAX_H = 150

    def _request_preview(self, path):
        self._preview_job_id += 1
        job_id = self._preview_job_id
        self._last_preview_path = path

        if path is None:
            self._current_preview = None
            self.preview_label.configure(image="", text="")
            return

        self.preview_label.configure(image="", text="…")

        # Snapshot налаштувань (виконується у GUI-треді, безпечно)
        settings = self._read_settings()
        override = self.file_size_overrides.get(path)
        sizes = override if override is not None else self._get_global_sizes()
        target_size = max(sizes) if sizes else 256

        def worker():
            preview = None
            err = None
            try:
                render_size = max(target_size, 512)
                img = self._load_image_rgba(path, render_size=render_size)
                bg_color = self._resolve_bg_color(settings)
                img = self._apply_bg_fill(img, bg_color)
                img = self._apply_square_fit(img, settings["square_fit"], bg_color)
                resample = _ico_resample(settings["resize_method"])
                # Resize до фактичного target_size — таким буде найбільший фрейм у .ico
                sized = img.resize((target_size, target_size), resample)
                # Зменшити для відображення, якщо target_size більший за preview area
                ow, oh = sized.size
                r = min(self.PREVIEW_MAX_W / ow, self.PREVIEW_MAX_H / oh, 1.0)
                if r < 1.0:
                    pw, ph = max(1, int(ow * r)), max(1, int(oh * r))
                    small = sized.resize((pw, ph), _ico_resample("lanczos"))
                else:
                    pw, ph = ow, oh
                    small = sized.copy()
                bg = self._checkerboard(pw, ph)
                bg.alpha_composite(small)
                preview = bg
            except Exception as exc:
                err = str(exc)

            def apply():
                if job_id != self._preview_job_id:
                    return
                if preview is None:
                    self._current_preview = None
                    self.preview_label.configure(
                        image="", text=err or "(no preview)")
                else:
                    photo = ImageTk.PhotoImage(preview)
                    self._current_preview = photo
                    # підпис у tooltip-форматі: target × target px
                    self.preview_label.configure(
                        image=photo, text="", compound="center")
            self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- image loading & transforms ----------
    @staticmethod
    def _load_image_rgba(path: Path, render_size: int = 512) -> Image.Image:
        if path.suffix.lower() == ".svg":
            if not CAIROSVG_OK:
                raise RuntimeError("cairosvg недоступний — SVG не підтримується")
            png_bytes = cairosvg.svg2png(url=str(path),
                                         output_width=render_size,
                                         output_height=render_size)
            img = Image.open(io.BytesIO(png_bytes))
        else:
            img = Image.open(path)
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        return img

    @staticmethod
    def _resolve_bg_color(settings: dict):
        fill = settings.get("bg_fill", "transparent")
        if fill == "transparent":
            return None
        if fill == "white":
            return (255, 255, 255, 255)
        if fill == "black":
            return (0, 0, 0, 255)
        if fill == "custom":
            parsed = _parse_hex_color(settings.get("bg_fill_custom", ""))
            return parsed or (255, 255, 255, 255)
        return None

    @staticmethod
    def _apply_bg_fill(img: Image.Image, bg_color) -> Image.Image:
        if bg_color is None:
            return img
        bg = Image.new("RGBA", img.size, bg_color)
        bg.alpha_composite(img)
        return bg

    @staticmethod
    def _apply_square_fit(img: Image.Image, fit: str, bg_color) -> Image.Image:
        w, h = img.size
        if w == h:
            return img
        if fit == "stretch":
            return img
        if fit == "crop":
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            return img.crop((left, top, left + side, top + side))
        # pad (за замовчуванням)
        side = max(w, h)
        fill = bg_color if bg_color is not None else (0, 0, 0, 0)
        new_img = Image.new("RGBA", (side, side), fill)
        offset = ((side - w) // 2, (side - h) // 2)
        new_img.paste(img, offset, img)
        return new_img

    # ---------- prediction ----------
    def _schedule_prediction(self):
        self.refresh_files(preserve_scroll=True)

    # ---------- conversion ----------
    def _read_settings(self) -> dict:
        return {
            "mode": self.mode_var.get(),
            "square_fit": self.square_fit_var.get(),
            "bg_fill": self.bg_fill_var.get(),
            "bg_fill_custom": self.bg_fill_custom_var.get(),
            "resize_method": self.resize_method_var.get(),
        }

    def start_conversion(self):
        if self.is_converting or not self.selected_files:
            return

        global_sizes = self._get_global_sizes()
        with self._data_lock:
            files = list(self.selected_files)
            overrides = dict(self.file_size_overrides)
            file_root_snap = dict(self.file_root)

        # Перевірка: для кожного файла мають бути розміри
        for f in files:
            sizes = overrides.get(f, global_sizes)
            if not sizes:
                self.status_label.configure(
                    text=f"Для {f.name} не вказано жодного розміру", fg=ERROR)
                return
            if any(s > 1024 or s < 1 for s in sizes):
                self.status_label.configure(
                    text=f"Розмір поза межами 1..1024 для {f.name}", fg=ERROR)
                return

        settings = self._read_settings()

        self.is_converting = True
        self._update_buttons()
        output_dir = self.output_dir
        open_after = self.open_var.get() == 1
        overwrite = self.overwrite_var.get() == 1
        preserve_structure = self.preserve_structure_var.get() == 1
        suffix = self.suffix_var.get().strip()

        self.progress["value"] = 0
        self.analytics_label.configure(text="")
        self.current_label.configure(text="")
        self.status_label.configure(text="Конвертація...", fg=ACCENT)

        threading.Thread(target=self._convert_files,
                         args=(files, global_sizes, overrides, settings, output_dir,
                               open_after, overwrite, preserve_structure, suffix,
                               file_root_snap),
                         daemon=True).start()

    def _convert_files(self, files, global_sizes, overrides, settings, output_dir,
                       open_after, overwrite, preserve_structure, suffix, file_root):
        total = len(files)
        results: list = [None] * total
        completed = 0
        lock = threading.Lock()
        last_folder: Path | None = None

        def convert_one(idx: int, source: Path):
            nonlocal completed, last_folder
            sizes = sorted(overrides.get(source, global_sizes))
            result = {"success": False, "source": source, "outputs": [], "error": None}
            try:
                # Destination folder
                if output_dir and preserve_structure and source in file_root:
                    root = file_root[source]
                    try:
                        rel = source.relative_to(root)
                        folder = output_dir / rel.parent
                    except ValueError:
                        folder = output_dir
                else:
                    folder = output_dir if output_dir else source.parent
                folder.mkdir(parents=True, exist_ok=True)

                # Load + transform
                render_size = max(max(sizes), 512)
                img = self._load_image_rgba(source, render_size=render_size)
                bg_color = self._resolve_bg_color(settings)
                img = self._apply_bg_fill(img, bg_color)
                img = self._apply_square_fit(img, settings["square_fit"], bg_color)
                resample = _ico_resample(settings["resize_method"])

                outputs: list[Path] = []
                if settings["mode"] == "multi":
                    out_path = folder / f"{source.stem}{suffix}.ico"
                    if not overwrite:
                        n = 1
                        while out_path.exists():
                            out_path = folder / f"{source.stem}{suffix}_{n}.ico"
                            n += 1
                    resized_imgs = [img.resize((s, s), resample) for s in sizes]
                    first = resized_imgs[0]
                    first.save(out_path, format="ICO",
                               sizes=[(s, s) for s in sizes],
                               append_images=resized_imgs[1:])
                    outputs.append(out_path)
                else:
                    for s in sizes:
                        out_path = folder / f"{source.stem}{suffix}_{s}.ico"
                        if not overwrite:
                            n = 1
                            while out_path.exists():
                                out_path = folder / f"{source.stem}{suffix}_{s}_{n}.ico"
                                n += 1
                        resized = img.resize((s, s), resample)
                        resized.save(out_path, format="ICO", sizes=[(s, s)])
                        outputs.append(out_path)

                result.update({"success": True, "outputs": outputs})
            except Exception as exc:
                result["error"] = str(exc)

            with lock:
                results[idx] = result
                completed += 1
                done = completed
                if result["outputs"]:
                    last_folder = result["outputs"][-1].parent
                progress = done / total * 100

            def progress_apply():
                self.progress["value"] = progress
                self.current_label.configure(
                    text=f"Конвертовано {done}/{total}: {_truncate_middle(source.name, 36)}",
                    fg=ACCENT)
            self.root.after(0, progress_apply)

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(convert_one, i, f) for i, f in enumerate(files)]
            for fut in as_completed(futs):
                _ = fut.result()

        self.root.after(0, lambda: self._finish_conversion(results, last_folder, open_after))

    def _finish_conversion(self, results, last_folder, open_after):
        success = [r for r in results if r and r["success"]]
        failed = [r for r in results if r and not r["success"]]
        total = len(results)
        all_outputs = [o for r in success for o in r["outputs"]]

        if not failed:
            self.analytics_label.configure(
                text=f"✓ {len(success)}/{total} · згенеровано {len(all_outputs)} .ico",
                fg=SUCCESS)
            self.status_label.configure(text="Готово", fg=SUCCESS)
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_root.pop(src, None)
                    self.file_size_overrides.pop(src, None)
        elif success:
            self.analytics_label.configure(
                text=f"Частково: {len(success)}/{total} · помилок {len(failed)}",
                fg=WARNING)
            self.status_label.configure(text="Завершено з помилками", fg=WARNING)
            if failed[0]:
                src_name = failed[0]["source"].name
                self.current_label.configure(
                    text=f"Перша помилка: {src_name} — {failed[0]['error']}", fg=ERROR)
            with self._data_lock:
                for r in success:
                    src = r["source"]
                    self.selected_files = [f for f in self.selected_files if f != src]
                    self.file_root.pop(src, None)
                    self.file_size_overrides.pop(src, None)
        else:
            self.analytics_label.configure(text="Не вдалося конвертувати файли", fg=ERROR)
            self.status_label.configure(text="Помилка", fg=ERROR)
            if failed[0]:
                src_name = failed[0]["source"].name
                self.current_label.configure(
                    text=f"Перша помилка: {src_name} — {failed[0]['error']}", fg=ERROR)

        self._selected_file = None
        self.is_converting = False
        self.refresh_files()
        self._update_selected_panel()
        self._update_buttons()

        self.drop_zone.configure(bg=CARD_ALT, highlightbackground=ACCENT_BORDER)
        self.drop_icon.configure(bg=CARD_ALT, fg=ACCENT)
        self.labels_box.configure(bg=CARD_ALT)
        self.drop_label.configure(bg=CARD_ALT, fg=ACCENT,
                                  text="Перетягни зображення/SVG сюди")
        self.drop_sub.configure(bg=CARD_ALT, fg=MUTED)

        if open_after and last_folder:
            _open_folder(last_folder)


# ============================================================
# Main app
# ============================================================
class ImageConverterApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("920x780")
        root.minsize(840, 660)
        root.configure(bg=BG)

        self._setup_styles()
        self._build_ui()

        if WINDND_AVAILABLE:
            self._enable_drag_drop()

        # Глобальний обробник кліка — знімає фокус з Entry при кліку поза ним
        root.bind_all("<Button-1>", self._focus_handler, add="+")

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Modern.Horizontal.TProgressbar",
                        troughcolor=RAISED, background=ACCENT,
                        bordercolor=RAISED, lightcolor=ACCENT, darkcolor=ACCENT,
                        thickness=6)
        # Notebook стилі під темну тему: неактивна — маленька, активна — більша
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab",
                        background=CARD, foreground=MUTED,
                        padding=[8, 3], font=("Segoe UI", 8),
                        borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT_BG), ("active", RAISED)],
                  foreground=[("selected", ACCENT_HOVER), ("active", TEXT)],
                  padding=[("selected", [16, 6])],
                  font=[("selected", ("Segoe UI", 9, "bold"))])

    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill="both")

        self.webp_tab = WebPTab(self.notebook, self.root)
        self.notebook.add(self.webp_tab, text="  IMG to WEBP  ")

        self.svg_tab = SvgToPngTab(self.notebook, self.root)
        self.notebook.add(self.svg_tab, text="  SVG to PNG  ")

        self.ico_tab = IcoTab(self.notebook, self.root)
        self.notebook.add(self.ico_tab, text="  IMG to ICO  ")

    def _enable_drag_drop(self):
        try:
            windnd.hook_dropfiles(self.root.winfo_id(), self._handle_drop)
        except Exception as exc:
            print(f"Drag & drop error: {exc}")

    def _handle_drop(self, files):
        paths = []
        skipped = 0
        for item in files:
            if isinstance(item, str):
                paths.append(item)
                continue
            decoded = None
            for enc in ("utf-8", "cp1251"):
                try:
                    decoded = item.decode(enc)
                    break
                except (UnicodeDecodeError, AttributeError):
                    continue
            if decoded is not None:
                paths.append(decoded)
            else:
                skipped += 1

        # Маршрутизуємо в активну вкладку
        active = self._active_tab()
        if active is not None:
            active.queue_drop(paths)

    def _active_tab(self):
        try:
            idx = self.notebook.index(self.notebook.select())
        except Exception:
            return None
        tabs = [self.webp_tab, self.svg_tab, self.ico_tab]
        if 0 <= idx < len(tabs):
            return tabs[idx]
        return None

    def _focus_handler(self, event):
        if not isinstance(event.widget, tk.Entry):
            self.root.focus_set()


if __name__ == "__main__":
    root = tk.Tk()
    app = ImageConverterApp(root)
    root.mainloop()