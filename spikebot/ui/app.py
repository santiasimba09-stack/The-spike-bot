"""SpikeBot configuration & control window (tkinter)."""
from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import cv2
import numpy as np

from .. import __version__
from ..config import ON_STUCK, SIDES, TIERS, BotConfig, load_config, save_config
from ..control import BotControl
from ..flow import load_rules
from ..hotkeys import HotkeyManager
from ..platform import IS_WINDOWS
from ..runner import BotRunner
from ..template_catalog import CATALOG, info
from ..vision import MODES, Frame, TemplateLibrary
from .crop import CropDialog, bgr_to_photo

log = logging.getLogger(__name__)

TK_KEYS = {"space": "space", "return": "enter", "escape": "esc", "tab": "tab",
           "backspace": "backspace", "left": "left", "right": "right", "up": "up",
           "down": "down", "shift_l": "lshift", "shift_r": "rshift", "control_l": "lctrl",
           "control_r": "rctrl", "alt_l": "lalt", "alt_r": "ralt", "prior": "pageup",
           "next": "pagedown", "home": "home", "end": "end", "insert": "insert",
           "delete": "delete", "comma": ",", "period": ".", "slash": "/", "semicolon": ";",
           "apostrophe": "'", "bracketleft": "[", "bracketright": "]", "minus": "-",
           "equal": "=", "grave": "`", "backslash": "\\"}


def tk_key_name(keysym: str) -> str:
    k = keysym.lower()
    if k in TK_KEYS:
        return TK_KEYS[k]
    if k.startswith("kp_") and k[3:].isdigit():
        return f"num{k[3:]}"
    return k


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait(self.format(record))
        except Exception:
            pass


class App:
    def __init__(self, root: tk.Tk, config_path: Path, base_dir: Path):
        self.root = root
        self.config_path = config_path
        self.base_dir = base_dir
        self.cfg = load_config(config_path)
        self.vars: dict[tuple[str, str], tuple[tk.Variable, type]] = {}
        self.control = BotControl()
        self.runner: BotRunner | None = None
        self.status_q: queue.Queue = queue.Queue()
        self.log_q: queue.Queue = queue.Queue()
        self.calls_q: queue.Queue = queue.Queue()  # callables to run on the Tk thread
        self._preview: np.ndarray | None = None
        self._preview_lock = threading.Lock()
        self._last_capture: np.ndarray | None = None
        self._last_scores: dict[str, float] = {}

        h = QueueLogHandler(self.log_q)
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        h.setLevel(logging.INFO)
        logging.getLogger().addHandler(h)

        root.title(f"SpikeBot {__version__} - tournament helper for The Spike Cross")
        root.minsize(980, 680)
        self._build()
        self.hotkeys = HotkeyManager()
        self._bind_hotkeys()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)

    # ================================================================ helpers
    @property
    def library(self) -> TemplateLibrary:
        return TemplateLibrary(self.base_dir / self.cfg.templates_dir,
                               self.cfg.matching.default_threshold, self.cfg.matching.scales)

    def _var(self, section: str, name: str) -> tk.Variable:
        key = (section, name)
        if key in self.vars:
            return self.vars[key][0]
        obj = self.cfg if section == "" else getattr(self.cfg, section)
        value = getattr(obj, name)
        if isinstance(value, bool):
            var: tk.Variable = tk.BooleanVar(value=value)
        elif isinstance(value, list):
            var = tk.StringVar(value=", ".join(str(v) for v in value))
        else:
            var = tk.StringVar(value=str(value))
        self.vars[key] = (var, type(value))
        return var

    def _field(self, parent, row: int, section: str, name: str, label: str, help: str = "",
               choices=None, width: int = 10, key: bool = False) -> None:
        var = self._var(section, name)
        typ = self.vars[(section, name)][1]
        if typ is bool:
            ttk.Checkbutton(parent, text=label, variable=var).grid(row=row, column=0, columnspan=2,
                                                                   sticky="w", pady=2)
        else:
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2, padx=(0, 8))
            if choices:
                w = ttk.Combobox(parent, textvariable=var, values=list(choices), width=width,
                                 state="readonly")
            else:
                w = ttk.Entry(parent, textvariable=var, width=width)
            if key:
                box = ttk.Frame(parent)
                box.grid(row=row, column=1, sticky="w")
                w = ttk.Entry(box, textvariable=var, width=width)
                w.pack(side="left")
                ttk.Button(box, text="Set...", width=6,
                           command=lambda v=var, e=w: self._capture_key(v, e)).pack(side="left",
                                                                                     padx=4)
            else:
                w.grid(row=row, column=1, sticky="w")
        if help:
            ttk.Label(parent, text=help, foreground="#666").grid(row=row, column=2, sticky="w",
                                                                  padx=8)

    def _capture_key(self, var: tk.StringVar, entry: ttk.Entry) -> None:
        old = var.get()
        var.set("press a key...")

        def on_key(e):
            self.root.unbind("<KeyPress>")
            var.set(tk_key_name(e.keysym) if e.keysym != "Escape" or old == "" else old)
            return "break"

        entry.focus_set()
        self.root.bind("<KeyPress>", on_key)

    def _collect(self) -> BotConfig | None:
        """Build a config from the form; shows errors and returns None if invalid."""
        data = self.cfg.to_dict()
        errors = []
        for (section, name), (var, typ) in self.vars.items():
            raw = var.get()
            target = data if section == "" else data[section]
            try:
                if typ is bool:
                    target[name] = bool(raw)
                elif typ is int:
                    target[name] = int(str(raw).strip())
                elif typ is float:
                    target[name] = float(str(raw).strip())
                elif typ is list:
                    target[name] = [float(x) for x in str(raw).replace(";", ",").split(",")
                                    if x.strip()]
                else:
                    target[name] = str(raw).strip()
            except ValueError:
                errors.append(f"{section}.{name}: '{raw}' is not a valid number")
        cfg = BotConfig.from_dict(data)
        errors += cfg.validate()
        if errors:
            messagebox.showerror("Settings", "Please fix:\n\n" + "\n".join(errors))
            return None
        return cfg

    def save(self, quiet: bool = False) -> bool:
        cfg = self._collect()
        if cfg is None:
            return False
        self.cfg = cfg
        save_config(cfg, self.config_path)
        if not quiet:
            self._log(f"Settings saved to {self.config_path}")
        self._bind_hotkeys()
        return True

    def _call_soon(self, fn) -> None:
        """Run ``fn`` on the Tk thread (safe to call from any thread)."""
        self.calls_q.put(fn)

    def _log(self, text: str) -> None:
        self.log_q.put(f"{dt.datetime.now():%H:%M:%S}  {text}")

    # ================================================================== build
    def _build(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista" if IS_WINDOWS else "clam")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.nb = nb
        tabs = {}
        for name in ("Run", "Limits", "Tournament", "Gameplay", "Templates", "Window",
                     "Advanced"):
            f = ttk.Frame(nb, padding=10)
            nb.add(f, text=name)
            tabs[name] = f
        self._build_run(tabs["Run"])
        self._build_limits(tabs["Limits"])
        self._build_tournament(tabs["Tournament"])
        self._build_gameplay(tabs["Gameplay"])
        self._build_templates(tabs["Templates"])
        self._build_window(tabs["Window"])
        self._build_advanced(tabs["Advanced"])
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Save settings", command=self.save).pack(side="left")
        ttk.Button(bar, text="Open bot folder", command=self._open_folder).pack(side="left", padx=6)
        self.hotkey_label = ttk.Label(bar, text="", foreground="#666")
        self.hotkey_label.pack(side="right")

    # ------------------------------------------------------------------ run
    def _build_run(self, f) -> None:
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.start_btn = ttk.Button(top, text="Start", style="Big.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(top, text="Pause", style="Big.TButton", command=self.pause,
                                    state="disabled")
        self.pause_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(top, text="Stop", style="Big.TButton", command=self.stop,
                                   state="disabled")
        self.stop_btn.pack(side="left")
        opts = ttk.Frame(top)
        opts.pack(side="left", padx=20)
        ttk.Label(opts, text="Max runs (0 = no limit)").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(opts, from_=0, to=9999, textvariable=self._var("limits", "max_runs"),
                    width=6).grid(row=0, column=1, sticky="w", padx=4)
        gp = self._var("gameplay", "enabled")
        ttk.Radiobutton(opts, text="Bot plays the matches", variable=gp, value=True).grid(
            row=1, column=0, sticky="w")
        ttk.Radiobutton(opts, text="I play, bot does the menus", variable=gp, value=False).grid(
            row=1, column=1, sticky="w")
        ttk.Checkbutton(opts, text="Dry run (no real input)", variable=self._var("", "dry_run")
                        ).grid(row=2, column=0, sticky="w")
        self.live = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Live view", variable=self.live).grid(row=2, column=1,
                                                                         sticky="w")

        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, pady=8)
        st = ttk.LabelFrame(mid, text="Status", padding=8)
        st.pack(side="left", fill="y")
        self.status_vars = {}
        for i, (k, label) in enumerate([("state", "State"), ("runs", "Runs"), ("run", "This run"),
                                        ("matches", "Matches"), ("streak", "Loss streak"),
                                        ("elapsed", "Elapsed"), ("action", "Last action"),
                                        ("reason", "Stop reason")]):
            ttk.Label(st, text=label + ":").grid(row=i, column=0, sticky="nw", pady=1)
            v = tk.StringVar(value="-")
            ttk.Label(st, textvariable=v, wraplength=260).grid(row=i, column=1, sticky="w",
                                                               padx=6)
            self.status_vars[k] = v
        lv = ttk.LabelFrame(mid, text="Live view (what the bot sees in matches)", padding=4)
        lv.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.preview_label = tk.Label(lv, bg="#111", fg="#888",
                                      text="Starts when a match is detected")
        self.preview_label.pack(fill="both", expand=True)

        lf = ttk.LabelFrame(f, text="Log", padding=4)
        lf.pack(fill="both", expand=True)
        self.log_text = scrolledtext.ScrolledText(lf, height=10, state="disabled",
                                                  font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    # --------------------------------------------------------------- limits
    def _build_limits(self, f) -> None:
        ttk.Label(f, text="When should the bot stop? Any limit that is reached stops it "
                          "(0 / empty = no limit).").grid(row=0, column=0, columnspan=3,
                                                          sticky="w", pady=(0, 8))
        r = 1
        for name, label, help in [
            ("max_runs", "Max tournament runs", "a run = one tournament from the first match until "
                                                "it is won or you are eliminated"),
            ("max_matches", "Max matches", "stop after this many matches"),
            ("max_minutes", "Max minutes", "stop after running this long"),
            ("stop_at", "Stop at (HH:MM)", "local clock time, e.g. 23:30"),
            ("max_consecutive_losses", "Max losses in a row", "safety: stop if the bot keeps losing"),
            ("finish_current_run", "Finish the current run before a time limit stops the bot", ""),
        ]:
            self._field(f, r, "limits", name, label, help)
            r += 1

    # ----------------------------------------------------------- tournament
    def _build_tournament(self, f) -> None:
        r = 0
        self._field(f, r, "tournament", "tier", "Tier after a reset", "keep = just confirm the "
                    "popup", choices=TIERS); r += 1
        self._field(f, r, "tournament", "matches_per_tournament", "Wins per tournament",
                    "quarterfinal + semifinal + final = 3"); r += 1
        self._field(f, r, "tournament", "continue_key", "Continue key",
                    "key shown on '[key] Continue' prompts (empty = click)", key=True); r += 1
        for name, label in [("reset_after_win", "Reset the tournament after winning it"),
                            ("reset_after_loss", "Reset the tournament after being eliminated"),
                            ("auto_claim", "Claim rewards automatically ('Claim Reward')"),
                            ("skip_cutscenes", "Skip cutscenes/dialogues ('Skip')")]:
            self._field(f, r, "tournament", name, label); r += 1
        ttk.Label(f, text="\nThe screen-by-screen flow lives in flow.yaml (advanced). The bot never "
                          "clicks the 'Recharge' button and stops on any 'danger_*' template.",
                  foreground="#666", wraplength=700).grid(row=r, column=0, columnspan=3,
                                                          sticky="w")

    # ------------------------------------------------------------- gameplay
    def _build_gameplay(self, f) -> None:
        left = ttk.LabelFrame(f, text="Keys (must match the game's Control Settings)",
                              padding=8)
        left.grid(row=0, column=0, sticky="nw")
        r = 0
        for name, label in [("left", "Move left"), ("right", "Move right"),
                            ("jump_spike", "Jump / Spike (bottom HUD button)"),
                            ("bump", "Bump / receive"), ("block", "Block"), ("slide", "Slide"),
                            ("serve", "Serve")]:
            self._field(left, r, "keys", name, label, key=True, width=9)
            r += 1
        ttk.Label(left, text="In-game the HUD shows each key next to\nJump/Bump/Block/Slide.",
                  foreground="#666").grid(row=r, column=0, columnspan=2, sticky="w", pady=4)

        right = ttk.LabelFrame(f, text="Behaviour & timing (pixels are for a 1920-wide window)",
                               padding=8)
        right.grid(row=0, column=1, sticky="nw", padx=10)
        r = 0
        self._field(right, r, "gameplay", "our_side", "Our side of the court", choices=SIDES,
                    width=7); r += 1
        rows = [
            ("receive_zone_px", "Receive zone (px)", "chase landing spots this close (0 = all)"),
            ("move_tolerance_px", "Move tolerance (px)", "stop when this close to the target"),
            ("bump_lead_ms", "Bump lead (ms)", "bump this long before the ball lands"),
            ("bump_distance_px", "Bump distance (px)", "fallback: bump when the ball is this close"),
            ("bump_cooldown_ms", "Bump cooldown (ms)", ""),
            ("attack_enabled", "Attack (jump + spike)", ""),
            ("spike_ring_offset_px", "Spike position offset (px)", "negative = behind the ring"),
            ("jump_lead_ms", "Jump lead (ms)", "jump this long before the ball reaches the ring"),
            ("jump_delay_ms", "Jump delay fallback (ms)", "after the ring appears"),
            ("spike_delay_ms", "Spike delay fallback (ms)", "after jumping"),
            ("spike_ring_radius_frac", "Spike inside ring (0-1)", "how centred the ball must be"),
            ("serve_enabled", "Serve automatically", ""),
            ("serve_idle_s", "Serve after idle (s)", ""),
            ("serve_hold_ms", "Serve hold (ms)", "0 = tap"),
            ("serve_second_press_ms", "Second serve press (ms)", "jump serve; 0 = single press"),
        ]
        for name, label, help in rows:
            self._field(right, r, "gameplay", name, label, help, width=8)
            r += 1

        bottom = ttk.LabelFrame(f, text="Vision, live view & recording", padding=8)
        bottom.grid(row=1, column=0, columnspan=2, sticky="we", pady=8)
        r = 0
        for name, label, help in [
            ("analysis_width", "Analysis width (px)", "lower = faster, less precise"),
            ("max_fps", "Max FPS", ""),
            ("debug_overlay", "Separate OpenCV live-view window (CLI)", ""),
            ("record_frames", "Record matches to recordings/ (for tuning)", "also records your game "
                                                                            "keys when you play"),
            ("record_every_n", "Record every Nth frame", ""),
            ("record_max_frames", "Max recorded frames per match", ""),
        ]:
            self._field(bottom, r, "gameplay", name, label, help, width=8)
            r += 1

    # ------------------------------------------------------------ templates
    def _build_templates(self, f) -> None:
        ttk.Label(f, text="Templates are small screenshots of buttons/text the bot looks for. "
                          "Capture missing ones from your game: open the screen in the game, "
                          "click 'Capture', then drag a box around the button.",
                  wraplength=900).pack(anchor="w")
        cols = ("group", "status", "threshold", "mode", "score")
        tv = ttk.Treeview(f, columns=cols, height=14)
        tv.heading("#0", text="Template")
        tv.column("#0", width=200)
        for c, w in zip(cols, (140, 110, 80, 70, 110)):
            tv.heading(c, text=c.capitalize() if c != "score" else "Last test score")
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="both", expand=True, pady=6)
        tv.bind("<<TreeviewSelect>>", lambda e: self._tpl_selected())
        self.tpl_tree = tv
        info_row = ttk.Frame(f)
        info_row.pack(fill="x")
        self.tpl_desc = ttk.Label(info_row, text="", wraplength=620)
        self.tpl_desc.pack(side="left", anchor="n")
        self.tpl_img = tk.Label(info_row, bg="#222")
        self.tpl_img.pack(side="right")
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Capture from game (3 s)", command=self.capture_template).pack(
            side="left")
        ttk.Button(btns, text="Crop from image file...", command=self.crop_from_file).pack(
            side="left", padx=4)
        ttk.Button(btns, text="Test all on game screen", command=self.test_templates).pack(
            side="left")
        ttk.Button(btns, text="Edit threshold/mode...", command=self.edit_template).pack(
            side="left", padx=4)
        ttk.Button(btns, text="Delete", command=self.delete_template).pack(side="left")
        ttk.Button(btns, text="Reload", command=self.refresh_templates).pack(side="left", padx=4)
        self.refresh_templates()

    def refresh_templates(self) -> None:
        lib = self.library
        names = set(CATALOG) | set(lib.templates)
        try:
            for r in load_rules(self.base_dir / self.cfg.flow_file):
                names |= {n for n in r.templates() if "{" not in n}
        except Exception as exc:
            self._log(f"flow.yaml problem: {exc}")
        tv = self.tpl_tree
        sel = tv.selection()
        tv.delete(*tv.get_children())
        order = {g: i for i, g in enumerate(dict.fromkeys(i.group for i in CATALOG.values()))}

        def key(n):
            i = info(n)
            return (order.get(i.group, 99), not i.required, n)

        for n in sorted(names, key=key):
            i = info(n)
            t = lib.templates.get(n)
            if t is not None:
                status = "ok"
            else:
                status = "MISSING (needed)" if i.required else "missing (optional)"
            score = self._last_scores.get(n)
            tv.insert("", "end", iid=n, text=n, values=(
                i.group, status,
                f"{lib.threshold_for(n):.2f}" if t is not None else "",
                t.mode if t is not None else i.mode,
                "" if score is None else f"{score:.2f}"
                + (" visible" if t is not None and score >= lib.threshold_for(n) else "")))
        for s in sel:
            if tv.exists(s):
                tv.selection_set(s)

    def _selected_template(self) -> str:
        sel = self.tpl_tree.selection()
        return sel[0] if sel else ""

    def _tpl_selected(self) -> None:
        n = self._selected_template()
        if not n:
            return
        i = info(n)
        self.tpl_desc.config(text=f"{n}  [{i.group}]\n{i.what}")
        t = self.library.templates.get(n)
        if t is not None:
            self._tpl_photo, _ = bgr_to_photo(t.image, 320, 120)
            self.tpl_img.config(image=self._tpl_photo)
        else:
            self.tpl_img.config(image="")

    def _grab_game(self) -> np.ndarray:
        from ..capture import GameScreen

        cfg = self._collect() or self.cfg
        screen = GameScreen(cfg.window)
        try:
            return screen.grab().image.copy()
        finally:
            screen.close()

    def capture_template(self) -> None:
        name = self._selected_template()
        self._log("Capturing the game window in 3 seconds - switch to the game screen now...")

        def work():
            time.sleep(3)
            try:
                img = self._grab_game()
            except Exception as exc:
                msg = str(exc)
                self._call_soon(lambda: messagebox.showerror("Capture", msg))
                return
            self._save_capture(img)
            self._call_soon(lambda: self._open_crop(img, name))

        threading.Thread(target=work, daemon=True).start()

    def _save_capture(self, img: np.ndarray) -> Path:
        d = self.base_dir / "captures"
        d.mkdir(exist_ok=True)
        p = d / f"capture_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
        cv2.imwrite(str(p), img)
        self._last_capture = img
        self._log(f"Saved {p.name} ({img.shape[1]}x{img.shape[0]})")
        return p

    def crop_from_file(self) -> None:
        p = filedialog.askopenfilename(title="Screenshot of the game", initialdir=str(
            self.base_dir / "captures"), filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp")])
        if not p:
            return
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            messagebox.showerror("Open", f"Could not read {p}")
            return
        self._open_crop(img, self._selected_template())

    def _open_crop(self, img: np.ndarray, name: str) -> None:
        def saved(n, score):
            self._log(f"Template '{n}' saved (score on its screenshot: {score:.2f})")
            self.refresh_templates()
            if self.tpl_tree.exists(n):
                self.tpl_tree.selection_set(n)

        CropDialog(self.root, img, self.library, default_name=name,
                   default_threshold=self.cfg.matching.default_threshold, on_saved=saved)

    def test_templates(self) -> None:
        self._log("Testing all templates on the game window...")

        def work():
            try:
                img = self._grab_game()
            except Exception as exc:
                msg = str(exc)
                self._call_soon(lambda: messagebox.showerror("Test", msg))
                return
            self._call_soon(lambda: self._score_all(img))

        threading.Thread(target=work, daemon=True).start()

    def _score_all(self, img: np.ndarray) -> None:
        lib = self.library
        frame = Frame(img)
        self._last_scores = {}
        visible = []
        for n in lib.templates:
            m = lib.best(frame, n)
            self._last_scores[n] = m.score if m else 0.0
            if m and m.score >= lib.threshold_for(n):
                visible.append(f"{n} ({m.score:.2f})")
        self._log("Visible now: " + (", ".join(visible) if visible else "nothing"))
        self.refresh_templates()

    def edit_template(self) -> None:
        n = self._selected_template()
        lib = self.library
        if n not in lib.templates:
            messagebox.showinfo("Edit", "Select a captured template first.")
            return
        t = lib.templates[n]
        win = tk.Toplevel(self.root)
        win.title(f"Edit {n}")
        thr = tk.StringVar(value=f"{lib.threshold_for(n):.2f}")
        mode = tk.StringVar(value=t.mode)
        whole = tk.BooleanVar(value=t.region is None)
        ttk.Label(win, text="Threshold").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(win, textvariable=thr, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(win, text="Mode").grid(row=1, column=0, sticky="w", padx=8)
        ttk.Combobox(win, textvariable=mode, values=MODES, state="readonly", width=8).grid(
            row=1, column=1, sticky="w")
        ttk.Checkbutton(win, text="Search the whole screen (ignore saved area)",
                        variable=whole).grid(row=2, column=0, columnspan=2, sticky="w", padx=8)

        def ok():
            try:
                v = float(thr.get())
                assert 0 < v <= 1
            except (ValueError, AssertionError):
                messagebox.showerror("Edit", "Threshold must be between 0 and 1.", parent=win)
                return
            changes = {"threshold": round(v, 3), "mode": mode.get()}
            if whole.get():
                changes["region"] = None
            lib.update_meta(n, **changes)
            win.destroy()
            self.refresh_templates()

        ttk.Button(win, text="Save", command=ok).grid(row=3, column=0, columnspan=2, pady=8)
        win.transient(self.root)
        win.grab_set()

    def delete_template(self) -> None:
        n = self._selected_template()
        lib = self.library
        if n not in lib.templates:
            return
        if not messagebox.askyesno("Delete", f"Delete template '{n}'?"):
            return
        t = lib.templates[n]
        try:
            (lib.folder / t.file).unlink()
        except OSError:
            pass
        manifest = lib.read_manifest()
        manifest.pop(n, None)
        lib.write_manifest(manifest)
        self.refresh_templates()

    # --------------------------------------------------------------- window
    def _build_window(self, f) -> None:
        r = 0
        self._field(f, r, "window", "title_contains", "Window title contains",
                    "part of the game window's title", width=24); r += 1
        self._field(f, r, "window", "process_name", "Process name (optional)",
                    "e.g. TheSpikeCross.exe - avoids matching browser tabs", width=24); r += 1
        self._field(f, r, "window", "require_foreground",
                    "Only send input while the game window is focused (recommended)"); r += 1
        self._field(f, r, "window", "allow_monitor_fallback",
                    "If the window isn't found, use the whole monitor"); r += 1
        self._field(f, r, "window", "fallback_monitor", "Monitor number", width=4); r += 1
        ttk.Button(f, text="Find game window", command=self.find_windows).grid(
            row=r, column=0, sticky="w", pady=8); r += 1
        self.win_list = tk.Listbox(f, height=10, width=100)
        self.win_list.grid(row=r, column=0, columnspan=3, sticky="we")
        self.win_list.bind("<Double-Button-1>", self._pick_window)
        self._windows = []
        ttk.Label(f, text="Double-click a window to use it. Run the game in windowed or "
                          "borderless mode at 16:9 (1920x1080 recommended).",
                  foreground="#666").grid(row=r + 1, column=0, columnspan=3, sticky="w")

    def find_windows(self) -> None:
        self.win_list.delete(0, "end")
        if not IS_WINDOWS:
            self.win_list.insert("end", "Window search is only available on Windows.")
            return
        from .. import win32

        self._windows = sorted(win32.list_windows(), key=lambda w: -w.width * w.height)
        title = self._var("window", "title_contains").get().lower()
        for w in self._windows:
            mark = "  <== matches" if title and title in w.title.lower() else ""
            self.win_list.insert("end", f"{w.title}   [{w.process}]   {w.width}x{w.height}{mark}")

    def _pick_window(self, _e=None) -> None:
        sel = self.win_list.curselection()
        if not sel or sel[0] >= len(self._windows):
            return
        w = self._windows[sel[0]]
        self._var("window", "title_contains").set(w.title)
        self._var("window", "process_name").set(w.process)
        self._log(f"Using window '{w.title}' ({w.process}, {w.width}x{w.height})")
        if (w.width, w.height) != (1920, 1080):
            self._log("Note: the starter templates were cut at 1920x1080; recapture HUD/menu "
                      "templates if detection is poor at this size.")

    # ------------------------------------------------------------- advanced
    def _build_advanced(self, f) -> None:
        a = ttk.LabelFrame(f, text="Human-like input", padding=8)
        a.grid(row=0, column=0, sticky="nw")
        r = 0
        for name, label, help in [
            ("delay_min_s", "Delay before actions min (s)", ""),
            ("delay_max_s", "Delay before actions max (s)", ""),
            ("click_jitter", "Click spread (0-1)", "how far from the centre"),
            ("move_min_s", "Mouse move time min (s)", ""),
            ("move_max_s", "Mouse move time max (s)", ""),
            ("press_min_s", "Key/click hold min (s)", ""),
            ("press_max_s", "Key/click hold max (s)", ""),
            ("curved_moves", "Curved mouse paths", ""),
        ]:
            self._field(a, r, "humanize", name, label, help, width=7)
            r += 1
        b = ttk.LabelFrame(f, text="Safety", padding=8)
        b.grid(row=0, column=1, sticky="nw", padx=10)
        r = 0
        self._field(b, r, "safety", "stuck_timeout_s", "Unknown screen timeout (s)", width=7); r += 1
        self._field(b, r, "safety", "on_stuck", "When stuck", "escape = press Esc once first",
                    choices=ON_STUCK, width=7); r += 1
        self._field(b, r, "safety", "max_match_minutes", "Max match length (min)", width=7); r += 1
        for name, label in [("stop_on_danger", "Stop on danger_* templates (shop/purchase)"),
                            ("verify_before_click", "Re-check buttons right before clicking"),
                            ("save_debug_screenshots", "Save screenshots when stuck / on errors")]:
            self._field(b, r, "safety", name, label); r += 1
        c = ttk.LabelFrame(f, text="Hotkeys (global)", padding=8)
        c.grid(row=1, column=0, sticky="nw", pady=8)
        self._field(c, 0, "hotkeys", "stop", "Emergency stop", key=True, width=7)
        self._field(c, 1, "hotkeys", "pause", "Pause / resume", key=True, width=7)
        self._field(c, 2, "hotkeys", "screenshot", "Save screenshot", key=True, width=7)
        d = ttk.LabelFrame(f, text="Matching", padding=8)
        d.grid(row=1, column=1, sticky="nw", padx=10, pady=8)
        self._field(d, 0, "matching", "default_threshold", "Default threshold", width=7)
        self._field(d, 1, "matching", "poll_interval_s", "Menu poll interval (s)", width=7)
        self._field(d, 2, "matching", "scales", "Scales", "e.g. 0.95, 1.0, 1.05", width=14)
        self._field(d, 3, "matching", "match_end_grace_s", "Match-end grace (s)", width=7)

    # ============================================================== control
    def start(self) -> None:
        if self.runner is not None and self.runner.running:
            return
        if not self.save(quiet=True):
            return
        lib = self.library
        need = [n for n, i in CATALOG.items() if i.required and n not in lib.templates]
        if need:
            self._log("Missing templates (rules using them won't fire): " + ", ".join(need))
        self.control.reset()
        self.runner = BotRunner(self.cfg, self.control, base_dir=self.base_dir,
                                on_status=self.status_q.put)
        if self.live.get():
            self.runner.preview_cb = self._set_preview
        self.runner.start_thread()
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal", text="Pause")
        self.stop_btn.config(state="normal")
        self._log(f"Started. Switch to the game - press {self.cfg.hotkeys.stop.upper()} "
                  "to stop at any time.")

    def pause(self) -> None:
        paused = self.control.toggle_pause()
        self.pause_btn.config(text="Resume" if paused else "Pause")
        if paused and self.runner and self.runner.input:
            self.runner.input.release_all()
        self._log("Paused" if paused else "Resumed")

    def stop(self) -> None:
        self.control.request_stop("stopped by user")

    def _set_preview(self, img: np.ndarray) -> None:
        with self._preview_lock:
            self._preview = img

    def _bind_hotkeys(self) -> None:
        hk = self.hotkeys
        hk.clear()
        hk.bind(self.cfg.hotkeys.stop, lambda: self.control.request_stop(
            f"{self.cfg.hotkeys.stop.upper()} pressed"))
        hk.bind(self.cfg.hotkeys.pause, lambda: self._call_soon(self.pause))
        hk.bind(self.cfg.hotkeys.screenshot, lambda: threading.Thread(
            target=self._hotkey_screenshot, daemon=True).start())
        ok = hk.start()
        self.hotkey_label.config(
            text=(f"Hotkeys: {self.cfg.hotkeys.stop.upper()} stop, "
                  f"{self.cfg.hotkeys.pause.upper()} pause, "
                  f"{self.cfg.hotkeys.screenshot.upper()} screenshot") if ok
            else "Global hotkeys unavailable - use the Stop button")

    def _hotkey_screenshot(self) -> None:
        try:
            self._save_capture(self._grab_game())
        except Exception as exc:
            self._log(f"Screenshot failed: {exc}")

    # ================================================================ polling
    def _poll(self) -> None:
        try:
            while True:
                fn = self.calls_q.get_nowait()
                try:
                    fn()
                except Exception:
                    log.exception("UI callback failed")
        except queue.Empty:
            pass
        try:
            while True:
                line = self.log_q.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", line + "\n")
                if int(self.log_text.index("end-1c").split(".")[0]) > 2000:
                    self.log_text.delete("1.0", "500.0")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        last = None
        try:
            while True:
                last = self.status_q.get_nowait()
        except queue.Empty:
            pass
        if last is not None:
            self._show_status(last)
        with self._preview_lock:
            img, self._preview = self._preview, None
        if img is not None and self.live.get():
            w = max(320, self.preview_label.winfo_width())
            h = max(180, self.preview_label.winfo_height())
            self._preview_photo, _ = bgr_to_photo(img, w, h)
            self.preview_label.config(image=self._preview_photo, text="")
        if self.runner is not None and not self.runner.running \
                and str(self.start_btn["state"]) == "disabled":
            self.start_btn.config(state="normal")
            self.pause_btn.config(state="disabled", text="Pause")
            self.stop_btn.config(state="disabled")
        self.root.after(100, self._poll)

    def _show_status(self, s: dict) -> None:
        sv = self.status_vars
        state = s.get("state", "-") + ("  (PAUSED)" if s.get("paused") else "")
        sv["state"].set(state)
        mr = s.get("max_runs") or 0
        sv["runs"].set(f"{s['runs_completed']} / {mr if mr else 'unlimited'}  "
                       f"(won {s['runs_won']}, lost {s['runs_lost']})")
        sv["run"].set(f"{s['run_wins']} win(s) so far" if s.get("run_active") else "-")
        sv["matches"].set(f"{s['matches_played']}  (W {s['matches_won']} / L {s['matches_lost']})")
        sv["streak"].set(str(s["consecutive_losses"]))
        e = int(s.get("elapsed_s", 0))
        sv["elapsed"].set(f"{e // 3600:d}:{e % 3600 // 60:02d}:{e % 60:02d}")
        sv["action"].set(s.get("last_action") or "-")
        reason = s.get("stop_reason") or (f"pending: {s['pending_stop']}"
                                          if s.get("pending_stop") else "-")
        sv["reason"].set(reason)

    # ================================================================== misc
    def _open_folder(self) -> None:
        p = str(self.base_dir)
        try:
            if IS_WINDOWS:
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as exc:
            self._log(f"Could not open folder: {exc}")

    def _on_close(self) -> None:
        if self.runner is not None and self.runner.running:
            if not messagebox.askyesno("Quit", "The bot is running. Stop it and quit?"):
                return
            self.control.request_stop("window closed")
            self.runner._thread.join(timeout=3)
        self.hotkeys.stop()
        self.root.destroy()


def main(config_path: str | Path = "config.yaml", base_dir: str | Path | None = None) -> None:
    from ..logging_setup import setup_logging
    from ..platform import set_dpi_awareness

    set_dpi_awareness()  # before any window exists
    base = Path(base_dir) if base_dir else Path(config_path).resolve().parent
    setup_logging(base / "logs")
    root = tk.Tk()
    App(root, Path(config_path), base)
    root.mainloop()
