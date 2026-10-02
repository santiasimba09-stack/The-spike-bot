"""Data-driven screen flow: ordered rules of 'when these templates are visible, do this'.

The default tournament flow lives in ``flow.yaml`` at the repository root. Rules
are evaluated top to bottom on every poll and the first rule whose conditions
hold is executed. See ``flow.yaml`` for the schema documentation.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import BotConfig
from .vision import Frame, Match, TemplateLibrary

log = logging.getLogger(__name__)

ACTIONS = ("click", "key", "play", "stop", "wait", "none", "steps")
EVENTS = ("match_started", "match_won", "match_lost", "tournament_won")


@dataclass
class Step:
    click: str = ""
    key: str = ""
    optional: bool = False
    wait_s: float = 0.0


@dataclass
class Rule:
    name: str
    when: list[str] = field(default_factory=list)
    any: list[str] = field(default_factory=list)
    unless: list[str] = field(default_factory=list)
    if_flags: list[str] = field(default_factory=list)
    if_option: list[str] = field(default_factory=list)
    action: str = "click"
    target: str = ""
    key: str = ""
    steps: list[Step] = field(default_factory=list)
    event: list[str] = field(default_factory=list)
    set_flags: list[str] = field(default_factory=list)
    clear_flags: list[str] = field(default_factory=list)
    cooldown_s: float = 1.5
    settle_s: float = 0.8
    enabled: bool = True

    def templates(self) -> set[str]:
        names = set(self.when) | set(self.any) | set(self.unless)
        if self.target:
            names.add(self.target)
        for s in self.steps:
            if s.click:
                names.add(s.click)
        return names


@dataclass
class Decision:
    rule: Rule
    matches: dict[str, Match]
    fire_events: bool
    target: str = ""
    key: str = ""
    cooling: bool = False
    """Rule matched but is still in its cooldown - wait instead of acting."""


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def parse_rules(data: dict) -> list[Rule]:
    rules = []
    for i, raw in enumerate(data.get("rules") or []):
        if not isinstance(raw, dict) or "name" not in raw:
            raise ValueError(f"flow rule #{i + 1} must be a mapping with a 'name'")
        steps = []
        for s in raw.get("steps") or []:
            if not isinstance(s, dict):
                raise ValueError(f"rule '{raw['name']}': steps must be mappings")
            steps.append(Step(click=str(s.get("click", "")), key=str(s.get("key", "")),
                              optional=bool(s.get("optional", False)),
                              wait_s=float(s.get("wait_s", 0.0))))
        action = str(raw.get("action", "steps" if steps else "click"))
        if action not in ACTIONS:
            raise ValueError(f"rule '{raw['name']}': unknown action '{action}'")
        events = _as_list(raw.get("event"))
        for e in events:
            if e not in EVENTS:
                raise ValueError(f"rule '{raw['name']}': unknown event '{e}'")
        rule = Rule(
            name=str(raw["name"]),
            when=_as_list(raw.get("when")),
            any=_as_list(raw.get("any")),
            unless=_as_list(raw.get("unless")),
            if_flags=_as_list(raw.get("if_flags")),
            if_option=_as_list(raw.get("if_option")),
            action=action,
            target=str(raw.get("target", "")),
            key=str(raw.get("key", "")),
            steps=steps,
            event=events,
            set_flags=_as_list(raw.get("set_flags")),
            clear_flags=_as_list(raw.get("clear_flags")),
            cooldown_s=float(raw.get("cooldown_s", 1.5)),
            settle_s=float(raw.get("settle_s", 0.8)),
            enabled=bool(raw.get("enabled", True)),
        )
        if not rule.when and not rule.any:
            raise ValueError(f"rule '{rule.name}' needs 'when' or 'any'")
        rules.append(rule)
    return rules


def load_rules(path: str | Path) -> list[Rule]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return parse_rules(data)


def get_option(cfg: BotConfig, dotted: str) -> Any:
    obj: Any = cfg
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


class FlowEngine:
    """Picks the rule to run for the current frame."""

    # Same rule firing again within this window counts as the same screen
    # (e.g. a click that didn't register) - its events are not counted twice.
    EVENT_DEDUPE_S = 20.0

    def __init__(self, rules: list[Rule], library: TemplateLibrary, cfg: BotConfig):
        self.rules = rules
        self.library = library
        self.cfg = cfg
        self.flags: set[str] = set()
        self._last_fired: dict[str, float] = {}
        self._last_rule = ""

    # ------------------------------------------------------------ helpers
    def substitute(self, text: str) -> str:
        """Expand {tier} and {continue_key} placeholders from the config."""
        values = {"tier": self.cfg.tournament.tier,
                  "continue_key": self.cfg.tournament.continue_key}
        return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)

    def missing_templates(self) -> dict[str, list[str]]:
        """Rule name -> templates referenced by that rule that aren't loaded."""
        out = {}
        for r in self.rules:
            names = {self.substitute(n) for n in r.templates()}
            missing = sorted(n for n in names if not self.library.has(n)
                             and not n.startswith("tier_"))
            if missing:
                out[r.name] = missing
        return out

    def _option_ok(self, expr: str) -> bool:
        neg = expr.startswith("!")
        try:
            val = bool(get_option(self.cfg, expr.lstrip("!")))
        except AttributeError:
            log.warning("Unknown option '%s' in flow - treated as false", expr)
            val = False
        return not val if neg else val

    def _flags_ok(self, rule: Rule) -> bool:
        for f in rule.if_flags:
            if f.startswith("!"):
                if f[1:] in self.flags:
                    return False
            elif f not in self.flags:
                return False
        return True

    # ----------------------------------------------------------- evaluate
    def evaluate(self, frame: Frame, now: float | None = None) -> Decision | None:
        now = time.monotonic() if now is None else now
        cache: dict[str, Match | None] = {}

        def seen(name: str) -> Match | None:
            name = self.substitute(name)
            if name not in cache:
                cache[name] = self.library.find(frame, name) if self.library.has(name) else None
            return cache[name]

        for rule in self.rules:
            if not rule.enabled:
                continue
            if not all(self._option_ok(o) for o in rule.if_option):
                continue
            if not self._flags_ok(rule):
                continue
            if any(seen(n) is None for n in rule.when):
                continue
            if rule.any and not any(seen(n) is not None for n in rule.any):
                continue
            if any(seen(n) is not None for n in rule.unless):
                continue
            last = self._last_fired.get(rule.name, -1e9)
            if now - last < rule.cooldown_s:
                return Decision(rule, {}, False, cooling=True)
            matches = {k: v for k, v in cache.items() if v is not None}
            fire_events = not (self._last_rule == rule.name and now - last < self.EVENT_DEDUPE_S)
            target = self.substitute(rule.target) if rule.target else ""
            if not target:
                target = next((self.substitute(n) for n in rule.when + rule.any
                               if seen(n) is not None), "")
            return Decision(rule, matches, fire_events, target=target,
                            key=self.substitute(rule.key))
        return None

    def mark_fired(self, rule: Rule, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._last_fired[rule.name] = now
        self._last_rule = rule.name
        for f in rule.set_flags:
            self.flags.add(f)
        for f in rule.clear_flags:
            self.flags.discard(f)
