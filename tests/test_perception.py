"""Perception + HUD templates on real screenshots of the PC build (v8.0.109w)."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from spikebot.config import GameplayConfig
from spikebot.gameplay.perception import Perception
from spikebot.vision import Frame, TemplateLibrary

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def lib():
    return TemplateLibrary(ROOT / "templates")


def frame(name: str, width: int = 1920) -> Frame:
    img = cv2.imread(str(FIX / name))
    assert img is not None, name
    if img.shape[1] != width:
        img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])),
                         interpolation=cv2.INTER_AREA)
    return Frame(img)


def near(a, b, tol):
    return abs(a - b) <= tol


def test_defense_frame_detects_everything(lib):
    d = Perception(GameplayConfig(), lib).detect(frame("match_defense_rings.jpg"))
    assert d.phase == "defense"
    assert d.player and near(d.player[0], 286, 8) and near(d.player[1], 618, 8)
    assert d.net_x is not None and near(d.net_x, 366, 10)
    assert d.floor_ring and near(d.floor_ring.x, 1623, 8) and near(d.floor_ring.y, 745, 8)
    assert d.air_ring and near(d.air_ring.x, 507, 15) and near(d.air_ring.y, 430, 15)
    assert d.shadow and near(d.shadow[0], 1264, 10) and near(d.shadow[1], 768, 10)


@pytest.mark.parametrize("width", [1920, 1600, 1280])
def test_hud_phase_at_several_window_sizes(lib, width):
    p = Perception(GameplayConfig(), lib)
    assert p.hud_phase(frame("match_defense_rings.jpg", width))[0] == "defense"
    assert p.hud_phase(frame("match_attack.jpg", width))[0] == "attack"
    assert p.hud_phase(frame("match_air_spike.jpg", width))[0] == "air"
    assert p.hud_phase(frame("home_menu.jpg", width))[0] == "unknown"


def test_menu_frames_have_no_gameplay_detections(lib):
    for name in ("home_menu.jpg", "rewards_continue.jpg"):
        d = Perception(GameplayConfig(), lib).detect(frame(name))
        assert d.phase == "unknown"
        assert d.player is None and d.floor_ring is None and d.air_ring is None


@pytest.mark.parametrize("width", [1920, 1280])
def test_screen_templates(lib, width):
    match = frame("match_defense_rings.jpg", width)
    home = frame("home_menu.jpg", width)
    rewards = frame("rewards_continue.jpg", width)
    assert lib.visible(match, "hud_pause")
    assert not lib.visible(home, "hud_pause") and not lib.visible(rewards, "hud_pause")
    assert lib.visible(home, "home_match") and not lib.visible(match, "home_match")
    assert lib.visible(rewards, "continue_prompt") and not lib.visible(match, "continue_prompt")
    m = lib.find(home, "home_match")
    # the Match button sits in the bottom-right corner
    assert m.x > 0.8 * home.width and m.y > 0.85 * home.height
