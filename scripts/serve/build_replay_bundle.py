#!/usr/bin/env python3
"""Build the synthetic development replay bundle through the shared routes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.serve.replay import build_replay_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--event", default="british_grand_prix")
    parser.add_argument("--battle", default="synthetic_2026_GBR_Race_HAM_ANT")
    parser.add_argument("--final-mode", action="store_true", help="Require official final inputs; currently fails closed")
    args = parser.parse_args()
    print(json.dumps(build_replay_bundle(args.out, event=args.event, battle_id=args.battle, final_mode=args.final_mode), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
