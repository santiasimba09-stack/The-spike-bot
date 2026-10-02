"""A tiny simulated game for end-to-end tests of the menu flow.

Screens are canvases with template patches pasted at fixed spots. The fake acts
both as the screen source (``grab``) and the input backend (clicks/keys), and
moves between screens like the real tournament does.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import yaml

from spikebot.vision import Frame

W, H = 960, 540
TEMPLATE_NAMES = [
    "home_match", "mode_tournament", "prepare_for_match", "start_game", "hud_pause",
    "result_win", "result_lose", "continue_prompt", "tournament_winner", "bracket_reset",
    "tier_confirm", "tier_gold", "claim_reward", "danger_purchase",
]


def make_templates(folder: Path, seed: int = 1) -> dict[str, np.ndarray]:
    """Write distinct random-texture templates + manifest (source_width = W)."""
    rng = np.random.default_rng(seed)
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    manifest = {}
    for name in TEMPLATE_NAMES:
        img = rng.integers(0, 255, size=(30, 60, 3), dtype=np.uint8)
        img = cv2.GaussianBlur(img, (3, 3), 0)
        cv2.imwrite(str(folder / f"{name}.png"), img)
        out[name] = img
        manifest[name] = {"file": f"{name}.png", "source_width": W, "mode": "color",
                          "threshold": 0.9}
    (folder / "templates.yaml").write_text(yaml.safe_dump(manifest))
    return out


@dataclass
class Button:
    name: str
    x: int
    y: int
    to: str  # next screen


@dataclass
class Screen:
    name: str
    buttons: list[Button] = field(default_factory=list)
    keys: dict[str, str] = field(default_factory=dict)  # key -> next screen
    show: list[tuple[str, int, int]] = field(default_factory=list)  # non-clickable patches


class FakeGame:
    def __init__(self, templates: dict[str, np.ndarray], results: list[str],
                 match_frames: int = 3, start: str = "home"):
        self.tpl = templates
        self.results = list(results)  # "win"/"lose" per match, in order
        self.match_frames = match_frames
        self.screen = start
        self.log: list[str] = []
        self.clicks: list[tuple[str, str]] = []
        self.keys_pressed: list[str] = []
        self.wins_in_run = 0
        self._match_left = 0
        self._pos = (0, 0)
        self._down: set[str] = set()
        self.foreground = True
        self.rect = (0, 0, W, H)
        self.hwnd = 1
        self.title = "fake"
        self.screens = self._build()

    # ------------------------------------------------------------ screens
    def _build(self) -> dict[str, Screen]:
        return {
            "home": Screen("home", [Button("home_match", 800, 480, "match_menu")]),
            "match_menu": Screen("match_menu", [Button("mode_tournament", 300, 250, "bracket")]),
            "bracket": Screen("bracket", [Button("prepare_for_match", 820, 480, "team"),
                                          Button("bracket_reset", 700, 480, "tier_popup")]),
            "bracket_done": Screen("bracket_done", [Button("prepare_for_match", 820, 480, "bracket_done"),
                                                    Button("bracket_reset", 700, 480, "tier_popup")]),
            "tier_popup": Screen("tier_popup", [Button("tier_gold", 400, 250, "tier_popup"),
                                                Button("tier_confirm", 480, 400, "bracket")]),
            "team": Screen("team", [Button("start_game", 820, 480, "match")]),
            "match": Screen("match", show=[("hud_pause", 900, 40)]),
            "rewards": Screen("rewards", keys={"space": "result"},
                              show=[("continue_prompt", 100, 500)]),
            "result_win": Screen("result_win", keys={"space": "after_win"},
                                 show=[("result_win", 300, 200)]),
            "result_lose": Screen("result_lose", keys={"space": "bracket_done"},
                                  show=[("result_lose", 300, 200)]),
            "winner": Screen("winner", keys={"space": "bracket_done"},
                             show=[("tournament_winner", 480, 200)]),
            "shop": Screen("shop", show=[("danger_purchase", 480, 270)]),
        }

    def render(self) -> np.ndarray:
        img = np.full((H, W, 3), 40, np.uint8)
        cv2.putText(img, self.screen, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
        sc = self.screens[self.screen]
        for name, x, y in [(b.name, b.x, b.y) for b in sc.buttons] + sc.show:
            t = self.tpl[name]
            th, tw = t.shape[:2]
            img[y - th // 2:y - th // 2 + th, x - tw // 2:x - tw // 2 + tw] = t
        return img

    def _goto(self, name: str) -> None:
        if name == "match":
            self._match_left = self.match_frames
        elif name == "result":
            res = self.results.pop(0) if self.results else "win"
            name = "result_win" if res == "win" else "result_lose"
            if res == "lose":
                self.wins_in_run = 0
        elif name == "after_win":
            self.wins_in_run += 1
            if self.wins_in_run >= 3:
                self.wins_in_run = 0
                name = "winner"
            else:
                name = "bracket"
        self.log.append(name)
        self.screen = name

    # ------------------------------------------------------- screen source
    def grab(self) -> Frame:
        if self.screen == "match":
            if self._match_left <= 0:
                self._goto("rewards")
            else:
                self._match_left -= 1
        return Frame(self.render(), time.monotonic())

    def locate(self, force: bool = False):
        return self.rect

    def is_foreground(self) -> bool:
        return self.foreground

    def focus(self) -> bool:
        return True

    def close(self) -> None:
        pass

    # -------------------------------------------------------- input backend
    def key_down(self, name: str) -> None:
        self._down.add(name)

    def key_up(self, name: str) -> None:
        if name not in self._down:
            return
        self._down.discard(name)
        self.keys_pressed.append(name)
        nxt = self.screens[self.screen].keys.get(name)
        if nxt:
            self._goto(nxt)

    def move_to(self, x: int, y: int) -> None:
        self._pos = (x, y)

    def mouse_down(self) -> None:
        pass

    def mouse_up(self) -> None:
        x, y = self._pos
        for b in self.screens[self.screen].buttons:
            th, tw = self.tpl[b.name].shape[:2]
            if abs(x - b.x) <= tw // 2 and abs(y - b.y) <= th // 2:
                self.clicks.append((self.screen, b.name))
                self._goto(b.to)
                return
        self.clicks.append((self.screen, "<miss>"))

    def cursor_pos(self) -> tuple[int, int]:
        return self._pos
