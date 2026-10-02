"""Score every template on a screenshot (or the live game window).

    python tools/test_templates.py captures/capture_20261002_210000.png
    python tools/test_templates.py --live            # grab the game window now
    python tools/test_templates.py IMG --annotate out.png
"""
import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spikebot.config import load_config  # noqa: E402
from spikebot.platform import set_dpi_awareness  # noqa: E402
from spikebot.vision import Frame, TemplateLibrary  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("image", nargs="?")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--annotate", help="write a copy of the image with matches drawn")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.live:
        set_dpi_awareness()
        from spikebot.capture import GameScreen

        screen = GameScreen(cfg.window)
        img = screen.grab().image
        screen.close()
    elif args.image:
        img = cv2.imread(args.image, cv2.IMREAD_COLOR)
        if img is None:
            print(f"cannot read {args.image}")
            return 1
    else:
        ap.print_help()
        return 1
    lib = TemplateLibrary(ROOT / cfg.templates_dir, cfg.matching.default_threshold,
                          cfg.matching.scales)
    frame = Frame(img)
    print(f"image {img.shape[1]}x{img.shape[0]}\n")
    print(f"{'template':24} {'score':>6} {'thr':>5}  where")
    out = img.copy()
    for name in sorted(lib.templates):
        m = lib.best(frame, name)
        thr = lib.threshold_for(name)
        if m is None:
            print(f"{name:24} {'-':>6} {thr:5.2f}")
            continue
        hit = m.score >= thr
        print(f"{name:24} {m.score:6.2f} {thr:5.2f}  {'VISIBLE' if hit else '':8} at {m.x},{m.y}")
        if hit:
            x, y, w, h = m.box
            cv2.rectangle(out, (x, y), (x + w, y + h), (0, 0, 255), 2)
            cv2.putText(out, name, (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 0, 255), 1)
    if args.annotate:
        cv2.imwrite(args.annotate, out)
        print(f"\nwrote {args.annotate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
