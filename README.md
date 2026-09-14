# TrackShift

TrackShift 2026 E-Delta Energy & Overtake Intelligence.

## Demo deployment

Run the backend without telemetry or trained-model artifacts:

```bash
TRACKSHIFT_DEMO_MODE=1 .venv/bin/python scripts/serve/run_service.py --host 0.0.0.0 --port 8000
```

Configure the frontend with `TRACKSHIFT_API_ORIGIN=http://127.0.0.1:8000/api/v1`
(or `NEXT_PUBLIC_TRACKSHIFT_API_BASE=http://127.0.0.1:8000/api/v1` when bypassing
the same-origin proxy), then start or build it from `frontend/`.
