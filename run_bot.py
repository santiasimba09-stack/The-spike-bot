"""Run the bot from the command line (no UI).

    python run_bot.py                 # use config.yaml (edit it with run_ui.py)
    python run_bot.py --max-runs 5    # override the run limit
    python run_bot.py --menus-only    # you play the matches, bot handles menus
    python run_bot.py --dry-run       # log actions without sending input

F8 = emergency stop, F7 = pause/resume (configurable). Ctrl+C also stops.
"""
import argparse
import logging
import sys
from pathlib import Path

from spikebot.config import load_config
from spikebot.control import BotControl
from spikebot.hotkeys import HotkeyManager
from spikebot.logging_setup import setup_logging
from spikebot.platform import set_dpi_awareness
from spikebot.runner import BotRunner

BASE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", default=str(BASE / "config.yaml"))
    ap.add_argument("--max-runs", type=int, help="tournament runs before stopping (0 = no limit)")
    ap.add_argument("--max-minutes", type=float, help="stop after N minutes")
    ap.add_argument("--menus-only", action="store_true", help="don't play matches (you play)")
    ap.add_argument("--dry-run", action="store_true", help="don't send any input")
    ap.add_argument("--live-view", action="store_true", help="show what the bot sees in matches")
    ap.add_argument("--record", action="store_true", help="record match frames to recordings/")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    set_dpi_awareness()
    setup_logging(BASE / "logs", logging.DEBUG if args.verbose else logging.INFO)
    cfg = load_config(args.config)
    if args.max_runs is not None:
        cfg.limits.max_runs = args.max_runs
    if args.max_minutes is not None:
        cfg.limits.max_minutes = args.max_minutes
    if args.menus_only:
        cfg.gameplay.enabled = False
    if args.dry_run:
        cfg.dry_run = True
    if args.live_view:
        cfg.gameplay.debug_overlay = True
    if args.record:
        cfg.gameplay.record_frames = True
    problems = cfg.validate()
    if problems:
        print("Config problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2

    control = BotControl()
    hk = HotkeyManager()
    hk.bind(cfg.hotkeys.stop, lambda: control.request_stop(f"{cfg.hotkeys.stop.upper()} pressed"))
    hk.bind(cfg.hotkeys.pause, lambda: logging.info(
        "Paused" if control.toggle_pause() else "Resumed"))
    if not hk.start():
        logging.warning("Hotkeys unavailable - use Ctrl+C to stop")
    runner = BotRunner(cfg, control, base_dir=BASE)
    print(f"Starting - switch to the game. {cfg.hotkeys.stop.upper()} = emergency stop, "
          f"{cfg.hotkeys.pause.upper()} = pause.")
    thread = runner.start_thread()
    try:
        while thread.is_alive():
            thread.join(0.2)
    except KeyboardInterrupt:
        control.request_stop("Ctrl+C")
        thread.join(5)
    hk.stop()
    print(f"Stopped: {runner.stop_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
