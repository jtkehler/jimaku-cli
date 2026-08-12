"""A thin wrapper over the jimaku.cc REST API.

The models below mirror the schemas published at https://jimaku.cc/api/docs. Each one
is built through `from_json` rather than `cls(**data)`, so a field added server-side is
ignored instead of raising, while a documented-required field going missing raises
KeyError -- a contract break worth hearing about. `flags` is the lone exception, as its
own schema marks none of its keys required.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


@dataclass(frozen=True)
class EntryFlags:
    """The API's `EntryFlags` object: an entry's boolean attributes.

    Held server-side as a bitfield and expanded into an object for API consumers, so
    all five arrive on every entry. They default to False anyway, since the published
    schema marks none of them required.
    """

    anime: bool = False
    unverified: bool = False  # Not yet checked by an editor.
    external: bool = False  # Imported from another subtitle site.
    movie: bool = False
    adult: bool = False

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> EntryFlags:
        return cls(
            anime=bool(data.get("anime", False)),
            unverified=bool(data.get("unverified", False)),
            external=bool(data.get("external", False)),
            movie=bool(data.get("movie", False)),
            adult=bool(data.get("adult", False)),
        )


@dataclass(frozen=True)
class Entry:
    """The API's `Entry` object: one directory of subtitles.

    Usually backed by an AniList or TMDB entry. The optional fields are left out of the
    JSON entirely when unset rather than sent as null -- the published schema calls
    them nullable, but the server skips serialising them -- and both cases land here as
    None.
    """

    id: int
    name: str  # Romaji, e.g. "Sousou no Frieren".
    last_modified: str  # RFC3339; the newest file's date, not the date of an edit.
    flags: EntryFlags = field(default_factory=EntryFlags)
    creator_id: int | None = None
    anilist_id: int | None = None
    tmdb_id: str | None = None  # "tv:1234" or "movie:1234".
    notes: str | None = None  # Limited markdown, set by editors only.
    english_name: str | None = None
    japanese_name: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Entry:
        return cls(
            id=data["id"],
            name=data["name"],
            last_modified=data["last_modified"],
            flags=EntryFlags.from_json(data.get("flags") or {}),
            creator_id=data.get("creator_id"),
            anilist_id=data.get("anilist_id"),
            tmdb_id=data.get("tmdb_id"),
            notes=data.get("notes"),
            english_name=data.get("english_name"),
            japanese_name=data.get("japanese_name"),
        )


@dataclass(frozen=True)
class FileEntry:
    """The API's `FileEntry` object: one downloadable file under an entry.

    Not necessarily a subtitle -- entries also carry ZIP archives and the occasional
    stray upload -- so callers filter on the extension of `name`.
    """

    url: str  # Absolute, so `download_file` fetches it without the base URL.
    name: str
    size: int  # Bytes.
    last_modified: str  # RFC3339, UTC.

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> FileEntry:
        return cls(
            url=data["url"],
            name=data["name"],
            size=data["size"],
            last_modified=data["last_modified"],
        )


class JimakuError(Exception):
    """A non-2xx response, carrying the API's `ApiError` body where there is one.

    `message` and `code` come from that body's `error` and `code` fields. A 429 has an
    empty body, and anything a proxy generates has no `ApiError` at all, so `code` may
    be None and `message` falls back to the HTTP reason phrase.
    """

    def __init__(self, status: int, message: str, code: int | None = None) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.code = code


class JimakuClient:
    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.base_url = "https://jimaku.cc/"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = api_key

    def get_entry(self, entry_id: int) -> Entry:
        """`GET /api/entries/{id}`: the entry with this ID."""
        return Entry.from_json(self._get(f"/api/entries/{entry_id}"))

    def get_files(self, entry_id: int, episode: int | None = None) -> list[FileEntry]:
        """`GET /api/entries/{id}/files`: the files under an entry.

        `episode` filters server-side, but only as a best-effort guess from the remote
        filenames, and it is ignored outright for entries flagged as movies.
        """
        return [
            FileEntry.from_json(item)
            for item in self._get(f"/api/entries/{entry_id}/files", {"episode": episode})
        ]

    def search_entries(
        self,
        query: str | None = None,
        *,
        anime: bool | None = None,
        anilist_id: int | None = None,
        tmdb_id: str | None = None,
        after: int | None = None,
        before: int | None = None,
    ) -> list[Entry]:
        """`GET /api/entries/search`: matching entries, best match first.

        `anime` is tested for equality against the entry's flag before anything else,
        so leaving it unset keeps the server's default of True and hides every
        non-anime entry -- including one being looked up by ID. `anilist_id`, then
        `tmdb_id`, short-circuit the rest: pass either and `query`, `after` and
        `before` are ignored. `after` and `before` are UNIX timestamps in seconds
        bounding `last_modified`, and `query` is a fuzzy match over all three names.
        """
        return [
            Entry.from_json(item)
            for item in self._get(
                "/api/entries/search",
                {
                    "query": query,
                    # Serialised by hand: requests would send Python's "True"/"False",
                    # which the API will not parse.
                    "anime": None if anime is None else json.dumps(anime),
                    "anilist_id": anilist_id,
                    "tmdb_id": tmdb_id,
                    "after": after,
                    "before": before,
                },
            )
        ]

    def download_file(self, url: str, dest: Path) -> None:
        """Fetch a file entry's URL to `dest`, atomically.

        `FileEntry.url` is absolute, so this bypasses `_get`'s base-URL join and its
        JSON decode. The write goes to a sibling `.part` and is renamed into place, so
        a run killed partway through cannot leave a truncated subtitle behind — which
        would otherwise count as an existing subtitle and be skipped forever after.
        """
        partial = dest.with_name(dest.name + ".part")
        try:
            with self.session.get(url, timeout=self.timeout, stream=True) as response:
                if not response.ok:
                    raise JimakuError(
                        response.status_code, response.reason or "request failed"
                    )
                with partial.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        f.write(chunk)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        os.replace(partial, dest)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Send a GET and return the decoded JSON body.

        requests drops params whose value is None, which is what lets `search_entries`
        hand over every filter and have the unset ones fall away. The return is `Any`
        deliberately: the callers above name the real response type and pass the body
        to the matching `from_json`, the only place the shape is ever asserted.
        """
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
                body.get("error") or response.reason or "request failed",
                body.get("code"),
            )
        return response.json()