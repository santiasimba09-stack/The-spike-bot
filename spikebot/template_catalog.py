"""What each template is, where to find it in the game and how to capture it."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateInfo:
    name: str
    group: str
    what: str
    mode: str = "color"
    required: bool = False


_C = [
    # --- in match (starters included)
    TemplateInfo("hud_pause", "Match HUD", "Pause button (two white bars) top-right during a match. "
                 "Starter included.", "white", True),
    TemplateInfo("hud_label_jump", "Match HUD", "The word 'Jump' on the bottom HUD button while "
                 "defending (text only, not the key icon). Starter included.", "white", True),
    TemplateInfo("hud_label_spike", "Match HUD", "The word 'Spike' on the bottom HUD button when "
                 "your attack is on. Starter included.", "white", True),
    TemplateInfo("hud_label_cancel", "Match HUD", "The word 'Cancel' (top HUD button while in the "
                 "air). Starter included.", "white"),
    TemplateInfo("hud_label_bump", "Match HUD", "The word 'Bump'. Starter included (informational).",
                 "white"),
    TemplateInfo("hud_label_block", "Match HUD", "The word 'Block'. Starter included (informational).",
                 "white"),
    TemplateInfo("hud_label_feint", "Match HUD", "The word 'Feint'. Starter included (informational).",
                 "white"),
    # --- navigation
    TemplateInfo("home_match", "Navigation", "Home screen 'Match' button (bottom-right). "
                 "Starter included.", "color", True),
    TemplateInfo("mode_tournament", "Navigation", "The 'Tournament' tile in the Match menu "
                 "(crop the title text/icon, not the changing 'Remaining Rewards').", "color", True),
    TemplateInfo("prepare_for_match", "Navigation", "'Prepare for Match' button on the tournament "
                 "bracket screen (bottom-right).", "color", True),
    TemplateInfo("start_game", "Navigation", "Yellow 'Start Game' button on the team/formation "
                 "screen (bottom-right).", "color", True),
    # --- after match
    TemplateInfo("continue_prompt", "After match", "The word 'Continue' of the '[key] Continue' "
                 "prompt (bottom-left). Starter included.", "white", True),
    TemplateInfo("result_win", "After match", "'WIN!' text on the result/stats screen.", "white", True),
    TemplateInfo("result_lose", "After match", "The defeat text on the result/stats screen "
                 "(e.g. 'LOSE').", "white", True),
    TemplateInfo("result_next", "After match", "Optional: the next/arrow button on the result "
                 "screen. If missing, the continue key is pressed.", "color"),
    TemplateInfo("tournament_winner", "After match", "'Tournament Winner!' text on the trophy "
                 "screen.", "white", True),
    TemplateInfo("winner_close", "After match", "Optional: a back/close button on the trophy "
                 "screen. NEVER capture the 'Recharge' button.", "color"),
    TemplateInfo("claim_reward", "After match", "'Claim Reward' button (Tournament Win Achievement "
                 "Reward).", "color"),
    # --- reset
    TemplateInfo("bracket_reset", "Reset", "Circular-arrows reset button next to 'Prepare for Match' "
                 "on the bracket.", "color", True),
    TemplateInfo("tier_confirm", "Reset", "Confirm button of the Tier popup that opens after the "
                 "reset button.", "color", True),
    TemplateInfo("tier_silver", "Reset", "Silver tier icon in the Tier popup.", "color"),
    TemplateInfo("tier_gold", "Reset", "Gold tier icon in the Tier popup.", "color"),
    TemplateInfo("tier_diamond", "Reset", "Diamond tier icon in the Tier popup.", "color"),
    TemplateInfo("tier_master", "Reset", "Master tier icon in the Tier popup.", "color"),
    # --- misc
    TemplateInfo("skip", "Misc", "'Skip' button on cutscenes / dialogues.", "white"),
    TemplateInfo("retry", "Misc", "'Retry' button, if the game shows one after a loss.", "color"),
    TemplateInfo("popup_ok", "Misc", "OK button of harmless info popups.", "color"),
    TemplateInfo("popup_close", "Misc", "X / close button of harmless info popups.", "color"),
    # --- safety
    TemplateInfo("danger_purchase", "Safety (bot STOPS)", "Any purchase / payment dialog.", "color"),
    TemplateInfo("danger_shop", "Safety (bot STOPS)", "The shop screen header.", "color"),
    TemplateInfo("danger_recharge", "Safety (bot STOPS)", "The 'Recharge' button on the trophy "
                 "screen (may cost premium currency).", "color"),
    TemplateInfo("danger_premium_confirm", "Safety (bot STOPS)", "A confirm dialog that spends "
                 "V Points / premium currency.", "color"),
]

CATALOG: dict[str, TemplateInfo] = {t.name: t for t in _C}


def info(name: str) -> TemplateInfo:
    return CATALOG.get(name) or TemplateInfo(name, "Custom", "Custom template used by flow.yaml.")
