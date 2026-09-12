#!/usr/bin/env python3
"""Run the M22 development DP without materialising generated artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from trackshift.value.api import DPConfig, solve_dp, shadow_price  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--segments", type=Path)
    parser.add_argument("--rules", type=Path)
    args = parser.parse_args()
    state = json.loads(args.state.read_text()) if args.state else {}
    segments = json.loads(args.segments.read_text()) if args.segments else []
    rules = yaml.safe_load(args.rules.read_text()) if args.rules else None
    result = solve_dp(segments, state, rules, config=DPConfig())
    print(json.dumps({"dp": result.to_dict(), "shadow_price": shadow_price(segments, state, rules) if segments else None}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
