"""Dialog for cutting a template out of a game screenshot."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from ..template_catalog import CATALOG, info
from ..vision import MODES, TemplateLibrary, convert, match_template
from ..vision import Frame as VFrame


def bgr_to_photo(img: np.ndarray, max_w: int, max_h: int) -> tuple[ImageTk.PhotoImage, float]:
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return ImageTk.PhotoImage(Image.fromarray(rgb)), scale


class CropDialog(tk.Toplevel):
    def __init__(self, master, image: np.ndarray, library: TemplateLibrary,
                 default_name: str = "", default_threshold: float = 0.82, on_saved=None):
        super().__init__(master)
        self.title("Cut a template")
        self.image = image
        self.library = library
        self.on_saved = on_saved
        self.sel: tuple[int, int, int, int] | None = None  # image coords x0, y0, x1, y1
        self._drag_start = None
        self._rect_id = None

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.photo, self.scale = bgr_to_photo(image, max(480, min(int(sw * 0.72), sw - 380)),
                                              max(270, int(sh * 0.72)))

        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        ttk.Label(left, text="Drag a rectangle around the button/text. Crop tightly; "
                             "avoid parts that change (numbers, timers, animations).").pack(anchor="w")
        self.canvas = tk.Canvas(left, width=self.photo.width(), height=self.photo.height(),
                                cursor="crosshair", highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)

        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="n", padx=6, pady=6)
        ttk.Label(right, text="Template name").grid(row=0, column=0, sticky="w")
        names = sorted(set(CATALOG) | set(library.templates))
        self.name = tk.StringVar(value=default_name)
        cb = ttk.Combobox(right, textvariable=self.name, values=names, width=26)
        cb.grid(row=1, column=0, sticky="we")
        cb.bind("<<ComboboxSelected>>", lambda e: self._name_changed())
        self.desc = ttk.Label(right, text="", wraplength=260, foreground="#555")
        self.desc.grid(row=2, column=0, sticky="w", pady=(2, 8))

        ttk.Label(right, text="Match mode").grid(row=3, column=0, sticky="w")
        self.mode = tk.StringVar(value="color")
        ttk.Combobox(right, textvariable=self.mode, values=MODES, state="readonly",
                     width=10).grid(row=4, column=0, sticky="w")
        ttk.Label(right, text="white = bright text/icons on changing backgrounds",
                  foreground="#555").grid(row=5, column=0, sticky="w")

        ttk.Label(right, text="Threshold (0-1)").grid(row=6, column=0, sticky="w", pady=(8, 0))
        self.threshold = tk.StringVar(value=f"{default_threshold:.2f}")
        ttk.Entry(right, textvariable=self.threshold, width=8).grid(row=7, column=0, sticky="w")

        ttk.Label(right, text="Search area").grid(row=8, column=0, sticky="w", pady=(8, 0))
        self.whole = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="whole screen", variable=self.whole).grid(row=9, column=0,
                                                                               sticky="w")
        mrow = ttk.Frame(right)
        mrow.grid(row=10, column=0, sticky="w")
        ttk.Label(mrow, text="else crop area + margin %").pack(side="left")
        self.margin = tk.StringVar(value="10")
        ttk.Spinbox(mrow, from_=0, to=100, textvariable=self.margin, width=5).pack(side="left")

        ttk.Label(right, text="Selection (zoomed)").grid(row=11, column=0, sticky="w", pady=(8, 0))
        self.zoom = tk.Label(right, bg="#222", width=32, height=8)
        self.zoom.grid(row=12, column=0, sticky="we")
        self.result = ttk.Label(right, text="", wraplength=260)
        self.result.grid(row=13, column=0, sticky="w", pady=4)
        btns = ttk.Frame(right)
        btns.grid(row=14, column=0, sticky="we", pady=6)
        ttk.Button(btns, text="Check uniqueness", command=self.check).pack(side="left")
        ttk.Button(btns, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
        self._name_changed()
        self.transient(master)
        self.grab_set()

    # ------------------------------------------------------------ selection
    def _name_changed(self) -> None:
        i = info(self.name.get().strip())
        self.desc.config(text=i.what)
        if self.name.get().strip() in CATALOG:
            self.mode.set(i.mode)
            if i.mode == "white":
                self.threshold.set("0.75")

    def _down(self, e):
        self._drag_start = (e.x, e.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff3030", width=2)

    def _move(self, e):
        if self._drag_start and self._rect_id:
            x0, y0 = self._drag_start
            self.canvas.coords(self._rect_id, x0, y0, e.x, e.y)

    def _up(self, e):
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        self._drag_start = None
        X0, X1 = sorted((x0, e.x))
        Y0, Y1 = sorted((y0, e.y))
        s = self.scale
        h, w = self.image.shape[:2]
        sel = (max(0, int(X0 / s)), max(0, int(Y0 / s)), min(w, int(X1 / s)), min(h, int(Y1 / s)))
        if sel[2] - sel[0] < 6 or sel[3] - sel[1] < 6:
            self.result.config(text="Selection too small.")
            return
        self.sel = sel
        crop = self.image[sel[1]:sel[3], sel[0]:sel[2]]
        z = 220 / max(crop.shape[1], crop.shape[0] * 1.5)
        zoomed = cv2.resize(crop, (max(1, int(crop.shape[1] * z)), max(1, int(crop.shape[0] * z))),
                            interpolation=cv2.INTER_NEAREST)
        self._zoom_photo, _ = bgr_to_photo(zoomed, 400, 300)
        self.zoom.config(image=self._zoom_photo, width=self._zoom_photo.width(),
                         height=self._zoom_photo.height())
        self.result.config(text=f"{crop.shape[1]}x{crop.shape[0]} px")

    # ---------------------------------------------------------------- logic
    def _region(self) -> tuple[float, float, float, float] | None:
        if self.whole.get() or self.sel is None:
            return None
        try:
            m = max(0.0, float(self.margin.get())) / 100.0
        except ValueError:
            m = 0.1
        h, w = self.image.shape[:2]
        x0, y0, x1, y1 = self.sel
        return (max(0.0, x0 / w - m), max(0.0, y0 / h - m), min(1.0, x1 / w + m),
                min(1.0, y1 / h + m))

    def check(self) -> float | None:
        """Best score of the crop anywhere else on the screenshot (lower = more unique)."""
        if self.sel is None:
            self.result.config(text="Select an area first.")
            return None
        x0, y0, x1, y1 = self.sel
        crop = self.image[y0:y1, x0:x1].copy()
        mode = self.mode.get()
        hay = convert(self.image, mode)
        t = convert(crop, mode)
        res = cv2.matchTemplate(hay, t, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res)
        pad_x, pad_y = (x1 - x0) // 2 + 2, (y1 - y0) // 2 + 2
        res[max(0, y0 - pad_y):y0 + pad_y, max(0, x0 - pad_x):x0 + pad_x] = -1
        other = float(res.max()) if res.size else 0.0
        try:
            thr = float(self.threshold.get())
        except ValueError:
            thr = 0.82
        if other >= thr:
            self.result.config(text=f"Warning: also matches elsewhere (score {other:.2f} >= "
                                    f"{thr:.2f}). Crop a more distinctive part or lower the "
                                    "search area.", foreground="#b00")
        else:
            self.result.config(text=f"Unique: best other match {other:.2f} < {thr:.2f}",
                               foreground="#070")
        return other

    def save(self) -> None:
        name = self.name.get().strip()
        if not name or not name.replace("_", "").isalnum():
            messagebox.showerror("Template", "Use a name with letters, digits and _ only.",
                                 parent=self)
            return
        if self.sel is None:
            messagebox.showerror("Template", "Drag a rectangle first.", parent=self)
            return
        try:
            thr = float(self.threshold.get())
            assert 0 < thr <= 1
        except (ValueError, AssertionError):
            messagebox.showerror("Template", "Threshold must be between 0 and 1.", parent=self)
            return
        if name in self.library.templates and not messagebox.askyesno(
                "Template", f"Replace the existing '{name}' template?", parent=self):
            return
        x0, y0, x1, y1 = self.sel
        crop = self.image[y0:y1, x0:x1].copy()
        region = self._region()
        self.library.save_template(name, crop, self.image.shape[1], mode=self.mode.get(),
                                   threshold=thr, region=region,
                                   note="captured with the SpikeBot UI")
        # Sanity check against the screenshot it came from.
        m = match_template(VFrame(self.image), self.library.templates[name], self.library.scales)
        score = m.score if m else 0.0
        if self.on_saved:
            self.on_saved(name, score)
        self.destroy()
