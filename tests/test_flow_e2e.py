"""End-to-end: the real runner + real flow.yaml drive a simulated tournament."""
from __future__ import annotations

import random
from pathlib import Path

import yaml

from spikebot.config import BotConfig
from spikebot.control import BotControl
from spikebot.runner import BotRunner

from fakegame import FakeGame, make_templates

ROOT = Path(__file__).resolve().parents[1]


def fast_flow(tmp: Path) -> Path:
    data = yaml.safe_load((ROOT / "flow.yaml").read_text())
    for r in data["rules"]:
        r["settle_s"] = 0
        r["cooldown_s"] = 0.05 if r.get("cooldown_s", 1.5) else 0
        for s in r.get("steps", []):
            s["wait_s"] = 0
    p = tmp / "flow.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def make_runner(tmp: Path, game: FakeGame, **limits) -> BotRunner:
    cfg = BotConfig()
    cfg.templates_dir = "templates"
    cfg.flow_file = "flow.yaml"
    cfg.log_dir = "logs"
    cfg.gameplay.enabled = False  # menu flow only; "you play" waits for the match to end
    cfg.matching.poll_interval_s = 0.0
    cfg.matching.default_threshold = 0.9
    cfg.matching.match_end_grace_s = 0.2
    cfg.humanize.delay_min_s = cfg.humanize.delay_max_s = 0
    cfg.humanize.move_min_s = cfg.humanize.move_max_s = 0
    cfg.humanize.press_min_s = cfg.humanize.press_max_s = 0
    cfg.safety.stuck_timeout_s = 5
    cfg.safety.save_debug_screenshots = False
    cfg.tournament.tier = "gold"
    for k, v in limits.items():
        setattr(cfg.limits, k, v)
    return BotRunner(cfg, BotControl(), base_dir=tmp, source=game, backend=game,
                     rng=random.Random(0))


def setup(tmp: Path, results, start="home"):
    tpls = make_templates(tmp / "templates")
    fast_flow(tmp)
    return FakeGame(tpls, results, match_frames=2, start=start)


def test_two_full_runs_win_then_loss(tmp_path):
    # run 1: win, win, win (tournament won) ; run 2: win, lose (eliminated)
    game = setup(tmp_path, ["win", "win", "win", "win", "lose"])
    runner = make_runner(tmp_path, game, max_runs=2, max_consecutive_losses=0)
    reason = runner.run()
    s = runner.stats
    assert "completed 2 run" in reason, reason
    assert s.runs_completed == 2 and s.runs_won == 1 and s.runs_lost == 1
    assert s.matches_won == 4 and s.matches_lost == 1 and s.matches_played == 5
    # navigation happened via clicks, results/rewards via the continue key
    clicked = [b for _, b in game.clicks]
    assert clicked[:2] == ["home_match", "mode_tournament"]
    assert clicked.count("start_game") == 5
    assert "<miss>" not in clicked
    # after the tournament win the bracket was reset and a tier picked
    assert "bracket_reset" in clicked and "tier_gold" in clicked and "tier_confirm" in clicked
    assert game.keys_pressed.count("space") >= 10


def test_max_matches_limit(tmp_path):
    game = setup(tmp_path, ["win"] * 10, start="bracket")
    runner = make_runner(tmp_path, game, max_runs=0, max_matches=2)
    reason = runner.run()
    assert "played 2 match" in reason
    assert runner.stats.matches_played == 2


def test_consecutive_losses_limit(tmp_path):
    game = setup(tmp_path, ["lose"] * 5, start="bracket")
    runner = make_runner(tmp_path, game, max_runs=0, max_consecutive_losses=2)
    reason = runner.run()
    assert "2 losses in a row" in reason
    assert runner.stats.runs_lost == 2


def test_danger_screen_stops_immediately(tmp_path):
    game = setup(tmp_path, [], start="shop")
    runner = make_runner(tmp_path, game)
    reason = runner.run()
    assert "safety rule 'danger'" in reason
    assert game.clicks == [] and game.keys_pressed == []


def test_unknown_screen_stops_after_timeout(tmp_path):
    game = setup(tmp_path, [], start="home")
    game.screens["home"].buttons.clear()  # nothing recognisable
    runner = make_runner(tmp_path, game)
    runner.cfg.safety.stuck_timeout_s = 0.3
    reason = runner.run()
    assert "stuck on an unknown screen" in reason


def test_no_input_without_focus(tmp_path):
    game = setup(tmp_path, [], start="home")
    game.foreground = False
    runner = make_runner(tmp_path, game)
    control = runner.control

    import threading

    t = threading.Timer(0.5, lambda: control.request_stop("test over"))
    t.start()
    reason = runner.run()
    t.cancel()
    assert reason == "test over"
    assert game.clicks == [] and game.keys_pressed == []
    assert runner.stats.state == "stopped"


def test_full_run_with_gameplay_agent_enabled(tmp_path):
    game = setup(tmp_path, ["win", "win", "win"], start="bracket")
    runner = make_runner(tmp_path, game, max_runs=1)
    runner.cfg.gameplay.enabled = True
    runner.cfg.gameplay.serve_enabled = False
    reason = runner.run()
    assert "completed 1 run" in reason, reason
    assert runner.stats.runs_won == 1 and runner.stats.matches_won == 3
    assert runner.input.held == set()
