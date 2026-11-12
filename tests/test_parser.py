"""Client-side episode matching through cached anime relations."""

import json
from pathlib import Path
from typing import Any

import pytest
import typer

from jimaku_cli.api import Entry, EntryFlags, FileEntry, JimakuClient, JimakuError
from jimaku_cli.parser import Listing, parse_episode_range

RULE = {
    "anilist_id": 200,
    "source": {"type": "inclusive", "begin": 13, "end": 24},
    "destination": {"type": "inclusive", "begin": 1, "end": 12},
}


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(typer, "get_app_dir", lambda name: str(tmp_path / name))
    return tmp_path / "jimaku-cli" / "anime-relations.json"


def remote(name: str) -> FileEntry:
    return FileEntry("https://example.invalid/subtitle", name, 8, "2026-01-01T00:00:00Z")


def entry(anilist_id: int | None = 200, *, movie: bool = False) -> Entry:
    return Entry(
        1,
        "Show",
        "2026-01-01T00:00:00Z",
        EntryFlags(anime=True, movie=movie),
        anilist_id=anilist_id,
    )


def names(files: list[FileEntry]) -> list[str]:
    return [file.name for file in files]


class RelationsClient(JimakuClient):
    """One entry's listing and an anime-relations document, recording each request.

    It never starts a session, so a request not stubbed here fails instead of
    reaching jimaku.cc.
    """

    def __init__(  # pyright: ignore[reportMissingSuperCall]
        self,
        names: list[str],
        relations: dict[str, list[dict[str, Any]]] | None = None,
        hash: str = "v1",
    ) -> None:
        self.files = [remote(name) for name in names]
        self.document: dict[str, Any] = {
            "hash": hash,
            "created_at": "2026-01-01T00:00:00Z",
            "relations": relations or {},
        }
        self.requests: list[str] = []
        self.offline = False

    def get_files(self, entry_id: int) -> list[FileEntry]:
        self.requests.append("files")
        return self.files

    def get_anime_relations_hash(self) -> str:
        self.requests.append("date")
        if self.offline:
            raise JimakuError(503, "offline")
        return self.document["hash"]

    def get_anime_relations(self) -> dict[str, Any]:
        self.requests.append("relations")
        if self.offline:
            raise JimakuError(503, "offline")
        return self.document


def test_renumbering_applies_to_remote_files_only() -> None:
    client = RelationsClient(
        ["Show S01E13.srt", "Show S02E01.srt", "Show S02E02.srt"], {"200": [RULE]}
    )

    listing = Listing.fetch(client, entry())

    assert names(listing.for_episode(1)) == ["Show S01E13.srt", "Show S02E01.srt"]
    assert names(listing.for_episode(13)) == ["Show S01E13.srt"]
    assert names(listing.for_episode(14)) == []


def test_rules_moving_episodes_to_another_anime_are_ignored() -> None:
    sequel = {
        "anilist_id": 300,
        "source": {"type": "inclusive", "begin": 14, "end": 26},
        "destination": {"type": "inclusive", "begin": 1, "end": 13},
    }
    client = RelationsClient(["Show - 01.srt", "Show - 14.srt"], {"200": [sequel]})

    listing = Listing.fetch(client, entry())

    assert names(listing.for_episode(1)) == ["Show - 01.srt"]
    assert names(listing.for_episode(14)) == ["Show - 14.srt"]


@pytest.mark.parametrize(
    "source, destination, episode, files",
    [
        (
            {"type": "inclusive", "begin": 13, "end": 24},
            {"type": "inclusive", "begin": 1, "end": 12},
            2,
            ["Show - 14.srt", "Show - 15.srt"],
        ),
        (
            {"type": "from", "value": 25},
            {"type": "from", "value": 1},
            6,
            ["Show - 30.srt", "Show - 29.srt"],
        ),
        (
            {"type": "inclusive", "begin": 1, "end": 2},
            {"type": "number", "value": 1},
            1,
            ["Show - 02.srt", "Show - 03.srt"],
        ),
    ],
    ids=["inclusive", "from", "number"],
)
def test_each_relation_range_type_renumbers(
    source: dict[str, Any], destination: dict[str, Any], episode: int, files: list[str]
) -> None:
    rule = {"anilist_id": 200, "source": source, "destination": destination}
    client = RelationsClient(files, {"200": [rule]})

    listing = Listing.fetch(client, entry())

    assert names(listing.for_episode(episode)) == files[:1]


def test_movies_and_numberless_videos_receive_the_whole_listing() -> None:
    files = ["Show - 01.srt", "Show - OVA.srt"]
    movie = RelationsClient(files, {"200": [RULE]})

    assert names(Listing.fetch(movie, entry(movie=True)).for_episode(13)) == files
    assert movie.requests == ["files"]

    client = RelationsClient(files)
    listing = Listing.fetch(client, entry(anilist_id=None))

    assert names(listing.for_episode(None)) == files
    assert names(listing.for_episode(1)) == ["Show - 01.srt"]
    assert client.requests == ["files"]


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("[Group] Show - 01v2 [1080p].srt", (1, 1)),
        ("Show - 01-02.srt", (1, 2)),
        ("ショー 第03話.srt", (3, 3)),
        ("Show - 03.5.srt", None),
        ("Show - OVA.srt", None),
    ],
)
def test_remote_episode_ranges(filename: str, expected: tuple[int, int] | None) -> None:
    assert parse_episode_range(filename) == expected


def test_relations_are_cached_and_revalidated_by_hash(cache: Path) -> None:
    client = RelationsClient(["Show - 13.srt"], {"200": [RULE]})

    assert names(Listing.fetch(client, entry()).for_episode(1)) == ["Show - 13.srt"]
    assert client.requests == ["files", "relations"]
    assert json.loads(cache.read_text(encoding="utf-8"))["hash"] == "v1"

    client.requests.clear()
    assert names(Listing.fetch(client, entry()).for_episode(1)) == ["Show - 13.srt"]
    assert client.requests == ["files", "date"]

    client.document = {**client.document, "hash": "v2", "relations": {}}
    client.requests.clear()
    assert Listing.fetch(client, entry()).for_episode(1) == []
    assert client.requests == ["files", "date", "relations"]
    assert json.loads(cache.read_text(encoding="utf-8"))["hash"] == "v2"


def test_cached_relations_survive_an_unreachable_jimaku() -> None:
    client = RelationsClient(["Show - 13.srt"], {"200": [RULE]})
    _ = Listing.fetch(client, entry())
    client.offline = True

    assert names(Listing.fetch(client, entry()).for_episode(1)) == ["Show - 13.srt"]


def test_unreachable_relations_without_a_cache_fail() -> None:
    client = RelationsClient(["Show - 13.srt"], {"200": [RULE]})
    client.offline = True

    with pytest.raises(JimakuError):
        _ = Listing.fetch(client, entry())


def test_a_corrupt_cache_is_replaced(cache: Path) -> None:
    cache.parent.mkdir(parents=True)
    _ = cache.write_text("{", encoding="utf-8")
    client = RelationsClient(["Show - 13.srt"], {"200": [RULE]})

    assert names(Listing.fetch(client, entry()).for_episode(1)) == ["Show - 13.srt"]
    assert client.requests == ["files", "relations"]
    assert json.loads(cache.read_text(encoding="utf-8"))["hash"] == "v1"


def test_an_unwritable_cache_does_not_fail_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "blocker"
    blocker.touch()
    monkeypatch.setattr(typer, "get_app_dir", lambda name: str(blocker / name))
    client = RelationsClient(["Show - 13.srt"], {"200": [RULE]})

    assert names(Listing.fetch(client, entry()).for_episode(1)) == ["Show - 13.srt"]
