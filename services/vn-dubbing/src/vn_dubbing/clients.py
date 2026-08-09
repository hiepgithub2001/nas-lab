from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import PipelineError


class ApiError(PipelineError):
    code = "api_error"


class JsonApiClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json", "User-Agent": "vn-dubbing/0.1"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
            headers["X-Emby-Token"] = self.api_key
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(f"{method} {url} failed: {exc}") from exc
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ApiError(f"{method} {url} returned invalid JSON") from exc


class RadarrClient(JsonApiClient):
    def tags(self) -> list[dict[str, Any]]:
        result = self.request("GET", "/api/v3/tag")
        return result if isinstance(result, list) else []

    def movies(self) -> list[dict[str, Any]]:
        result = self.request("GET", "/api/v3/movie")
        return result if isinstance(result, list) else []

    def movie(self, movie_id: int) -> dict[str, Any]:
        result = self.request("GET", f"/api/v3/movie/{movie_id}")
        if not isinstance(result, dict):
            raise ApiError(f"Radarr movie {movie_id} response is invalid")
        return result


class JellyfinClient(JsonApiClient):
    def refresh_library(self) -> None:
        self.request("POST", "/Library/Refresh")
