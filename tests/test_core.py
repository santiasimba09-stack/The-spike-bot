"""Unit tests for config, keys, input structs, matching, humanize, limits and flow parsing."""
from __future__ import annotations

import ctypes
import datetime as dt
import random
from pathlib import Path

import cv2
import numpy as np
import pytest

from spikebot import keys, win32
from spikebot.config import BotConfig, load_config, save_config
from spikebot.flow import FlowEngine, load_rules, parse_rules
from spikebot.humanize import human_delay, jitter_point, mouse_path
from spikebot.stats import RunStats, count_limit_reason, time_limit_reason
from spikebot.vision import Frame, Template, TemplateLibrary, match_template

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------- config
def test_config_roundtrip_and_unknown_keys(tmp_path):
    cfg = BotConfig()
    cfg.limits.max_runs = 7
    cfg.keys.jump_spike = "space"
    cfg.matching.scales = [0.95, 1.0]
    p = tmp_path / "config.yaml"
    save_config(cfg, p)
    text = p.read_text() + "\nbogus_section: {a: 1}\n"
    p.write_text(text)
    back = load_config(p)
    assert back.limits.max_runs == 7
    assert back.keys.jump_spike == "space"
    assert back.matching.scales == [0.95, 1.0]
    assert back.validate() == []


def test_config_bad_values_fall_back_and_validate(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("limits: {max_runs: lots, stop_at: '25:99'}\nkeys: {bump: banana}\n"
                 "hotkeys: {stop: f8, pause: f8}\n")
    cfg = load_config(p)
    assert cfg.limits.max_runs == BotConfig().limits.max_runs  # bad int -> default
    problems = " | ".join(cfg.validate())
    assert "stop_at" in problems and "keys.bump" in problems and "hotkeys" in problems


def test_missing_config_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.yaml") == BotConfig()


# --------------------------------------------------------------------- keys
def test_key_names():
    assert keys.vk_code("F8") == 0x77
    assert keys.vk_code("space") == 0x20 and keys.vk_code(" ") == 0x20
    assert keys.vk_code("Escape") == keys.vk_code("esc") == 0x1B
    assert keys.vk_code("z") == ord("Z")
    assert keys.is_extended("left") and not keys.is_extended("z")
    assert keys.fallback_scan_code("left") == 0x4B
    assert not keys.is_valid_key("banana")


def test_sendinput_struct_layout():
    # Must match the Win32 ABI or SendInput silently rejects every event.
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(win32.INPUT) == expected
    assert ctypes.sizeof(win32.KEYBDINPUT) == (24 if expected == 40 else 16)


# ------------------------------------------------------------------- vision
def _textured(h, w, seed=0):
    rng = np.random.default_rng(seed)
    return cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (5, 5), 0)


def test_match_scales_template_to_window_size():
    big = np.full((1080, 1920, 3), 30, np.uint8)
    patch = _textured(60, 120)
    big[900:960, 1700:1820] = patch
    tpl = Template("btn", patch, source_width=1920)
    m = match_template(Frame(big), tpl)
    assert m.score > 0.99 and (m.x, m.y) == (1760, 930)
    small = cv2.resize(big, (1280, 720), interpolation=cv2.INTER_AREA)
    m2 = match_template(Frame(small), tpl)
    assert m2.score > 0.9 and abs(m2.x - 1173) <= 3 and abs(m2.y - 620) <= 3


def test_flat_regions_do_not_match():
    flat = np.zeros((300, 300, 3), np.uint8)
    tpl = Template("t", _textured(20, 40), mode="white")
    m = match_template(Frame(flat), tpl)
    assert m is None or m.score < 0.1


def test_region_limits_search():
    img = np.full((400, 400, 3), 30, np.uint8)
    patch = _textured(20, 40)
    img[10:30, 10:50] = patch
    inside = Template("a", patch, source_width=400, region=(0, 0, 0.5, 0.5))
    outside = Template("b", patch, source_width=400, region=(0.5, 0.5, 1, 1))
    assert match_template(Frame(img), inside).score > 0.99
    assert match_template(Frame(img), outside).score < 0.5


def test_library_save_and_reload(tmp_path):
    lib = TemplateLibrary(tmp_path)
    lib.save_template("start_game", _textured(30, 60), 1920, mode="color", threshold=0.8,
                      region=(0.5, 0.8, 1, 1), note="test")
    lib2 = TemplateLibrary(tmp_path)
    t = lib2.templates["start_game"]
    assert t.threshold == 0.8 and t.region == (0.5, 0.8, 1.0, 1.0) and t.source_width == 1920


# ----------------------------------------------------------------- humanize
def test_humanize_bounds():
    rng = random.Random(1)
    for _ in range(500):
        d = human_delay(rng, 0.4, 1.5)
        assert 0.4 <= d <= 1.5
        x, y = jitter_point(rng, 100, 100, 40, 20, 0.5)
        assert 90 <= x <= 110 and 95 <= y <= 105
    path = mouse_path(rng, (0, 0), (300, 200))
    assert path[-1] == (300, 200) and len(path) >= 3


# ------------------------------------------------------------------- limits
def test_count_limits():
    from spikebot.config import LimitsConfig

    s = RunStats()
    lim = LimitsConfig(max_runs=2, max_matches=0, max_consecutive_losses=2)
    assert count_limit_reason(lim, s) == ""
    s.end_run(True)
    s.end_run(False)
    assert "2 run" in count_limit_reason(lim, s)
    s2 = RunStats()
    s2.on_match_lost()
    s2.on_match_lost()
    assert "losses" in count_limit_reason(lim, s2)


def test_stop_at_crosses_midnight():
    from spikebot.config import LimitsConfig

    lim = LimitsConfig(stop_at="02:00")
    s = RunStats()
    s.started_at = dt.datetime(2026, 10, 2, 23, 0).timestamp()
    assert time_limit_reason(lim, s, dt.datetime(2026, 10, 3, 1, 59)) == ""
    assert "02:00" in time_limit_reason(lim, s, dt.datetime(2026, 10, 3, 2, 0))
    lim2 = LimitsConfig(max_minutes=30)
    s.started_at = dt.datetime.now().timestamp() - 31 * 60
    assert "30 min" in time_limit_reason(lim2, s)


# --------------------------------------------------------------------- flow
def test_default_flow_parses_and_references_known_events():
    rules = load_rules(ROOT / "flow.yaml")
    names = [r.name for r in rules]
    assert names[0] == "danger" and "in_match" in names and "start_game" in names
    assert len(set(names)) == len(names)


def test_flow_rejects_bad_rules():
    with pytest.raises(ValueError):
        parse_rules({"rules": [{"name": "x", "when": ["a"], "action": "explode"}]})
    with pytest.raises(ValueError):
        parse_rules({"rules": [{"name": "x", "action": "click"}]})
    with pytest.raises(ValueError):
        parse_rules({"rules": [{"name": "x", "when": ["a"], "event": "party"}]})


def test_flow_flags_options_and_placeholders(tmp_path):
    lib = TemplateLibrary(tmp_path)
    patch = _textured(30, 60, seed=3)
    lib.save_template("bracket_reset", patch, 400)
    lib.save_template("prepare_for_match", _textured(30, 60, seed=4), 400)
    img = np.full((300, 400, 3), 30, np.uint8)
    img[10:40, 10:70] = patch
    img[100:130, 100:160] = lib.templates["prepare_for_match"].image
    cfg = BotConfig()
    rules = parse_rules({"rules": [
        {"name": "reset", "when": ["bracket_reset"], "if_flags": ["needs_reset"]},
        {"name": "prepare", "when": ["prepare_for_match"], "if_flags": ["!needs_reset"],
         "key": "{continue_key}"},
    ]})
    eng = FlowEngine(rules, lib, cfg)
    d = eng.evaluate(Frame(img), now=0)
    assert d.rule.name == "prepare" and d.key == "space" and d.target == "prepare_for_match"
    eng.flags.add("needs_reset")
    assert eng.evaluate(Frame(img), now=0).rule.name == "reset"
    eng.mark_fired(rules[0], now=0)
    assert eng.evaluate(Frame(img), now=0.5).cooling
