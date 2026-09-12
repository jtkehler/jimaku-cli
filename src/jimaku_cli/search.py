"""Interactive setup; stdout contains only the resulting download command."""

import re
import shlex
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Annotated, Literal

import anitopy
import guessit
import typer

from .api import JimakuClient
from .download import (
    TRANSFER_ERRORS,
    VIDEO_EXTS,
    download,
    download_config,
    filter_release,
    parse_episode,
    parse_release,
)
from .output import inline, log_error

app = typer.Typer()


@app.command()
def search(
    ctx: typer.Context,
    directory: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            readable=True,
            resolve_path=True,
            help="Directory of video files to set up.",
        ),
    ] = Path("."),
    run_download: Annotated[
        bool,
        typer.Option(
            "--download/--no-download",
            "-d",
            help="Also download subtitles after setup; otherwise only print the command.",
        ),
    ] = False,
    anime: Annotated[
        bool,
        typer.Option(
            help="Search anime entries; --no-anime searches live action.",
        ),
    ] = True,
    prefer_format: Annotated[
        Literal["srt", "ass", "ssa", "vtt", "sub"],
        typer.Option(
            case_sensitive=False,
            help="Preferred format within each release.",
        ),
    ] = download_config.get("prefer_format", "srt"),
    download_all: Annotated[
        bool,
        typer.Option(
            "--all/--no-all",
            help="Download every matching subtitle.",
        ),
    ] = download_config.get("all", False),
    rename: Annotated[
        bool,
        typer.Option(
            help="Name subtitles after their video files.",
        ),
    ] = download_config.get("rename", False),
    overwrite: Annotated[
        bool,
        typer.Option(
            help="Re-download existing subtitles.",
        ),
    ] = download_config.get("overwrite", False),
    align: Annotated[
        bool,
        typer.Option(
            help="Align subtitles to the video's audio.",
        ),
    ] = download_config.get("align", False),
    strip_ih: Annotated[
        bool,
        typer.Option(
            help="Remove hearing-impaired annotations and ruby readings.",
        ),
    ] = download_config.get("strip_ih", False),
) -> None:
    """Choose an entry and releases; print a download command and optionally run it."""
    client: JimakuClient = ctx.obj
    try:
        videos: list[tuple[Path, int | None]] = [
            (path, parse_episode(path.name))
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTS
        ]
    except OSError as exc:
        log_error(f"could not read {directory}: {exc}")
        raise typer.Exit(1) from exc
    if not videos:
        log_error(f"no video files in {directory}")
        raise typer.Exit(1)
    # Numbered episodes first; numberless movies/specials last, ordered by name.
    videos.sort(key=lambda item: (item[1] is None, item[1] or 0, item[0].name))
    first = videos[0][0]
    parsed = anitopy.parse(first.name) or {}
    query = parsed.get("anime_title") or guessit.guessit(first.name).get("title")
    if not isinstance(query, str) or not query.strip():
        log_error(f"could not determine a title from {first.name}")
        raise typer.Exit(1)
    typer.echo(f"Searching for {inline(query)}…", err=True)
    try:
        entries = client.search_entries(query=query, anime=anime)
    except TRANSFER_ERRORS as exc:
        log_error(f"could not search entries: {exc}")
        raise typer.Exit(1) from exc
    if not entries:
        message = f"no entries found for {query!r}"
        if anime:
            message += ". Use --no-anime to search live action."
        log_error(message)
        raise typer.Exit(1)
    entry = entries[
        choose("Entry", [f"{entry.name} (ID {entry.id})" for entry in entries])
    ]

    releases: list[str] = []
    failed = False
    for video, episode in videos:
        if episode is None and guessit.guessit(video.name).get("type") == "episode":
            log_error(f"{video.name}: could not determine an episode number")
            failed = True
            continue
        try:
            files = client.get_files(entry.id, episode)
        except TRANSFER_ERRORS as exc:
            log_error(f"{video.name}: could not retrieve subtitles: {exc}")
            failed = True
            continue
        if releases and filter_release(files, releases, prefer_format):
            continue
        candidates = filter_release(files, [], prefer_format)
        if not candidates:
            log_error(f"{video.name}: no subtitles available")
            continue
        typer.echo(f"Subtitles for {inline(video.name)}:", err=True)
        selected = candidates[
            choose("Subtitle release", [file.name for file in candidates])
        ]
        releases.append(release_pattern(selected.name))

    if failed:
        raise typer.Exit(1)
    if not releases:
        log_error("no subtitle releases selected; no command generated")
        raise typer.Exit(1)

    command: list[str] = ["jimaku", "download", str(directory), "--id", str(entry.id)]
    for release in releases:
        command.extend(["--release", release])
    command.extend(["--prefer-format", prefer_format])
    # Record effective values, including false overrides of configuration defaults.
    for name, enabled in (
        ("all", download_all),
        ("rename", rename),
        ("overwrite", overwrite),
        ("align", align),
        ("strip-ih", strip_ih),
    ):
        command.append(f"--{'' if enabled else 'no-'}{name}")
    # Preserve shell argument data; echo strips ANSI sequences on redirected stdout.
    sys.stdout.write(shlex.join(command) + "\n")
    if run_download:
        download(
            ctx,
            entry_id=entry.id,
            directory=directory,
            release=releases,
            prefer_format=prefer_format,
            download_all=download_all,
            rename=rename,
            overwrite=overwrite,
            align=align,
            strip_ih=strip_ih,
            quiet=False,
            verbose=False,
        )


def release_pattern(filename: str) -> str:
    """Keep a parsed release, or match the stem with its episode generalized."""
    release = parse_release(filename)
    if release and not release.startswith("re:"):
        return release
    stem = Path(filename).stem
    pattern = re.escape(stem)
    episode = (anitopy.parse(filename) or {}).get("episode_number")
    if isinstance(episode, str) and episode.isdecimal():
        # Version suffixes stay literal; only the episode digits are candidates.
        candidates = list(
            re.finditer(
                rf"(?<!\w)(?:S\d+E)?({episode})(?:v\d+)?(?!\w)",
                stem,
                re.IGNORECASE,
            )
        )
        if len(candidates) == 1:
            start, end = candidates[0].span(1)
            episode_matches = guessit.guessit(filename, {"advanced": True}).get(
                "episode", []
            )
            if not isinstance(episode_matches, list):
                episode_matches = [episode_matches]
            if any(item.span == (start, end) for item in episode_matches):
                pattern = re.escape(stem[:start]) + r"\d+" + re.escape(stem[end:])
    # The shared matcher already filters extensions; format remains a preference.
    return "re:^" + pattern + r"\.[^.]+$"


def choose(message: str, labels: list[str]) -> int:
    """Use Typer's numbered prompt, keeping even input echoes off stdout."""
    for number, label in enumerate(labels, 1):
        typer.echo(f"{number}. {inline(label)}", err=True)
    # Click uses input() for the answer; its space and echo also belong on stderr.
    with redirect_stdout(sys.stderr):
        while True:
            number = typer.prompt(message, type=int, err=True)
            if 1 <= number <= len(labels):
                return number - 1
            log_error(f"Choose a number from 1 to {len(labels)}.")
