#!/usr/bin/env python3
"""Run the TrackShift service on CPU (CP-24).

    python scripts/serve/run_service.py
    python scripts/serve/run_service.py --port 8080 --reload
    python scripts/serve/run_service.py --check

``--check`` builds the app, reports which routes are backed by a real artifact
and which would answer from the synthetic development model, then exits without
binding a port. Run it first: a service that starts cleanly and then serves
placeholder probabilities is the failure worth catching before the UI owner
does.

``--final`` refuses to start unless every gate the release path requires is met.
It is expected to fail today and the failure names what is missing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.serve.app import PASS_MODELS, create_app  # noqa: E402
from trackshift.serve.pass_service import available_checkpoints  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def readiness() -> dict[str, object]:
    """What the service would actually answer with, before it starts.

    Reported per checkpoint rather than as one boolean: DETECTION can be backed
    by a real artifact while BRAKING falls back, and a single "ready: true"
    would hide that.
    """
    artifacts = available_checkpoints(PASS_MODELS)
    backed = {k: v for k, v in artifacts.items() if v}
    return {
        "pass_model_artifacts": {
            k: (str(Path(v).relative_to(ROOT)) if v else None)
            for k, v in artifacts.items()
        },
        "checkpoints_backed_by_a_real_model": sorted(backed),
        "checkpoints_that_would_use_the_synthetic_model": sorted(
            k for k, v in artifacts.items() if not v),
        "cpu_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--final", action="store_true",
                        help="Refuse to start unless every release gate is met")
    parser.add_argument("--check", action="store_true",
                        help="Report readiness and exit without binding a port")
    args = parser.parse_args()

    report = readiness()
    if args.check:
        print(json.dumps(report, indent=2))
        return 0

    # Construct before binding, so a configuration failure is a clean error
    # rather than a half-started server.
    create_app(mode="service", final_mode=args.final)

    stubbed = report["checkpoints_that_would_use_the_synthetic_model"]
    if stubbed:
        print(f"WARNING: {stubbed} have no CP-14 artifact and will answer from the "
              "synthetic development model. Responses carry is_stub: true.",
              file=sys.stderr)

    import uvicorn

    uvicorn.run(
        "trackshift.serve.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
