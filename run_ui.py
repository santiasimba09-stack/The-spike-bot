"""Open the SpikeBot settings & control window.

    python run_ui.py [--config config.yaml]
"""
import argparse
from pathlib import Path

from spikebot.ui.app import main

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.yaml"))
    args = ap.parse_args()
    main(args.config, Path(__file__).resolve().parent)
