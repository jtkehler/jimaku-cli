import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import guessit
import requests

from .api import FileEntry, JimakuClient, JimakuError

# Anything a request or a write to disk can fail with. Caught per file so one bad
# episode does not abort a nightly run.
TRANSFER_ERRORS = (JimakuError, requests.RequestException, OSError)

VIDEO_EXTS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".ts", ".ogm"})
SUBTITLE_EXTS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub"})

@dataclass(frozen=True)
class DownloadSpec:
    entry_id: int
    directory: Path
    release: list[str] = field(default_factory=list)
    offset: int = 0
    lang: str = "ja"
    align: bool = False
    strip_ih: bool = False
    overwrite: bool = False
    all: bool = False

    def to_args(self) -> list[str]:
        """Render as argv for `jimaku download`. Round-trips with the Typer signature."""
        args = ["jimaku", "download", str(self.directory.expanduser().resolve())]
        args += ["--id", str(self.entry_id)]
        for pattern in self.release:
            args += ["--release", pattern]
        if self.offset:
            args += ["--offset", str(self.offset)]
        if self.overwrite:
            args.append("--overwrite")
        if self.align:
            args.append("--align")
        if self.strip_ih:
            args.append("--strip-ih")
        return args


def parse_episode(filename: str) -> int | None:
    episode = guessit.guessit(filename).get("episode")
    return episode if isinstance(episode, int) else None

def parse_release(filename: str) -> str | None:
    """The release group, falling back to the streaming service.

    guessit files a name like "Netflix" under either key depending on the rest of
    the filename, so the first key that resolves to a name wins.
    """
    guessit_out = guessit.guessit(filename)
    for key in ["release_group", "streaming_service"]:
        release = guessit_out.get(key)
        if isinstance(release, str):
            return release
    return None

def match(pattern: str, filename: str) -> bool:
    """Returns whether or not single release pattern matches filename.
 
    Pattern starting with `re:` parses as regex, otherwise matches guessit release group or streaming service (case insensitive).
    """
    if pattern.startswith("re:"):
        return re.search(pattern[3:], filename, re.IGNORECASE) is not None
    else:
        release = parse_release(filename)
        return release is not None and pattern.lower() == release.lower()

def select(
    file_candidates: Sequence[FileEntry], release_patterns: Sequence[str]
) -> dict[str, FileEntry]:
    """The file chosen for each release pattern, in the order the patterns were given.

    Returns dict of { release pattern : file entry }, choosing most recent entry when there are multiple per release.
    """
    selected: dict[str, FileEntry] = {}
    for pattern in release_patterns:
        matched = [file for file in file_candidates if match(pattern, file.name)]
        if matched:
            selected[pattern] = max(matched, key=lambda f: f.last_modified)
    return selected

def has_subtitle(video: Path, siblings: Iterable[Path]) -> Path | None:
    """An already-present subtitle for this video, ignoring provider/language suffixes.

    Everything between the video's stem and the extension is ignored, which is what
    keeps a changed --release from triggering a full re-download. Matching on the
    literal stem plus a dot stops `Show - 01.mkv` from claiming
    `Show - 01 (1080p).ja.srt`.
    """
    prefix = f"{video.stem}."
    for sibling in siblings:
        if sibling.name.startswith(prefix) and sibling.suffix.lower() in SUBTITLE_EXTS:
            return sibling
    return None

def output_name(
    video: Path, remote_name: str, lang: str, provider: str | None
) -> str:
    """Sidecar filename: the video's stem, then the provider, then the language.

    Language goes last because media servers scan suffix tokens right-to-left and
    take the first one that resolves as a language.
    """
    parts = [video.stem]
    if provider:
        parts.append(provider)
    parts.append(lang)
    return ".".join(parts) + Path(remote_name).suffix


def run_download(client: JimakuClient, spec: DownloadSpec) -> int:
    """Download one subtitle per video in the directory. Returns an exit code."""
    directory = spec.directory.expanduser()
    if not directory.is_dir():
        print(f"error: {directory} is not a directory")
        return 1

    siblings = sorted(path for path in directory.iterdir() if path.is_file())
    videos = [path for path in siblings if path.suffix.lower() in VIDEO_EXTS]
    if not videos:
        print(f"error: no video files in {directory}")
        return 1

    try:
        remote_files = client.get_files(spec.entry_id)
    except TRANSFER_ERRORS as exc:
        print(f"error: could not list files for entry {spec.entry_id}: {exc}")
        return 1

    # Bucket the remote listing by episode once. Archives and other non-subtitle
    # uploads are dropped here rather than filtered per episode.
    buckets: dict[int, list[FileEntry]] = defaultdict(list)
    for remote in remote_files:
        if Path(remote.name).suffix.lower() not in SUBTITLE_EXTS:
            continue
        episode = parse_episode(remote.name)
        if episode is not None:
            buckets[episode].append(remote)

    parsed = [(video, parse_episode(video.name)) for video in videos]
    parsed.sort(key=lambda item: (item[1] is None, item[1] or 0, item[0].name))

    problems = 0
    for video, episode in parsed:
        if episode is None:
            print(f"warn  {video.name}: could not determine an episode number")
            problems += 1
            continue

        # Skip-existing is checked before selection, so a directory that is already
        # complete stays quiet on a nightly run even if --release no longer matches
        # anything remote.
        existing = has_subtitle(video, siblings)
        if existing is not None and not spec.overwrite:
            print(f"skip  {video.name} -> {existing.name} already present")
            continue

        remote_episode = episode + spec.offset
        selections = select(buckets.get(remote_episode, []), spec.release)
        if not selections:
            print(
                f"warn  {video.name}: no subtitle matched for episode {remote_episode}"
            )
            problems += 1
            continue

        # One file per release. The provider goes into the filename, so several
        # releases of the same episode sit side by side instead of overwriting.
        written: list[Path] = []
        for provider, remote in selections.items():
            target = directory / output_name(video, remote.name, spec.lang, provider)
            try:
                client.download_file(remote.url, target)
            except TRANSFER_ERRORS as exc:
                print(f"error {video.name} -> {target.name}: download failed: {exc}")
                problems += 1
                continue
            written.append(target)
            print(f"ok    {video.name} -> {target.name}")

        # Same provider and extension means the rename landed on the existing file.
        # A different one leaves both behind, and the media server would show two
        # tracks. Not deleted automatically: the leftover may be a hand-made subtitle
        # in another language, which --overwrite has no business discarding.
        if existing is not None and existing not in written:
            print(f"      note: {existing.name} is still there; remove it by hand")

    return 1 if problems else 0
