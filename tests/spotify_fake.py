"""fake spotify for tests: answers from the anonymized real responses in fixtures/spotify."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spotify"
# developer apps from before february 2026 still get the old api, new ones the new one
LEGACY_APP = "legacy_app"
NEW_APP = "new_app"
VARIANTS = (LEGACY_APP, NEW_APP)
NOT_FOUND = (404, {"error": {"status": 404, "message": "Resource not found"}})


class MemoryTokenStore:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def load(self) -> str | None:
        return self.token

    def save(self, refresh_token: str) -> None:
        self.token = refresh_token

    def clear(self) -> None:
        self.token = None


def _offset(params: dict[str, Any]) -> int:
    return int(params.get("offset", 0) or 0)


class FakeSpotify:
    def __init__(self, variant: str = LEGACY_APP) -> None:
        self.variant = variant
        fixtures = FIXTURES / variant
        self.ids: dict[str, str] = json.loads((fixtures / "_ids.json").read_text(encoding="utf-8"))
        self.routes: dict[tuple, tuple[int, Any]] = {}
        self.bodies: dict[str, Any] = {}
        for path in sorted(fixtures.glob("*.json")):
            if path.name.startswith("_"):
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            request = record["request"]
            route = (record["status"], record["body"])
            self.bodies[path.stem] = record["body"]
            limit = request["params"].get("limit")
            self.routes[(request["path"], _offset(request["params"]), limit)] = route
            # probes answer only for their exact limit, everything else for any limit
            if ".probe" not in path.stem:
                self.routes.setdefault((request["path"], _offset(request["params"])), route)
        self.overrides: dict[str, tuple[int, Any]] = {}
        self.requests: list[str] = []
        self.token_requests: list[dict[str, str]] = []
        self.revoked = False

    def body(self, name: str) -> Any:
        return json.loads(json.dumps(self.bodies[name]))

    def override(self, path: str, status: int, body: Any) -> None:
        """answer this api path (any offset and limit) with something else."""
        self.overrides[path] = (status, body)

    def counts(self) -> Counter:
        return Counter(r.split("?")[0] for r in self.requests)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.spotify.com":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if self.revoked:
                return httpx.Response(400, json={"error": "invalid_grant"})
            n = len(self.token_requests)
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-{n}",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": f"refresh-{n}",
                    "scope": "playlist-read-private",
                },
            )
        assert request.url.host == "api.spotify.com", request.url
        assert request.headers["Authorization"].startswith("Bearer access-")
        path = request.url.path.removeprefix("/v1")
        params = dict(request.url.params)
        self.requests.append(f"{path}?offset={_offset(params)}&limit={params.get('limit')}")
        status, body = (
            self.overrides.get(path)
            or self.routes.get((path, _offset(params), params.get("limit")))
            or self.routes.get((path, _offset(params)))
            or NOT_FOUND
        )
        return httpx.Response(status, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
