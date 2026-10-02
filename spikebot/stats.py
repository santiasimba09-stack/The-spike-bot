"""Run / match counters and limit checks."""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import asdict, dataclass, field

from .config import LimitsConfig


@dataclass
class RunStats:
    started_at: float = field(default_factory=time.time)
    runs_completed: int = 0
    runs_won: int = 0
    runs_lost: int = 0
    matches_played: int = 0
    matches_won: int = 0
    matches_lost: int = 0
    consecutive_losses: int = 0
    run_active: bool = False
    run_wins: int = 0
    run_matches: int = 0
    state: str = "idle"
    last_action: str = ""

    @property
    def elapsed_s(self) -> float:
        return time.time() - self.started_at

    def as_dict(self) -> dict:
        d = asdict(self)
        d["elapsed_s"] = round(self.elapsed_s, 1)
        return d

    # ------------------------------------------------------------- events
    def on_match_started(self) -> None:
        if not self.run_active:
            self.run_active = True
            self.run_wins = 0
            self.run_matches = 0
        self.run_matches += 1

    def on_match_won(self) -> None:
        self.matches_played += 1
        self.matches_won += 1
        self.consecutive_losses = 0
        self.run_active = True
        self.run_wins += 1

    def on_match_lost(self) -> None:
        self.matches_played += 1
        self.matches_lost += 1
        self.consecutive_losses += 1

    def end_run(self, won: bool) -> None:
        self.runs_completed += 1
        if won:
            self.runs_won += 1
        else:
            self.runs_lost += 1
        self.run_active = False
        self.run_wins = 0
        self.run_matches = 0


def _stop_at_reached(stop_at: str, started: dt.datetime, now: dt.datetime) -> bool:
    if not stop_at:
        return False
    hh, mm = (int(x) for x in stop_at.strip().split(":"))
    target = started.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= started:
        target += dt.timedelta(days=1)  # e.g. started 23:00, stop at 02:00
    return now >= target


def time_limit_reason(limits: LimitsConfig, stats: RunStats,
                      now: dt.datetime | None = None) -> str:
    """Reason string if a time-based limit has been reached, else ''."""
    now = now or dt.datetime.now()
    if limits.max_minutes > 0 and stats.elapsed_s >= limits.max_minutes * 60:
        return f"time limit reached ({limits.max_minutes:g} min)"
    started = dt.datetime.fromtimestamp(stats.started_at)
    if _stop_at_reached(limits.stop_at, started, now):
        return f"stop time reached ({limits.stop_at})"
    return ""


def count_limit_reason(limits: LimitsConfig, stats: RunStats) -> str:
    """Reason string if a run/match/loss count limit has been reached, else ''."""
    if limits.max_runs > 0 and stats.runs_completed >= limits.max_runs:
        return f"completed {stats.runs_completed} run(s) (max {limits.max_runs})"
    if limits.max_matches > 0 and stats.matches_played >= limits.max_matches:
        return f"played {stats.matches_played} match(es) (max {limits.max_matches})"
    if limits.max_consecutive_losses > 0 and stats.consecutive_losses >= limits.max_consecutive_losses:
        return f"{stats.consecutive_losses} losses in a row"
    return ""
