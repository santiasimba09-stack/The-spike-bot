"""Bot configuration: typed dataclasses with YAML load/save.

All pixel values in the gameplay section are expressed for a 1920-px-wide game
window and scaled automatically to the real window size.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

TIERS = ("keep", "silver", "gold", "diamond", "master")
ON_STUCK = ("stop", "escape")
SIDES = ("left", "right")


@dataclass
class WindowConfig:
    title_contains: str = "The Spike"
    """Case-insensitive substring of the game window title."""
    process_name: str = ""
    """Optional exe name (e.g. ``TheSpikeCross.exe``) to avoid matching browser tabs."""
    require_foreground: bool = True
    """Never send input unless the game window has focus."""
    allow_monitor_fallback: bool = False
    """If the window can't be found, use the whole monitor below instead of stopping."""
    fallback_monitor: int = 1


@dataclass
class LimitsConfig:
    max_runs: int = 10
    """Tournament runs to complete before stopping (0 = unlimited)."""
    max_matches: int = 0
    """Matches to play before stopping (0 = unlimited)."""
    max_minutes: float = 0.0
    """Stop after this many minutes (0 = unlimited)."""
    stop_at: str = ""
    """Local wall-clock time ``HH:MM`` to stop at (empty = off)."""
    max_consecutive_losses: int = 3
    """Stop after this many lost matches in a row (0 = never)."""
    finish_current_run: bool = True
    """When a time limit hits mid-run, finish the run before stopping."""


@dataclass
class TournamentConfig:
    tier: str = "keep"
    """Tier to pick when the tournament is reset: keep, silver, gold, diamond, master."""
    auto_claim: bool = True
    """Click 'Claim Reward' whenever it shows up."""
    reset_after_win: bool = True
    reset_after_loss: bool = True
    matches_per_tournament: int = 3
    """Wins needed to finish a tournament (quarterfinal, semifinal, final)."""
    continue_key: str = "space"
    """Key for 'SPACE Continue' prompts. Empty = click the prompt instead."""
    skip_cutscenes: bool = True


@dataclass
class SafetyConfig:
    stuck_timeout_s: float = 120.0
    """Seconds on an unrecognised screen before reacting."""
    on_stuck: str = "stop"
    """stop = stop the bot, escape = press Esc once and keep waiting."""
    max_match_minutes: float = 25.0
    stop_on_danger: bool = True
    """Stop immediately if a 'danger_*' template (shop / purchase dialog) is visible."""
    save_debug_screenshots: bool = True
    verify_before_click: bool = True
    """Re-check a button is still there right before clicking it."""


@dataclass
class HumanizeConfig:
    delay_min_s: float = 0.4
    delay_max_s: float = 1.5
    click_jitter: float = 0.3
    """0..1: how far from the button centre a click may land (fraction of half-size)."""
    move_min_s: float = 0.12
    move_max_s: float = 0.35
    curved_moves: bool = True
    press_min_s: float = 0.04
    press_max_s: float = 0.11


@dataclass
class HotkeysConfig:
    stop: str = "f8"
    pause: str = "f7"
    screenshot: str = "f9"


@dataclass
class MatchingConfig:
    default_threshold: float = 0.82
    poll_interval_s: float = 0.35
    scales: list[float] = field(default_factory=lambda: [1.0])
    """Extra relative scales to try (e.g. [0.95, 1.0, 1.05]) if buttons are missed."""
    match_end_grace_s: float = 3.0
    """The match counts as over once the in-match HUD is gone for this long (cut-ins hide it)."""


@dataclass
class KeysConfig:
    """Keyboard binds - must match the game's Control Settings."""

    left: str = "left"
    right: str = "right"
    jump_spike: str = "z"
    """Bottom HUD slot: Jump when defending, Spike when attacking/in the air."""
    bump: str = "space"
    """Third HUD slot: Bump (receive) / Feint."""
    block: str = "x"
    """Second HUD slot: Block / Feint."""
    slide: str = "c"
    """Top HUD slot: Slide (dive) / Cancel."""
    serve: str = "z"


@dataclass
class GameplayConfig:
    enabled: bool = True
    """False = the bot only drives menus and waits while you play the match yourself."""
    our_side: str = "left"
    receive_zone_px: int = 450
    """Only chase landing spots within this distance of our player (0 = anywhere on our side)."""
    move_tolerance_px: int = 14
    bump_lead_ms: int = 170
    """Press Bump when the ball is estimated to land within this many ms."""
    bump_distance_px: int = 70
    """Fallback: press Bump when the ball shadow is this close to our player."""
    bump_cooldown_ms: int = 700
    attack_enabled: bool = True
    spike_ring_offset_px: int = -25
    """Where to stand relative to the spike ring centre (negative = toward our baseline)."""
    jump_lead_ms: int = 430
    """Jump this long before the ball reaches the spike ring."""
    jump_delay_ms: int = 300
    """Fallback when the ball can't be tracked: jump this long after the spike ring appears."""
    spike_delay_ms: int = 280
    """Fallback: press Spike this long after jumping."""
    spike_ring_radius_frac: float = 0.55
    """Press Spike when the ball is within this fraction of the ring size from its centre."""
    serve_enabled: bool = True
    serve_hold_ms: int = 0
    serve_second_press_ms: int = 650
    """Press serve again after this delay (jump serve). 0 = single press."""
    serve_idle_s: float = 3.0
    """With no HUD labels and no ball for this long, assume it's our serve."""
    block_enabled: bool = False
    analysis_width: int = 960
    max_fps: int = 60
    marker_hsv_low: list[int] = field(default_factory=lambda: [92, 60, 200])
    marker_hsv_high: list[int] = field(default_factory=lambda: [110, 150, 255])
    debug_overlay: bool = False
    record_frames: bool = False
    record_every_n: int = 3
    record_max_frames: int = 3000


@dataclass
class BotConfig:
    window: WindowConfig = field(default_factory=WindowConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    tournament: TournamentConfig = field(default_factory=TournamentConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    keys: KeysConfig = field(default_factory=KeysConfig)
    gameplay: GameplayConfig = field(default_factory=GameplayConfig)
    templates_dir: str = "templates"
    flow_file: str = "flow.yaml"
    log_dir: str = "logs"
    dry_run: bool = False
    """Log actions instead of sending real input."""

    # ------------------------------------------------------------------ io
    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BotConfig":
        return _from_dict(cls, data or {}, "")

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty = OK)."""
        p: list[str] = []
        lim = self.limits
        for name in ("max_runs", "max_matches", "max_consecutive_losses"):
            if getattr(lim, name) < 0:
                p.append(f"limits.{name} must be >= 0")
        if lim.max_minutes < 0:
            p.append("limits.max_minutes must be >= 0")
        if lim.stop_at and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", lim.stop_at.strip()):
            p.append("limits.stop_at must look like HH:MM (24h) or be empty")
        if self.tournament.tier not in TIERS:
            p.append(f"tournament.tier must be one of {', '.join(TIERS)}")
        if self.tournament.matches_per_tournament < 1:
            p.append("tournament.matches_per_tournament must be >= 1")
        if self.safety.on_stuck not in ON_STUCK:
            p.append(f"safety.on_stuck must be one of {', '.join(ON_STUCK)}")
        h = self.humanize
        if h.delay_min_s < 0 or h.delay_max_s < h.delay_min_s:
            p.append("humanize: need 0 <= delay_min_s <= delay_max_s")
        if h.move_min_s < 0 or h.move_max_s < h.move_min_s:
            p.append("humanize: need 0 <= move_min_s <= move_max_s")
        if h.press_min_s < 0 or h.press_max_s < h.press_min_s:
            p.append("humanize: need 0 <= press_min_s <= press_max_s")
        if not 0 <= h.click_jitter <= 1:
            p.append("humanize.click_jitter must be between 0 and 1")
        if not 0 < self.matching.default_threshold <= 1:
            p.append("matching.default_threshold must be in (0, 1]")
        if self.matching.match_end_grace_s < 0:
            p.append("matching.match_end_grace_s must be >= 0")
        if not self.matching.scales or any(s <= 0 for s in self.matching.scales):
            p.append("matching.scales must be a non-empty list of positive numbers")
        g = self.gameplay
        if g.our_side not in SIDES:
            p.append("gameplay.our_side must be left or right")
        if g.analysis_width < 320:
            p.append("gameplay.analysis_width must be >= 320")
        if g.max_fps < 5:
            p.append("gameplay.max_fps must be >= 5")
        hk = [self.hotkeys.stop, self.hotkeys.pause, self.hotkeys.screenshot]
        if len({k.lower() for k in hk if k}) != len([k for k in hk if k]):
            p.append("hotkeys must be different from each other")
        if not self.hotkeys.stop:
            p.append("hotkeys.stop must be set (emergency stop)")
        from .keys import is_valid_key

        for name, value in dataclasses.asdict(self.keys).items():
            if value and not is_valid_key(value):
                p.append(f"keys.{name}: unknown key '{value}'")
        if self.tournament.continue_key and not is_valid_key(self.tournament.continue_key):
            p.append(f"tournament.continue_key: unknown key '{self.tournament.continue_key}'")
        for name, value in dataclasses.asdict(self.hotkeys).items():
            if value and not is_valid_key(value):
                p.append(f"hotkeys.{name}: unknown key '{value}'")
        return p


def _from_dict(cls, data: dict[str, Any], prefix: str):
    kwargs = {}
    fields = {f.name for f in dataclasses.fields(cls)}
    for key, value in data.items():
        if key not in fields:
            log.warning("Ignoring unknown config key '%s%s'", prefix, key)
            continue
        default = getattr(cls(), key)
        if dataclasses.is_dataclass(default):
            kwargs[key] = _from_dict(type(default), value or {}, f"{prefix}{key}.")
        else:
            kwargs[key] = _coerce(value, default, f"{prefix}{key}")
    return cls(**kwargs)


def _coerce(value: Any, default: Any, name: str) -> Any:
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, str):
            return "" if value is None else str(value)
        if isinstance(default, list):
            if not isinstance(value, (list, tuple)):
                raise TypeError("expected a list")
            if default and isinstance(default[0], (int, float)):
                caster = type(default[0])
                return [caster(v) for v in value]
            return list(value)
    except (TypeError, ValueError) as exc:
        log.warning("Bad value for %s (%r): %s - using default %r", name, value, exc, default)
        return default
    return value


def load_config(path: str | Path) -> BotConfig:
    """Load config from YAML; missing file or keys fall back to defaults."""
    path = Path(path)
    if not path.exists():
        return BotConfig()
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return BotConfig.from_dict(data)


def save_config(cfg: BotConfig, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg.to_dict(), fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)
