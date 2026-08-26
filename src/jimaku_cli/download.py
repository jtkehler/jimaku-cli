import re
from collections import Counter
from pathlib import Path
from typing import Annotated

import anitopy
import guessit
import requests
import typer

from . import postprocess
from .api import FileEntry, JimakuClient, JimakuError
from .config import config
from .output import error, status, summary

# Expected network, API, and filesystem failures. Caught per file so one bad
# transfer does not abort the rest of a batch run.
TRANSFER_ERRORS = (JimakuError, requests.RequestException, OSError)

VIDEO_EXTS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".ts", ".ogm"})
SUBTITLE_EXTS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub"})

LANG = "ja"

# The tags worth a cron mail: something arrived, or something broke. The rest are
# what you ask for when a run was silent and you would like to know why.
SHOWN_OUTCOMES = frozenset({"download", "error"})

# Every outcome, in the order the summary counts them.
OUTCOME_LABELS = (
    ("download", "downloaded"),
    ("skip", "skipped"),
    ("missing", "missing"),
    ("error", "errors"),
)

app = typer.Typer()

download_config = config.get("download", {})


@app.command()
def download(
    ctx: typer.Context,
    entry_id: Annotated[int, typer.Option("--id", help="jimaku entry ID.")],
    directory: Annotated[
        Path, typer.Argument(help="Directory of video files to subtitle.")
    ] = Path("."),
    release: Annotated[
        list[str],
        typer.Option(
            help=(
                "Release to fetch, repeatable; every one that matches is downloaded, "
                "each to its own file. Matched case-insensitively against the release "
                "group or streaming service in the remote filename, or prefix with "
                "`re:` for a regex. Omit to take whatever is there."
            ),
        ),
    ] = download_config.get("release", []),  # noqa: B008
    rename: Annotated[
        bool,
        typer.Option(
            help="Rename downloaded subtitles to match their video files.",
        ),
    ] = download_config.get("rename", False),
    overwrite: Annotated[
        bool,
        typer.Option(
            help="Re-download episodes that already have subtitles.",
        ),
    ] = download_config.get("overwrite", False),
    align: Annotated[
        bool,
        typer.Option(
            help="Align subtitles to the video's audio with ffsubsync.",
        ),
    ] = download_config.get("align", False),
    strip_ih: Annotated[
        bool,
        typer.Option(
            help=(
                "Remove hearing-impaired annotations, and ruby readings written "
                "as halfwidth-parenthesised kana after kanji or as HTML <ruby>. "
                "Applies to .srt, .ass and .ssa; .vtt and .sub are left alone."
            ),
        ),
    ] = download_config.get("strip_ih", False),
    download_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Download all matching subtitle files. When disabled, only the best match is downloaded",
        ),
    ] = download_config.get("all", False),
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Report skipped and missing episodes as well.",
        ),
    ] = False,
):
    """Download subtitles for the video files in a directory."""
    client: JimakuClient = ctx.obj
    counts: Counter[str] = Counter()

    def record(outcome: str, subject: str, *details: str) -> None:
        """Count one file's outcome, and report it if its tag is one that prints."""
        counts[outcome] += 1
        if verbose or outcome in SHOWN_OUTCOMES:
            status(outcome, subject, *details)

    directory = directory.expanduser()
    if not directory.is_dir():
        error(f"{directory} is not a directory")
        raise typer.Exit(1)

    siblings = sorted(path for path in directory.iterdir() if path.is_file())
    videos = [path for path in siblings if path.suffix.lower() in VIDEO_EXTS]
    if not videos:
        error(f"no video files in {directory}")
        raise typer.Exit(1)

    for video in videos:
        episode = parse_episode(video.name)
        if episode is None and guessit.guessit(video).get("type") == "episode":
            record("error", video.name, "could not determine an episode number")
            continue
        try:
            remote_files = client.get_files(entry_id, episode)
        except TRANSFER_ERRORS as e:
            record(
                "error",
                video.name,
                f"could not retrieve files for episode {episode}: {e}",
            )
            continue

        filtered = filter_release(remote_files, release)
        to_download = filtered if download_all else filtered[:1]

        if not to_download:
            if release:
                record(
                    "missing", video.name, f"no matching release for episode {episode}"
                )
            else:
                record("missing", video.name, f"no subtitle for episode {episode}")
            continue

        for file in to_download:
            remote_release = parse_release(file.name)
            output_filename = (
                output_name(video, file.name, remote_release) if rename else file.name
            )
            output_path = directory / output_filename
            target = "" if output_path.name == file.name else f"-> {output_path.name}"
            # TODO: Deduplicate same-path candidates within a run so --overwrite
            # writes only the highest-ranked remote subtitle to each target.
            if output_path.exists() and not overwrite:
                record("skip", file.name, target, "already present")
                continue

            try:
                client.download_file(file.url, output_path)
            except TRANSFER_ERRORS as e:
                record("error", file.name, target, f"download failed: {e}")
                continue
            # download_file installs the completed file atomically, so reporting
            # only after it returns keeps this line from claiming a write that did
            # not land.
            record("download", file.name, target)

            # Before aligning, not after. ffsubsync correlates cue timings against
            # the reference, and the cues this drops -- sound effects, music
            # markers -- are the ones with no speech under them, so removing them
            # sharpens the signal rather than costing it anchors. Nothing that
            # survives moves, so no true anchor is lost either way.
            if strip_ih:
                try:
                    postprocess.strip_ih(output_path)
                # Parse errors, unreadable encodings and the filesystem: report
                # and move on rather than discard a subtitle that downloaded.
                except Exception as e:  # noqa: BLE001
                    record("error", output_path.name, f"strip failed: {e}")

            # Its own try, so a failed strip still gets aligned and a failed
            # align still leaves the stripped subtitle in place.
            if align:
                try:
                    postprocess.sync_subtitle(output_path, video)
                # ffsubsync reaches ffmpeg, the filesystem and a stack of parsers, so
                # its failure modes are not worth enumerating: report and move on
                # rather than discard a subtitle that downloaded successfully.
                except Exception as e:  # noqa: BLE001
                    record("error", output_path.name, f"alignment failed: {e}")

    # The summary tallies whatever the run was willing to print, so it cannot drift
    # from the lines above it -- and a run that wrote nothing and broke nothing has
    # an empty tally and stays silent.
    tallied = [
        (outcome, label)
        for outcome, label in OUTCOME_LABELS
        if verbose or outcome in SHOWN_OUTCOMES
    ]
    if any(counts[outcome] for outcome, _ in tallied):
        summary(", ".join(f"{counts[outcome]} {label}" for outcome, label in tallied))

    # In step with visibility deliberately: an outcome not worth printing is not
    # worth failing over, and anything worth failing over gets printed.
    raise typer.Exit(1 if counts["error"] else 0)


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


def match(pattern: str, filename: str) -> bool:
    """Returns whether or not single release pattern matches filename.

    Pattern starting with `re:` parses as regex, otherwise matches guessit release group or streaming service (case insensitive).
    """
    if pattern.startswith("re:"):
        return re.search(pattern[3:], filename, re.IGNORECASE) is not None
    else:
        release = parse_release(filename)
        return release is not None and pattern.lower() == release.lower()


def filter_release(
    file_candidates: list[FileEntry], release_patterns: list[str]
) -> list[FileEntry]:
    """Filter to subtitle files, ordering matches by release-pattern priority."""
    valid_subtitles = [
        file
        for file in file_candidates
        if Path(file.name).suffix.lower() in SUBTITLE_EXTS
    ]
    if not release_patterns:
        return valid_subtitles
    filtered: list[FileEntry] = []
    for pattern in release_patterns:
        matched = [file for file in valid_subtitles if match(pattern, file.name)]
        matched.sort(key=lambda file: file.name)
        matched.sort(key=lambda file: file.last_modified, reverse=True)
        filtered.extend(matched)
    return filtered


def output_name(video: Path, remote_name: str, provider: str | None) -> str:
    """Sidecar filename: the video's stem, then the provider, then the language.

    Language goes last because media servers scan suffix tokens right-to-left and
    take the first one that resolves as a language.
    """
    parts = [video.stem]
    if provider:
        parts.append(provider.lower())
    parts.append(LANG)
    return ".".join(parts) + Path(remote_name).suffix.lower()
