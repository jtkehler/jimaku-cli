"""Episodes and releases parsed from filenames.

Episode numbers for local videos and remote files are read in an entry's own numbering.
jimaku's `?episode=` filter renumbers remote names in one direction only. Here both
sides go through the same rules, using one listing request.
"""

import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anitopy
import guessit
import typer

from .api import TRANSFER_ERRORS, Entry, FileEntry, JimakuClient
from .postprocess import temporary_path

logger = logging.getLogger(__name__)

APP_NAME = "jimaku-cli"
RELATIONS_FILE = "anime-relations.json"


@dataclass(frozen=True)
class EpisodeRange:
    """An anime-relations episode range as jimaku serializes it.

    `last` is None when the range is open-ended. A `single` destination receives
    every episode of its source range.
    """

    first: int
    last: int | None
    single: bool = False

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> EpisodeRange:
        match data["type"]:
            case "inclusive":
                return cls(data["begin"], data["end"])
            case "from":
                return cls(data["value"], None)
            case _:  # "number"
                return cls(data["value"], data["value"], single=True)


@dataclass(frozen=True)
class Rule:
    """An anime-relations rule that renumbers episodes within one AniList entry."""

    source: EpisodeRange
    destination: EpisodeRange


@dataclass(frozen=True)
class Listing:
    """An entry's files, each read once for the episodes it covers."""

    files: list[FileEntry]
    ranges: list[list[tuple[int, int]]]  # Parallel to `files`: own range, then any renumbered one.
    rules: list[Rule]
    movie: bool

    @classmethod
    def fetch(cls, client: JimakuClient, entry: Entry) -> Listing:
        """One listing request, plus anime relations when the entry can be renumbered."""
        files = client.get_files(entry.id)
        rules = load_rules(client, entry)
        return cls(files, [_readings(file.name, rules) for file in files], rules, entry.flags.movie)

    def for_episode(self, episode: int | None) -> list[FileEntry]:
        """Files for a video's episode; all of them for numberless videos and movies.

        Either of the video's numbers, its own or its renumbered one, may fall in either
        of a file's ranges, so renumbering works in both directions.
        """
        if episode is None or self.movie:
            return self.files
        numbers = {episode}
        renumbered = renumber(self.rules, episode)
        if renumbered is not None:
            numbers.add(renumbered)
        return [
            file
            for file, spans in zip(self.files, self.ranges, strict=True)
            if any(first <= number <= last for first, last in spans for number in numbers)
        ]


def parse_episode(filename: str) -> int | None:
    """Parse a single numeric episode with anitopy, falling back to guessit.

    anitopy extracts episode number in some cases where guessit won't,
    it also runs faster and is the same library that Jimaku uses.
    """
    parsed = anitopy.parse(filename)
    if parsed is not None:
        episode = parsed.get("episode_number")
        if isinstance(episode, str) and episode.isdecimal():
            return int(episode)

    episode = guessit.guessit(filename).get("episode")
    if isinstance(episode, int):
        return episode
    return None


def is_episode_type(filename: str) -> bool:
    """Whether guessit reads a filename as an episode, numbered or not.

    Pass the bare filename: guessit also reads directories, so a parent such as
    `Season 1` would make a movie or special look like an episode.
    """
    return guessit.guessit(filename).get("type") == "episode"


def parse_episode_range(filename: str) -> tuple[int, int] | None:
    """The first and last episode a remote filename names, as jimaku's filter reads it."""
    episode = (anitopy.parse(filename) or {}).get("episode_number")
    if episode is None:
        guessed = guessit.guessit(filename).get("episode")
        return (guessed, guessed) if isinstance(guessed, int) else None
    numbers = [episode] if isinstance(episode, str) else list(episode)
    if not numbers or not numbers[0].isdecimal():
        return None
    first = int(numbers[0])
    last = int(numbers[1]) if len(numbers) > 1 and numbers[1].isdecimal() else first
    return first, last


def parse_release(filename: str) -> str | None:
    """The release group, falling back to the streaming service.

    guessit files a name like "Netflix" under either key depending on the rest of
    the filename, so the first key that resolves to a name wins.
    """
    # TODO: A disposition flag can win the release_group key. guessit reads
    # `Show.S01E01.1080p.AMZN.WEB-DL.ja[sdh].srt` as release_group "sdh", burying
    # streaming_service "Amazon Prime" -- so `--release Amazon` matches nothing, and
    # --rename writes `stem.sdh.ja.srt`, the mislabeled-track collision. Skipping
    # `sdh`/`cc`/`hi`/`forced` here needs the label slugged too, or "Amazon Prime"
    # puts a space in the filename.
    guessit_out = guessit.guessit(filename)
    for key in ["release_group", "streaming_service"]:
        release = guessit_out.get(key)
        if isinstance(release, str):
            return release
    return None


def renumber(rules: Sequence[Rule], episode: int) -> int | None:
    """Where the entry's relation rules move an episode; None if no rule covers it."""
    for rule in rules:
        source, destination = rule.source, rule.destination
        if episode < source.first or (source.last is not None and episode > source.last):
            continue
        found = destination.first if destination.single else destination.first + episode - source.first
        if destination.last is None or found <= destination.last:
            return found
    return None


def load_rules(client: JimakuClient, entry: Entry) -> list[Rule]:
    """anime-relations rules renumbering this entry's episodes within the entry.

    Movies and entries without an AniList ID have none and make no request.
    """
    if entry.anilist_id is None or entry.flags.movie:
        return []
    relations = _relations(client)
    return [
        Rule(EpisodeRange.from_json(item["source"]), EpisodeRange.from_json(item["destination"]))
        for item in relations["relations"].get(str(entry.anilist_id), [])
        if item["anilist_id"] == entry.anilist_id
    ]


def _readings(filename: str, rules: Sequence[Rule]) -> list[tuple[int, int]]:
    """A remote file's own episode range, then its renumbered range if both ends move."""
    span = parse_episode_range(filename)
    if span is None:
        return []
    first, last = renumber(rules, span[0]), renumber(rules, span[1])
    if first is None or last is None:
        return [span]
    return [span, (first, last)]


def _relations(client: JimakuClient) -> dict[str, Any]:
    """jimaku's anime-relations document, cached in the app directory by content hash."""
    path = Path(typer.get_app_dir(APP_NAME)) / RELATIONS_FILE
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cached["hash"], str) or not isinstance(cached["relations"], dict):
            cached = None
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    try:
        if cached is not None and client.get_anime_relations_hash() == cached["hash"]:
            return cached
        relations = client.get_anime_relations()
    except TRANSFER_ERRORS:
        if cached is None:
            raise
        logger.debug("Using cached anime relations", exc_info=True)
        return cached
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = temporary_path(path, "jimaku")
        try:
            partial.write_text(json.dumps(relations), encoding="utf-8")
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not cache anime relations", exc_info=True)
    return relations
