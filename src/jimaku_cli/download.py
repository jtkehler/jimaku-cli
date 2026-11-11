import logging
import re
from pathlib import Path
from typing import Annotated, Literal

import typer

from . import postprocess
from .api import TRANSFER_ERRORS, FileEntry, JimakuClient
from .output import Reporter, diagnostic_session, log_error
from .parser import Listing, is_episode_type, parse_episode, parse_release

logger = logging.getLogger(__name__)

VIDEO_EXTS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".ts", ".ogm"})
SUBTITLE_EXTS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub"})

LANG = "ja"

DEFAULT_RELEASE: list[str] = []
DEFAULT_PREFER_FORMAT = "srt"
DEFAULT_DOWNLOAD_ALL = False
DEFAULT_RENAME = False
DEFAULT_OVERWRITE = False
DEFAULT_SYNC = False
DEFAULT_FFSUBSYNC_ARGS = ""
DEFAULT_QUIET = False
DEFAULT_VERBOSE = False

app = typer.Typer()


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
    ] = DEFAULT_RELEASE,
    prefer_format: Annotated[
        Literal["srt", "ass", "ssa", "vtt", "sub"],
        typer.Option(
            case_sensitive=False,
            help=(
                "Prefer this subtitle format within each release. Other formats "
                "remain eligible as fallbacks; --all still downloads every match."
            ),
        ),
    ] = DEFAULT_PREFER_FORMAT,
    rename: Annotated[
        bool,
        typer.Option(
            "--rename/--no-rename",
            "-r",
            help="Rename downloaded subtitles to match their video files.",
        ),
    ] = DEFAULT_RENAME,
    overwrite: Annotated[
        bool,
        typer.Option(
            help="Re-download episodes that already have subtitles.",
        ),
    ] = DEFAULT_OVERWRITE,
    sync: Annotated[
        bool,
        typer.Option(
            "--sync/--no-sync",
            "-s",
            help="Align subtitles to the video's audio with ffsubsync.",
        ),
    ] = DEFAULT_SYNC,
    ffsubsync_args: Annotated[
        str,
        typer.Option(
            help=(
                "Extra ffsubsync options as one quoted string, such as "
                "'--reference-stream 0:s:1'. Used only with --sync."
            ),
        ),
    ] = DEFAULT_FFSUBSYNC_ARGS,
    download_all: Annotated[
        bool,
        typer.Option(
            "--all/--no-all",
            help="Download all matching subtitle files. When disabled, only the best match is downloaded",
        ),
    ] = DEFAULT_DOWNLOAD_ALL,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Show only downloads, their basic processing results, and errors.",
        ),
    ] = DEFAULT_QUIET,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Add diagnostic logs and tracebacks for caught failures.",
        ),
    ] = DEFAULT_VERBOSE,
):
    """Download subtitles for the video files in a directory."""
    if quiet and verbose:
        raise typer.BadParameter("--quiet/-q cannot be combined with --verbose/-v.")
    client: JimakuClient = ctx.obj
    reporter = Reporter(quiet=quiet, verbose=verbose)
    ctx.with_resource(diagnostic_session(verbose=verbose, api_key=client.api_key))

    directory = directory.expanduser()
    if not directory.is_dir():
        log_error(f"{directory} is not a directory")
        raise typer.Exit(1)

    siblings = sorted(path for path in directory.iterdir() if path.is_file())
    videos = [path for path in siblings if path.suffix.lower() in VIDEO_EXTS]
    if not videos:
        log_error(f"no video files in {directory}")
        raise typer.Exit(1)

    try:
        listing = Listing.fetch(client, client.get_entry(entry_id))
    except TRANSFER_ERRORS as e:
        log_error(f"could not retrieve files for entry {entry_id}: {e}")
        logger.debug("Listing failed for entry %s", entry_id, exc_info=True)
        raise typer.Exit(1) from e

    for video in videos:
        episode = parse_episode(video.name)
        if episode is None and is_episode_type(video.name):
            reporter.record(
                "failed", video.name, "could not determine an episode number"
            )
            continue
        filtered = filter_release(listing.for_episode(episode), release, prefer_format)
        to_download = filtered if download_all else filtered[:1]

        if not to_download:
            reporter.record(
                "missing",
                video.name,
                f"no {'matching release' if release else 'subtitle'} for episode {episode}",
            )
            continue

        for file in to_download:
            remote_release = parse_release(file.name)
            output_filename = (
                output_name(video, file.name, remote_release) if rename else file.name
            )
            output_path = directory / output_filename
            # Normalize before both skip detection and processing; ffsubsync
            # derives its input format from the case-sensitive extension.
            output_path = output_path.with_suffix(output_path.suffix.lower())
            # TODO: Deduplicate same-path candidates within a run so --overwrite
            # writes only the highest-ranked remote subtitle to each target.
            if output_path.exists() and not overwrite:
                reporter.record(
                    "skip", file.name, "already present", target=output_path.name
                )
                continue

            try:
                client.download_file(file.url, output_path)
            except TRANSFER_ERRORS as e:
                reporter.record(
                    "failed",
                    file.name,
                    f"download failed: {e}",
                    target=output_path.name,
                )
                logger.debug("Download failed for %s", file.name, exc_info=True)
                continue
            # download_file installs the completed file atomically, so reporting
            # only after it returns keeps this line from claiming a write that did
            # not land.
            reporter.record(
                "download", file.name, "downloaded", target=output_path.name
            )

            if sync:
                reporter.step(output_path.name, "ffsubsync", "aligning…")
                try:
                    aligned = postprocess.sync_subtitle(
                        output_path,
                        video,
                        show_progress=reporter.interactive,
                        ffsubsync_args=ffsubsync_args,
                    )
                # ffsubsync reaches ffmpeg, the filesystem and a stack of parsers, so
                # its failure modes are not worth enumerating: report and move on
                # rather than discard a subtitle that downloaded successfully.
                except Exception as e:
                    reporter.record(
                        "failed", output_path.name, f"alignment failed: {e}"
                    )
                    logger.debug(
                        "Alignment failed for %s", output_path.name, exc_info=True
                    )
                else:
                    reporter.aligned(output_path.name, aligned)

    reporter.print_summary()
    raise typer.Exit(reporter.exit_code)


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
    file_candidates: list[FileEntry],
    release_patterns: list[str],
    prefer_format: str = DEFAULT_PREFER_FORMAT,
) -> list[FileEntry]:
    """Filter subtitles by release group, then sort by preferred format, recency, then name."""
    valid_subtitles = [
        file
        for file in file_candidates
        if Path(file.name).suffix.lower() in SUBTITLE_EXTS
    ]
    valid_subtitles.sort(key=lambda file: file.name)
    valid_subtitles.sort(key=lambda file: file.last_modified, reverse=True)
    valid_subtitles.sort(
        key=lambda file: Path(file.name).suffix.lower() != f".{prefer_format.lower()}"
    )
    if not release_patterns:
        return valid_subtitles
    filtered: list[FileEntry] = []
    for pattern in release_patterns:
        matched = [file for file in valid_subtitles if match(pattern, file.name)]
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
