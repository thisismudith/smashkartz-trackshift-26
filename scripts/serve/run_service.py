"""Run the dev TrackShift API service: python scripts/serve/run_service.py [--port 8000]."""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import uvicorn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "trackshift.serve.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(SRC_DIR),
    )


if __name__ == "__main__":
    main()
