"""Re-run perception on a recorded match and write an annotated video + stats.

Recordings are made with "Record matches" (Gameplay tab) or `run_bot.py --record`.

    python tools/replay_recording.py recordings/20261002_210000
    python tools/replay_recording.py recordings/20261002_210000 --frames   # also annotated JPGs
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spikebot.config import load_config  # noqa: E402
from spikebot.gameplay.overlay import annotate  # noqa: E402
from spikebot.gameplay.perception import Perception  # noqa: E402
from spikebot.vision import Frame, TemplateLibrary  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--frames", action="store_true", help="write annotated JPGs as well")
    ap.add_argument("--fps", type=float, default=20)
    args = ap.parse_args()
    folder = Path(args.folder)
    cfg = load_config(args.config)
    lib = TemplateLibrary(ROOT / cfg.templates_dir, cfg.matching.default_threshold,
                          cfg.matching.scales)
    perception = Perception(cfg.gameplay, lib)
    events = {}
    keys = []
    ev_path = folder / "events.jsonl"
    if ev_path.exists():
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("frame"):
                events[e["frame"]] = e
            if e.get("user_key"):
                keys.append(e)
    images = sorted(folder.glob("[0-9]*.jpg"))
    if not images:
        print("no frames found")
        return 1
    out_dir = folder / "annotated"
    out_dir.mkdir(exist_ok=True)
    writer = None
    stats = Counter()
    for p in images:
        img = cv2.imread(str(p))
        det = perception.detect(Frame(img))
        stats["frames"] += 1
        stats[f"phase_{det.phase}"] += 1
        for k in ("player", "net_x", "floor_ring", "air_ring", "shadow"):
            if getattr(det, k) is not None:
                stats[k] += 1
        e = events.get(p.name, {})
        lines = [p.name] + list(e.get("notes", [])) + (["> " + ", ".join(e["actions"])]
                                                        if e.get("actions") else [])
        vis = annotate(img, det, lines)
        if writer is None:
            h, w = vis.shape[:2]
            writer = cv2.VideoWriter(str(out_dir / "replay.mp4"),
                                     cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (w, h))
        writer.write(vis)
        if args.frames:
            cv2.imwrite(str(out_dir / p.name), vis)
    writer.release()
    n = stats["frames"]
    print(f"{n} frames -> {out_dir / 'replay.mp4'}")
    for k in ("player", "net_x", "floor_ring", "air_ring", "shadow"):
        print(f"  {k:11} detected in {stats[k] / n:6.1%} of frames")
    for k, v in sorted(stats.items()):
        if k.startswith("phase_"):
            print(f"  {k:11} {v / n:6.1%}")
    if keys:
        print(f"  your key presses logged: {len(keys)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
