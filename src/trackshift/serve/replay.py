"""Replay bundle generation through the same FastAPI route/model path."""
from __future__ import annotations

import hashlib
import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .app import create_app
from .fixture import BATTLE_ID, EVENT, SYNTHETIC_FIXTURE_VERSION


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def request_json(app: Any, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    """Call a FastAPI app in-process without an optional HTTP client package."""
    parsed = urlsplit(path)
    handler = getattr(app.state, "route_handlers", {}).get((method.upper(), parsed.path))
    if handler is not None:
        try:
            if method.upper() == "POST":
                return 200, handler(body or {})
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            return 200, handler(**query)
        except Exception as exc:
            status = int(getattr(exc, "status_code", 500))
            detail = getattr(exc, "detail", str(exc))
            return status, {"detail": detail}

    async def run() -> tuple[int, Any]:
        request_body = b"" if body is None else json.dumps(body).encode("utf-8")
        sent = False
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": request_body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method.upper(), "scheme": "http", "path": parsed.path,
            "raw_path": parsed.path.encode(), "query_string": parsed.query.encode(),
            "headers": [(b"content-type", b"application/json")], "client": ("test", 1),
            "server": ("test", 80),
        }, receive, send)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        payload = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return status, json.loads(payload or b"null")

    return asyncio.run(run())


def build_replay_bundle(out: str | Path, *, event: str = EVENT, battle_id: str = BATTLE_ID, final_mode: bool = False) -> dict[str, Any]:
    """Call route handlers in-process and persist their response bodies.

    The synthetic bundle is useful for UI development only. Final mode raises
    at app construction while official rules/C4/C5 gates remain unresolved.
    """
    if final_mode:
        create_app(mode="replay", final_mode=True)
    root = Path(out)
    app = create_app(mode="replay")
    requests = {
        "meta": ("GET", "/api/v1/meta", None),
        "track": ("GET", f"/api/v1/track/{event}", None),
        "rules": ("GET", f"/api/v1/rules/{event}", None),
        "battles": ("GET", "/api/v1/battles", None),
        "validation": ("GET", "/api/v1/validation", None),
        "timeline": ("GET", f"/api/v1/battles/{battle_id}/timeline", None),
        "policies": ("GET", "/api/v1/simulate/policies", None),
        "plan": ("POST", "/api/v1/plan", {"include_baselines": True}),
        "simulate": ("POST", "/api/v1/simulate", {"n_episodes": 8, "seed": 17, "rival_policy": "DEFEND_CONSERVE"}),
    }
    files: dict[str, str] = {}
    for name, (method, path, body) in requests.items():
        status, payload = request_json(app, method, path, body)
        if status != 200:
            raise RuntimeError(f"replay route failed {method} {path}: {status} {payload}")
        destination = root / ("timeline.json" if name == "timeline" else f"{name}.json")
        _write_json(destination, payload)
        files[str(destination.relative_to(root))] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "trackshift_replay_manifest_v1",
        "event": event,
        "battle_id": battle_id,
        "mode": "replay",
        "synthetic_fixture": SYNTHETIC_FIXTURE_VERSION,
        "provenance": "SIMULATED",
        "stubs_used": [],
        "final_mode_permitted": False,
        "release_ready": False,
        "rule_violations": 0,
        "files": files,
        "reason": "development synthetic replay; not a final release artifact",
    }
    _write_json(root / "bundle_manifest.json", manifest)
    return manifest


__all__ = ["build_replay_bundle", "request_json"]
