"""Gameplay policy with synthetic detections (no screen needed)."""
from __future__ import annotations

import random

from spikebot.actions import DryRunBackend, InputController
from spikebot.config import BotConfig
from spikebot.control import BotControl
from spikebot.gameplay.agent import GameplayAgent
from spikebot.gameplay.perception import Detections, Ring
from spikebot.gameplay.tracking import ClosingTracker


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make_agent(**gameplay):
    cfg = BotConfig()
    for k, v in gameplay.items():
        setattr(cfg.gameplay, k, v)
    backend = DryRunBackend()
    control = BotControl()
    inp = InputController(backend, cfg.humanize, control, random.Random(0))
    clock = Clock()
    agent = GameplayAgent(cfg, library=None, inp=inp, control=control, clock=clock)
    return agent, backend, clock, cfg


def det(t, phase="defense", player=(500, 600), net_x=960, floor=None, air=None, shadow=None):
    return Detections(t=t, phase=phase, player=player, net_x=net_x, floor_ring=floor,
                      air_ring=air, shadow=shadow)


def test_runs_under_landing_ring_then_bumps():
    agent, be, clock, cfg = make_agent()
    ring = Ring(700, 745, 50, 22)
    agent.step(det(clock.t, floor=ring), clock.t)
    assert ("key_down", cfg.keys.right) in be.events  # moving right toward the ring
    # arrive under the ring; the ball shadow closes in fast
    clock.t += 0.1
    agent.step(det(clock.t, player=(698, 600), floor=ring, shadow=(400, 768, 20)), clock.t)
    assert ("key_up", cfg.keys.right) in be.events
    assert cfg.keys.bump not in be.taps()
    for i, sx in enumerate((520, 640)):
        clock.t += 0.05
        agent.step(det(clock.t, player=(698, 600), floor=ring, shadow=(sx, 768, 20)), clock.t)
    assert be.taps().count(cfg.keys.bump) == 1


def test_ignores_landing_ring_on_opponent_side():
    agent, be, clock, cfg = make_agent()
    agent.step(det(clock.t, floor=Ring(1400, 745, 50, 22), shadow=(1390, 768, 20)), clock.t)
    assert be.events == []


def test_receive_zone_limits_chasing():
    agent, be, clock, cfg = make_agent(receive_zone_px=100)
    agent.step(det(clock.t, floor=Ring(800, 745, 50, 22)), clock.t)
    assert be.events == []


def run_frames(agent, clock, seconds, **kw):
    """Step the agent at ~60 fps for ``seconds`` with the same detections."""
    for _ in range(int(seconds / 0.016)):
        clock.t += 0.016
        agent.step(det(clock.t, **kw), clock.t)


def test_attack_jump_then_spike_with_fallback_timings():
    agent, be, clock, cfg = make_agent(jump_delay_ms=300, spike_delay_ms=250)
    ring = Ring(800, 430, 130, 250)
    agent.step(det(clock.t, phase="attack", air=ring), clock.t)
    assert ("key_down", cfg.keys.right) in be.events  # run toward the spike point
    run_frames(agent, clock, 0.25, phase="attack", player=(780, 600), air=ring)
    assert be.taps().count(cfg.keys.jump_spike) == 0  # not yet
    run_frames(agent, clock, 0.1, phase="attack", player=(780, 600), air=ring)
    assert be.taps().count(cfg.keys.jump_spike) == 1  # jump
    run_frames(agent, clock, 0.1, phase="air", player=(780, 500), air=ring)
    assert be.taps().count(cfg.keys.jump_spike) == 1  # too early to spike
    run_frames(agent, clock, 0.2, phase="air", player=(780, 500), air=ring)
    assert be.taps().count(cfg.keys.jump_spike) == 2  # spike
    run_frames(agent, clock, 0.3, phase="air", player=(780, 500), air=ring)
    assert be.taps().count(cfg.keys.jump_spike) == 2  # only one spike per attack
    # back to defense resets the attack state
    run_frames(agent, clock, 0.1, phase="defense")
    assert agent.jumped_at is None and agent.spiked is False


def test_spike_when_ball_reaches_ring():
    agent, be, clock, cfg = make_agent(spike_delay_ms=5000)
    ring = Ring(800, 430, 130, 250)
    agent.step(det(clock.t, phase="air", player=(780, 500), air=ring, shadow=(500, 768, 20)), clock.t)
    assert cfg.keys.jump_spike not in be.taps()
    clock.t += 0.05
    agent.step(det(clock.t, phase="air", player=(780, 500), air=ring, shadow=(790, 768, 20)), clock.t)
    assert be.taps() == [cfg.keys.jump_spike]


def test_serves_after_idle():
    agent, be, clock, cfg = make_agent(serve_idle_s=1.0, serve_second_press_ms=0)
    agent.step(det(clock.t, phase="unknown"), clock.t)
    clock.t += 0.5
    agent.step(det(clock.t, phase="unknown"), clock.t)
    assert be.taps() == []
    clock.t += 0.6
    agent.step(det(clock.t, phase="unknown"), clock.t)
    assert be.taps() == [cfg.keys.serve]
    clock.t += 1.2  # no double serve right after
    agent.step(det(clock.t, phase="unknown"), clock.t)
    assert be.taps() == [cfg.keys.serve]


def test_closing_tracker():
    tr = ClosingTracker()
    for i in range(5):
        tr.update(i * 0.05, 300 - i * 50)  # closing at 1000 px/s
    assert abs(tr.time_to_zero() - 0.1) < 1e-6
    tr.clear()
    for i in range(5):
        tr.update(i * 0.05, 100 + i * 10)  # moving away
    assert tr.time_to_zero() is None


def test_play_loop_on_real_frames_with_live_view():
    """Full play() loop: real screenshots -> perception -> policy -> preview images."""
    from pathlib import Path

    import cv2

    from spikebot.vision import Frame, TemplateLibrary

    root = Path(__file__).resolve().parents[1]
    lib = TemplateLibrary(root / "templates")
    cfg = BotConfig()
    cfg.gameplay.max_fps = 1000
    backend = DryRunBackend()
    control = BotControl()
    inp = InputController(backend, cfg.humanize, control, random.Random(0))
    agent = GameplayAgent(cfg, lib, inp, control)
    previews = []
    agent.on_preview = previews.append
    img = cv2.imread(str(Path(__file__).parent / "fixtures" / "match_defense_rings.jpg"))
    frames = iter(range(6))

    def grab():
        next(frames, None)
        return Frame(img)

    calls = {"n": 0}

    def in_match(_frame):
        calls["n"] += 1
        return calls["n"] <= 5

    assert agent.play(grab, in_match) == "match screen left"
    assert len(previews) == 5 and previews[0].shape[1] == 960
    assert inp.held == set()  # nothing left pressed


def test_keeps_moving_after_pause_released_keys():
    agent, be, clock, cfg = make_agent()
    ring = Ring(700, 745, 50, 22)
    agent.step(det(clock.t, floor=ring), clock.t)
    assert cfg.keys.right in agent.inp.held
    agent.inp.release_all()  # what Pause does
    clock.t += 0.02
    agent.step(det(clock.t, floor=ring), clock.t)
    assert cfg.keys.right in agent.inp.held
    assert be.events.count(("key_down", cfg.keys.right)) == 2
