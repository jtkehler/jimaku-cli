import json
import requests
from typing import Any


class JimakuError(Exception):
    def __init__(self, status: int, message: str, code: int | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.code = code


class JimakuClient:
    def __init__(self, api_key: str, timeout: float = 10.0):
        self.api_key = api_key
        self.base_url = "https://jimaku.cc/"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = api_key

    def get_entry(self, entry_id: int):
        return self._get(f"/api/entries/{entry_id}")

    def get_files(self, entry_id: int, episode: int | None = None):
        return self._get(
            f"/api/entries/{entry_id}/files", {"episode": episode}
        )

    def search_entries(
        self,
        query: str | None = None,
        *,
        anime: bool | None = None,
        anilist_id: int | None = None,
        tmdb_id: str | None = None,
        after: int | None = None,
        before: int | None = None,
    ):
        return self._get(
            "/api/entries/search",
            {
                "query": query,
                "anime": None if anime is None else json.dumps(anime),
                "anilist_id": anilist_id,
                "tmdb_id": tmdb_id,
                "after": after,
                "before": before,
            },
        )

    def _get(self, path: str, params: dict[str, Any] | None = None):
        response = self.session.get(
            self.base_url.rstrip("/") + path,
            params=params,
            timeout=self.timeout,
        )
        if not response.ok:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise JimakuError(
                response.status_code,
                body.get("error", response.reason),
                body.get("code"),
            )
        return response.json()