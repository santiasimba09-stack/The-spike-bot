"""Phase-1 match player.

A rule-based controller driven by what ``Perception`` sees:

* defense (HUD shows "Jump"): if the landing ring is on our side and in our
  zone, run under it and press Bump just before the ball arrives
* attack (HUD shows "Spike"): run to the spike ring (white circle in the air),
  jump ahead of the ball, press Spike when the ball reaches the ring
* serve: when nothing is happening for a while, press the serve key

Timing estimates come from the ball's floor shadow closing in on the ring;
when the shadow can't be tracked, configurable fixed delays are used. All
numbers live in ``GameplayConfig`` so they can be tuned from the UI using the
live view / recordings.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from ..config import BotConfig
from ..control import BotControl
from ..vision import Frame, TemplateLibrary
from .overlay import annotate
from .perception import Detections, Perception
from .tracking import ClosingTracker

log = logging.getLogger(__name__)

TAP_S = 0.05  # key press length for timing-critical actions
NO_RING_ATTACK_FALLBACK_S = 1.2
RING_MEMORY_S = 0.5


class GameplayAgent:
    def __init__(self, cfg: BotConfig, library: TemplateLibrary, inp, control: BotControl,
                 log_dir: str | Path = "logs", clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.g = cfg.gameplay
        self.k = cfg.keys
        self.inp = inp
        self.control = control
        self.clock = clock
        self.perception = Perception(self.g, library)
        self.log_dir = Path(log_dir)
        self.on_preview: Callable | None = None  # set by the UI for the live view
        self._cv_window = False
        self._recorder = None
        self.reset()

    # ----------------------------------------------------------------- state
    def reset(self) -> None:
        self.phase = "unknown"
        self.phase_since = self.clock()
        self.moving = ""
        self.last_bump = -1e9
        self.jumped_at: float | None = None
        self.spiked = False
        self.air_ring = None
        self.air_ring_since: float | None = None
        self.air_ring_seen = -1e9
        self.idle_since: float | None = None
        self.last_serve = -1e9
        self.recv = ClosingTracker()
        self.toss = ClosingTracker()
        self.notes: list[str] = []
        self.actions_total = 0

    def close(self) -> None:
        self._stop_recording()
        if self._cv_window:
            try:
                import cv2

                cv2.destroyWindow("SpikeBot live view")
            except Exception:
                pass
            self._cv_window = False

    # ------------------------------------------------------------ main loop
    def play(self, grab: Callable[[], Frame], in_match: Callable[[Frame], bool]) -> str:
        """Play until ``in_match`` says the match is over. Returns the reason."""
        self.reset()
        if self.g.record_frames:
            self._start_recording()
        period = 1.0 / max(5, self.g.max_fps)
        fps_t, fps_n, fps = self.clock(), 0, 0.0
        try:
            while True:
                self.control.check()
                t0 = self.clock()
                frame = grab()
                if not in_match(frame):
                    return "match screen left"
                det = self.perception.detect(frame)
                actions = self.step(det, t0)
                fps_n += 1
                if t0 - fps_t >= 1.0:
                    fps, fps_n, fps_t = fps_n / (t0 - fps_t), 0, t0
                self._publish(frame, det, actions, fps)
                left = period - (self.clock() - t0)
                if left > 0:
                    self.control.sleep(left)
        finally:
            self._release_moves()
            self._stop_recording()

    # ---------------------------------------------------------------- policy
    def step(self, det: Detections, now: float) -> list[str]:
        """Decide and send input for one frame. Returns a list of actions taken."""
        acts: list[str] = []
        self.notes = []
        if det.phase != self.phase:
            if det.phase in ("defense", "unknown") and self.phase in ("attack", "air"):
                self.jumped_at, self.spiked = None, False
            self.phase, self.phase_since = det.phase, now
        if det.air_ring is not None:
            if self.air_ring_since is None or now - self.air_ring_seen > RING_MEMORY_S:
                self.air_ring_since = now
                self.toss.clear()
            self.air_ring, self.air_ring_seen = det.air_ring, now
        elif now - self.air_ring_seen > RING_MEMORY_S:
            self.air_ring, self.air_ring_since = None, None

        if det.phase in ("attack", "air") and self.g.attack_enabled:
            self._attack(det, now, acts)
        elif det.phase == "defense":
            self._defend(det, now, acts)
        else:
            self._release_moves()
            self._maybe_serve(det, now, acts)
        if det.phase != "unknown" or det.floor_ring is not None:
            self.idle_since = None
        self.actions_total += len(acts)
        return acts

    # defense -------------------------------------------------------------
    def _on_our_side(self, x: float, det: Detections) -> bool:
        if det.net_x is None:
            return True
        return x < det.net_x if self.g.our_side == "left" else x > det.net_x

    def _defend(self, det: Detections, now: float, acts: list[str]) -> None:
        ring, me = det.floor_ring, det.player
        if ring is None or me is None:
            self.recv.clear()
            self._release_moves()
            return
        zone = self.g.receive_zone_px
        if not self._on_our_side(ring.x, det) or (zone and abs(ring.x - me[0]) > zone):
            self.notes.append("ring not ours")
            self.recv.clear()
            self._release_moves()
            return
        self._move_toward(ring.x, me[0], acts)
        ttl = None
        if det.shadow is not None:
            self.recv.update(now, ring.x - det.shadow[0])
            ttl = self.recv.time_to_zero()
        self.notes.append(f"ttl {ttl * 1000:.0f}ms" if ttl is not None else "ttl ?")
        close_shadow = det.shadow is not None and abs(det.shadow[0] - me[0]) <= self.g.bump_distance_px
        under_ring = abs(ring.x - me[0]) <= max(self.g.move_tolerance_px * 2, ring.w / 2)
        due = (ttl is not None and ttl * 1000 <= self.g.bump_lead_ms) or close_shadow
        if due and under_ring and (now - self.last_bump) * 1000 >= self.g.bump_cooldown_ms:
            self._tap(self.k.bump, "bump", acts)
            self.last_bump = now
            self.recv.clear()

    # attack --------------------------------------------------------------
    def _attack(self, det: Detections, now: float, acts: list[str]) -> None:
        me = det.player
        ring = self.air_ring
        if self.jumped_at is None and det.phase == "air":
            self.jumped_at = now  # already airborne (HUD shows Cancel/Spike)
        if self.jumped_at is None:
            if ring is not None and me is not None:
                off = self.g.spike_ring_offset_px
                target = ring.x + (off if self.g.our_side == "left" else -off)
                self._move_toward(target, me[0], acts)
            tta = None
            if ring is not None and det.shadow is not None:
                self.toss.update(now, ring.x - det.shadow[0])
                tta = self.toss.time_to_zero()
            self.notes.append(f"tta {tta * 1000:.0f}ms" if tta is not None else "tta ?")
            jump = False
            if ring is not None:
                since = now - (self.air_ring_since or now)
                jump = (tta is not None and tta * 1000 <= self.g.jump_lead_ms) or \
                       (tta is None and since * 1000 >= self.g.jump_delay_ms)
            elif now - self.phase_since >= NO_RING_ATTACK_FALLBACK_S:
                jump = True
                self.notes.append("no ring - fallback jump")
            if jump and det.phase == "attack":
                self._release_moves()
                self._tap(self.k.jump_spike, "jump", acts)
                self.jumped_at = now
            return
        if not self.spiked:
            inside = False
            if ring is not None and det.shadow is not None:
                inside = abs(det.shadow[0] - ring.x) <= ring.w * self.g.spike_ring_radius_frac
            late = (now - self.jumped_at) * 1000 >= self.g.spike_delay_ms
            if inside or late:
                self._tap(self.k.jump_spike, "spike", acts)
                self.spiked = True

    # serve ---------------------------------------------------------------
    def _maybe_serve(self, det: Detections, now: float, acts: list[str]) -> None:
        if not self.g.serve_enabled or det.floor_ring is not None or det.air_ring is not None:
            self.idle_since = None
            return
        if self.idle_since is None:
            self.idle_since = now
            return
        idle = now - self.idle_since
        if idle >= self.g.serve_idle_s and now - self.last_serve >= self.g.serve_idle_s + 2.0:
            hold = self.g.serve_hold_ms / 1000.0 if self.g.serve_hold_ms > 0 else TAP_S
            self._tap(self.k.serve, "serve", acts, hold)
            if self.g.serve_second_press_ms > 0:
                self.control.sleep(self.g.serve_second_press_ms / 1000.0)
                self._tap(self.k.serve, "serve hit", acts)
            self.last_serve = self.clock()
            self.idle_since = None

    # input helpers ---------------------------------------------------------
    def _tap(self, key: str, label: str, acts: list[str], hold: float = TAP_S) -> None:
        if not key:
            return
        self.inp.tap(key, hold_s=hold)
        acts.append(label)
        log.debug("gameplay: %s (%s)", label, key)

    def _move_toward(self, target_x: float, my_x: float, acts: list[str]) -> None:
        dx = target_x - my_x
        if abs(dx) <= self.g.move_tolerance_px:
            self._release_moves()
            return
        want = self.k.right if dx > 0 else self.k.left
        other = self.k.left if dx > 0 else self.k.right
        # Use the controller's real key state: a pause releases every key.
        if want not in self.inp.held:
            self.inp.key_up(other)
            self.inp.key_down(want)
            acts.append(f"move {'right' if dx > 0 else 'left'}")
        self.moving = want

    def _release_moves(self) -> None:
        if self.k.left in self.inp.held or self.k.right in self.inp.held:
            self.inp.key_up(self.k.left)
            self.inp.key_up(self.k.right)
        self.moving = ""

    # preview / recording ---------------------------------------------------
    def _publish(self, frame: Frame, det: Detections, actions: list[str], fps: float) -> None:
        want_view = self.on_preview is not None or self.g.debug_overlay
        if not want_view and self._recorder is None:
            return
        lines = [f"fps {fps:.0f}"] + self.notes + ([("> " + ", ".join(actions))] if actions else [])
        if self._recorder is not None:
            self._recorder.add(frame.image, {"t": det.t, "det": det.to_json(), "actions": actions,
                                             "notes": list(self.notes)})
        if not want_view:
            return
        img = annotate(frame.image, det, lines)
        if self.on_preview is not None:
            self.on_preview(img)
        elif self.g.debug_overlay:
            try:
                import cv2

                cv2.imshow("SpikeBot live view", img)
                cv2.waitKey(1)
                self._cv_window = True
            except Exception as exc:
                log.warning("Live view unavailable (%s) - disabling it", exc)
                self.g.debug_overlay = False

    def _start_recording(self) -> None:
        from .recorder import Recorder

        self._recorder = Recorder(self.log_dir.parent / "recordings", self.g.record_every_n,
                                  self.g.record_max_frames, self.g.analysis_width)

    def _stop_recording(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
