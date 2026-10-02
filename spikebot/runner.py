"""The bot's main loop: screen flow, limits, gameplay hand-off, safety."""
from __future__ import annotations

import datetime as dt
import logging
import random
import threading
import time
from pathlib import Path
from typing import Callable

import cv2

from .actions import InputController, make_backend
from .capture import GameNotFound, GameScreen
from .config import BotConfig
from .control import BotControl, StopRequested
from .flow import Decision, FlowEngine, load_rules
from .platform import set_dpi_awareness
from .stats import RunStats, count_limit_reason, time_limit_reason
from .vision import Frame, TemplateLibrary

log = logging.getLogger(__name__)

IN_MATCH_TEMPLATE = "hud_pause"


class NotForeground(Exception):
    """The game window doesn't have focus, so no input may be sent."""


class MatchWatcher:
    """Answers 'are we still in the match?' with a grace period for cut-ins/replays."""

    def __init__(self, library: TemplateLibrary, grace_s: float = 3.0,
                 clock: Callable[[], float] = time.monotonic):
        self.library = library
        self.grace_s = grace_s
        self.clock = clock
        self.last_seen = clock()

    def __call__(self, frame: Frame) -> bool:
        if self.library.find(frame, IN_MATCH_TEMPLATE) is not None:
            self.last_seen = self.clock()
            return True
        return self.clock() - self.last_seen < self.grace_s


class BotRunner:
    def __init__(self, cfg: BotConfig, control: BotControl | None = None, *,
                 base_dir: str | Path = ".", source=None, backend=None,
                 on_status: Callable[[dict], None] | None = None,
                 rng: random.Random | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.control = control or BotControl()
        self.base_dir = Path(base_dir)
        self._source = source
        self._backend = backend
        self.on_status = on_status
        self.rng = rng or random.Random()
        self.clock = clock
        self.stats = RunStats()
        self.stop_reason = ""
        self.screen = None
        self.library: TemplateLibrary | None = None
        self.engine: FlowEngine | None = None
        self.input: InputController | None = None
        self.agent = None
        self.preview_cb: Callable | None = None  # live view (set by the UI)
        self._last_status = 0.0
        self._pending_stop = ""
        self._escaped_once = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- threads
    def start_thread(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.run, name="SpikeBot", daemon=True)
        self._thread.start()
        return self._thread

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -------------------------------------------------------------- status
    def _emit(self, force: bool = False) -> None:
        if self.on_status is None:
            return
        now = self.clock()
        if not force and now - self._last_status < 0.4:
            return
        self._last_status = now
        d = self.stats.as_dict()
        d["paused"] = self.control.paused
        d["stop_reason"] = self.stop_reason
        d["pending_stop"] = self._pending_stop
        d["max_runs"] = self.cfg.limits.max_runs
        try:
            self.on_status(d)
        except Exception:
            log.exception("status callback failed")

    def _set_state(self, state: str) -> None:
        if state != self.stats.state:
            log.debug("state -> %s", state)
            self.stats.state = state
            self._emit(force=True)

    # --------------------------------------------------------------- setup
    def _setup(self) -> None:
        set_dpi_awareness()
        cfg = self.cfg
        problems = cfg.validate()
        if problems:
            raise ValueError("Invalid config:\n  " + "\n  ".join(problems))
        self.screen = self._source or GameScreen(cfg.window)
        self.library = TemplateLibrary(self.base_dir / cfg.templates_dir,
                                       cfg.matching.default_threshold, cfg.matching.scales)
        rules = load_rules(self.base_dir / cfg.flow_file)
        self.engine = FlowEngine(rules, self.library, cfg)
        missing = self.engine.missing_templates()
        if missing:
            log.debug("Rules waiting for templates: %s",
                      "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items()))
        from .template_catalog import CATALOG

        need = sorted(n for n, i in CATALOG.items() if i.required and not self.library.has(n))
        if need:
            log.warning("Missing required templates (capture them in the UI's Templates tab): %s",
                        ", ".join(need))
        if not self.library.has(IN_MATCH_TEMPLATE):
            log.warning("Template '%s' is missing - matches can't be detected!", IN_MATCH_TEMPLATE)
        backend = self._backend or make_backend(cfg.dry_run)
        self.input = InputController(backend, cfg.humanize, self.control, self.rng,
                                     guard=self._guard)
        if cfg.gameplay.enabled:
            from .gameplay.agent import GameplayAgent

            self.agent = GameplayAgent(cfg, self.library, self.input, self.control,
                                       log_dir=self.base_dir / cfg.log_dir)
            self.agent.on_preview = self.preview_cb
        # Bring the game to the front so input is allowed right away.
        try:
            self.screen.locate(force=True)
            if cfg.window.require_foreground and not self.screen.is_foreground():
                self.screen.focus()
        except GameNotFound as exc:
            log.warning("%s - waiting for it", exc)

    def _guard(self) -> None:
        if self.cfg.window.require_foreground and not self.screen.is_foreground():
            raise NotForeground()

    # ---------------------------------------------------------------- main
    def run(self) -> str:
        self.stats = RunStats()
        self.stop_reason = ""
        self._set_state("starting")
        try:
            self._setup()
            log.info("Bot started (max runs: %s, dry run: %s). Emergency stop: %s",
                     self.cfg.limits.max_runs or "unlimited", self.cfg.dry_run,
                     self.cfg.hotkeys.stop.upper())
            self._loop()
        except StopRequested as exc:
            self.stop_reason = str(exc) or self.control.stop_reason or "stopped"
        except Exception as exc:
            log.exception("Bot crashed")
            self.stop_reason = f"error: {exc}"
            self._save_screenshot("error")
        finally:
            if self.input is not None:
                self.input.release_all()
            if self.screen is not None:
                try:
                    self.screen.close()
                except Exception:
                    pass
            if self.agent is not None:
                self.agent.close()
            self.stats.state = "stopped"
            log.info("Bot stopped: %s | runs %d (won %d, lost %d), matches %d (W%d/L%d), %.1f min",
                     self.stop_reason, self.stats.runs_completed, self.stats.runs_won,
                     self.stats.runs_lost, self.stats.matches_played, self.stats.matches_won,
                     self.stats.matches_lost, self.stats.elapsed_s / 60)
            self._emit(force=True)
        return self.stop_reason

    def stop(self, reason: str) -> None:
        log.info("Stopping: %s", reason)
        self.control.request_stop(reason)
        raise StopRequested(reason)

    def _loop(self) -> None:
        cfg = self.cfg
        poll = cfg.matching.poll_interval_s
        last_known = self.clock()
        while True:
            self.control.check()
            self._emit()
            self._check_time_limits()
            if self._pending_stop and not self.stats.run_active:
                self.stop(self._pending_stop)
            try:
                frame = self.screen.grab()
            except GameNotFound as exc:
                self._set_state("waiting for game window")
                if self.clock() - last_known > cfg.safety.stuck_timeout_s:
                    self.stop(f"game window not found: {exc}")
                self.control.sleep(2.0)
                continue
            except StopIteration:
                self.stop("image source exhausted")

            decision = self.engine.evaluate(frame, self.clock())
            if decision is None:
                self._set_state("unknown screen")
                if self.clock() - last_known > cfg.safety.stuck_timeout_s:
                    if self._handle_stuck(frame):
                        last_known = self.clock()
                self.control.sleep(poll)
                continue
            last_known = self.clock()
            self._escaped_once = False
            if decision.cooling:
                self.control.sleep(poll)
                continue
            try:
                self._execute(decision, frame)
            except NotForeground:
                self.input.release_all()
                self._set_state("waiting for game focus")
                self.control.sleep(1.0)
                last_known = self.clock()

    # ------------------------------------------------------------- actions
    def _execute(self, d: Decision, frame: Frame) -> None:
        rule = d.rule
        self._set_state(rule.name)
        if d.fire_events and rule.event:
            for ev in rule.event:
                self._on_event(ev)
        if rule.action == "stop":
            self._save_screenshot(f"safety_{rule.name}")
            self.stop(f"safety rule '{rule.name}' matched "
                      f"({', '.join(d.matches) or 'template visible'})")
        if rule.action == "play":
            self._play_match()
            self.engine.mark_fired(rule, self.clock())
            return
        if rule.action in ("wait", "none"):
            self.engine.mark_fired(rule, self.clock())
            self.control.sleep(rule.settle_s)
            return

        self._guard()
        self.input.human_wait()
        if rule.action == "click":
            self._click(d.target, fallback_key=d.key)
        elif rule.action == "key":
            if d.key:
                self._tap(d.key)
            else:
                self._click(d.target)
        elif rule.action == "steps":
            for step in rule.steps:
                if step.click:
                    name = self.engine.substitute(step.click)
                    if not self._click(name) and not step.optional:
                        log.warning("Rule %s: step target '%s' not visible", rule.name, name)
                        break
                elif step.key:
                    self._tap(self.engine.substitute(step.key))
                if step.wait_s:
                    self.control.sleep(step.wait_s)
        self.engine.mark_fired(rule, self.clock())
        self.control.sleep(rule.settle_s)

    def _click(self, target: str, fallback_key: str = "") -> bool:
        m = None
        frame = None
        if target and self.library.has(target):
            frame = self.screen.grab()
            m = self.library.find(frame, target)
            if m is None and not self.cfg.safety.verify_before_click:
                m = self.library.best(frame, target)
        if m is not None:
            self.input.click_match(frame, m)
            self.stats.last_action = f"click {target} ({m.score:.2f})"
            log.info("Click %s (score %.2f)", target, m.score)
            return True
        if fallback_key:
            self._tap(fallback_key)
            return True
        log.debug("Nothing to click for '%s'", target)
        return False

    def _tap(self, key: str) -> None:
        self.input.tap(key)
        self.stats.last_action = f"key {key}"
        log.info("Press %s", key)

    def _play_match(self) -> None:
        watcher = MatchWatcher(self.library, self.cfg.matching.match_end_grace_s, clock=self.clock)
        started = self.clock()
        limit_s = self.cfg.safety.max_match_minutes * 60

        def in_match(frame: Frame) -> bool:
            if limit_s > 0 and self.clock() - started > limit_s:
                self._save_screenshot("match_too_long")
                self.stop(f"match lasted more than {self.cfg.safety.max_match_minutes:g} min")
            self._emit()
            return watcher(frame)

        if self.agent is not None:
            self._set_state("in_match (bot playing)")
            reason = self.agent.play(self.screen.grab, in_match)
            log.info("Match phase ended: %s", reason)
        else:
            self._manual_match(in_match)
        self.input.release_all()

    def _manual_match(self, in_match: Callable[[Frame], bool]) -> None:
        """You play; the bot waits (and records frames + your game keys if enabled)."""
        self._set_state("in_match (you play)")
        g = self.cfg.gameplay
        rec = perception = None
        if g.record_frames:
            from dataclasses import asdict

            from .gameplay.perception import Perception
            from .gameplay.recorder import Recorder

            perception = Perception(g, self.library)
            rec = Recorder(self.base_dir / "recordings", g.record_every_n, g.record_max_frames,
                           g.analysis_width, watch_keys=set(asdict(self.cfg.keys).values()))
        try:
            while True:
                self.control.sleep(0.5 if rec is None else 1.0 / max(5, g.max_fps))
                frame = self.screen.grab()
                if not in_match(frame):
                    break
                if rec is not None:
                    det = perception.detect(frame)
                    rec.add(frame.image, {"t": det.t, "det": det.to_json(), "actions": []})
        finally:
            if rec is not None:
                rec.close()

    # -------------------------------------------------------------- events
    def _on_event(self, ev: str) -> None:
        s = self.stats
        lim = self.cfg.limits
        if ev == "match_started":
            s.on_match_started()
            log.info("Match started (run %d, match %d)", s.runs_completed + 1, s.run_matches)
        elif ev == "match_won":
            s.on_match_won()
            log.info("Match WON (%d/%d this run)", s.run_wins,
                     self.cfg.tournament.matches_per_tournament)
            if s.run_wins >= self.cfg.tournament.matches_per_tournament:
                self._end_run(won=True)
        elif ev == "match_lost":
            s.on_match_lost()
            log.info("Match LOST (%d in a row)", s.consecutive_losses)
            self._end_run(won=False)
        elif ev == "tournament_won":
            if s.run_active or s.run_wins:
                self._end_run(won=True)
        # Count limits are checked on result screens, i.e. between matches.
        reason = count_limit_reason(lim, s)
        if reason:
            self._set_state("limit reached")
            self.stop(reason)

    def _end_run(self, won: bool) -> None:
        s = self.stats
        s.end_run(won)
        log.info("Run %d finished: %s (total: %d won, %d lost)", s.runs_completed,
                 "TOURNAMENT WON" if won else "eliminated", s.runs_won, s.runs_lost)
        t = self.cfg.tournament
        if ((won and t.reset_after_win) or (not won and t.reset_after_loss)) \
                and self.library.has("bracket_reset"):
            self.engine.flags.add("needs_reset")

    # ---------------------------------------------------------- limits/etc
    def _check_time_limits(self) -> None:
        if self._pending_stop:
            return
        reason = time_limit_reason(self.cfg.limits, self.stats)
        if not reason:
            return
        if self.cfg.limits.finish_current_run and self.stats.run_active:
            self._pending_stop = reason
            log.info("%s - finishing the current run first", reason)
            self._emit(force=True)
        else:
            self.stop(reason)

    def _handle_stuck(self, frame: Frame) -> bool:
        """Returns True if a recovery was attempted (timer should restart)."""
        self._save_screenshot("stuck", frame)
        if self.cfg.safety.on_stuck == "escape" and not self._escaped_once:
            self._escaped_once = True
            log.warning("Unknown screen for %.0fs - pressing Esc once",
                        self.cfg.safety.stuck_timeout_s)
            try:
                self._guard()
                self.input.tap("esc")
            except NotForeground:
                pass
            return True
        self.stop(f"stuck on an unknown screen for {self.cfg.safety.stuck_timeout_s:.0f}s "
                  "(screenshot saved in logs/screens)")
        return False

    def _save_screenshot(self, reason: str, frame: Frame | None = None) -> None:
        if not self.cfg.safety.save_debug_screenshots:
            return
        try:
            if frame is None:
                if self.screen is None:
                    return
                frame = self.screen.grab()
            d = self.base_dir / self.cfg.log_dir / "screens"
            d.mkdir(parents=True, exist_ok=True)
            name = f"{dt.datetime.now():%Y%m%d_%H%M%S}_{reason}.png"
            cv2.imwrite(str(d / name), frame.image)
            log.info("Saved screenshot %s", d / name)
        except Exception as exc:
            log.debug("Could not save screenshot: %s", exc)
