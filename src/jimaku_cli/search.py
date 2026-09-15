"""Interactive setup; stdout contains only the resulting download command."""

import os
import re
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Annotated, Literal

import anitopy
import guessit
import typer
from iterfzf import BUNDLED_EXECUTABLE

from .api import JimakuClient
from .colorscheme import FZF_COLORS
from .download import (
    DEFAULT_ALIGN,
    DEFAULT_DOWNLOAD_ALL,
    DEFAULT_OVERWRITE,
    DEFAULT_PREFER_FORMAT,
    DEFAULT_QUIET,
    DEFAULT_RENAME,
    DEFAULT_STRIP_IH,
    DEFAULT_VERBOSE,
    TRANSFER_ERRORS,
    VIDEO_EXTS,
    download,
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
            envvar="JIMAKU_SEARCH_DOWNLOAD",
            help="Also download subtitles after setup; otherwise only print the command.",
        ),
    ] = False,
    anime: Annotated[
        bool,
        typer.Option(
            envvar="JIMAKU_SEARCH_ANIME",
            help="Search anime entries; --no-anime searches live action.",
        ),
    ] = True,
    prefer_format: Annotated[
        Literal["srt", "ass", "ssa", "vtt", "sub"],
        typer.Option(
            case_sensitive=False,
            envvar="JIMAKU_SEARCH_PREFER_FORMAT",
            help="Preferred format within each release.",
        ),
    ] = DEFAULT_PREFER_FORMAT,
    download_all: Annotated[
        bool,
        typer.Option(
            "--all/--no-all",
            envvar="JIMAKU_SEARCH_ALL",
            help="Download every matching subtitle.",
        ),
    ] = DEFAULT_DOWNLOAD_ALL,
    rename: Annotated[
        bool,
        typer.Option(
            envvar="JIMAKU_SEARCH_RENAME",
            help="Name subtitles after their video files.",
        ),
    ] = DEFAULT_RENAME,
    overwrite: Annotated[
        bool,
        typer.Option(
            envvar="JIMAKU_SEARCH_OVERWRITE",
            help="Re-download existing subtitles.",
        ),
    ] = DEFAULT_OVERWRITE,
    align: Annotated[
        bool,
        typer.Option(
            envvar="JIMAKU_SEARCH_ALIGN",
            help="Align subtitles to the video's audio.",
        ),
    ] = DEFAULT_ALIGN,
    strip_ih: Annotated[
        bool,
        typer.Option(
            envvar="JIMAKU_SEARCH_STRIP_IH",
            help="Remove hearing-impaired annotations and ruby readings.",
        ),
    ] = DEFAULT_STRIP_IH,
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
    titles: list[object] = [
        (anitopy.parse(first.name) or {}).get("anime_title"),
        guessit.guessit(first.name).get("title"),
    ]
    if not anime:
        titles.reverse()
    queries: list[str] = []
    for title in titles:
        if isinstance(title, str) and title.strip() and title not in queries:
            queries.append(title)
    if not queries:
        log_error(f"could not determine a title from {first.name}")
    while True:
        if queries:
            query: str = queries.pop(0)
        else:
            # Keep the prompt and input echo out of the command payload.
            with redirect_stdout(sys.stderr):
                query = typer.prompt("Search title", err=True).strip()
            if not query:
                continue
        typer.echo(f"Searching for {inline(query)}…", err=True)
        try:
            entries = client.search_entries(query=query, anime=anime)
        except TRANSFER_ERRORS as exc:
            log_error(f"could not search entries: {exc}")
            raise typer.Exit(1) from exc
        if entries:
            break
        message = f"no entries found for {query!r}"
        if anime:
            message += ". Use --no-anime to search live action."
        log_error(message)
    [entry_index] = choose(
        "Entry", [f"{entry.name} (ID {entry.id})" for entry in entries]
    )
    entry = entries[entry_index]

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
        selected = choose(
            "Subtitle release", [file.name for file in candidates], multi=True
        )
        for index in selected:
            pattern = release_pattern(candidates[index].name)
            if pattern not in releases:
                releases.append(pattern)

    if failed:
        raise typer.Exit(1)
    if not releases:
        log_error("no subtitle releases selected; no command generated")
        raise typer.Exit(1)

    command: list[str] = ["jimaku", "download", str(directory), "--id", str(entry.id)]
    for release in releases:
        command.extend(["--release", release])
    if prefer_format != DEFAULT_PREFER_FORMAT:
        command.extend(["--prefer-format", prefer_format])
    for name, enabled, default in (
        ("all", download_all, DEFAULT_DOWNLOAD_ALL),
        ("rename", rename, DEFAULT_RENAME),
        ("overwrite", overwrite, DEFAULT_OVERWRITE),
        ("align", align, DEFAULT_ALIGN),
        ("strip-ih", strip_ih, DEFAULT_STRIP_IH),
    ):
        if enabled != default:
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
            quiet=DEFAULT_QUIET,
            verbose=DEFAULT_VERBOSE,
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


def choose(message: str, labels: list[str], *, multi: bool = False) -> list[int]:
    """Choose by hidden index; fzf's UI uses stderr, not the command payload."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"FZF_DEFAULT_OPTS", "FZF_DEFAULT_OPTS_FILE"}
    }
    color = (
        "--no-color"
        if env.get("NO_COLOR") or env.get("TERM") == "dumb"
        else f"--color={FZF_COLORS}"
    )
    header = (
        "Tab/Shift-Tab: mark in priority order; Enter: accept; Esc: cancel"
        if multi
        else "Type to search; Enter to select; Esc to cancel"
    )
    try:
        # iterfzf's callable waits before reading stdout and can fill the result pipe.
        # run() uses communicate() to drain it while fzf is still running.
        process = subprocess.run(
            [
                str(BUNDLED_EXECUTABLE or "fzf"),
                "--multi" if multi else "--no-multi",
                "--sort",
                f"--prompt={message}: ",
                f"--header={header}",
                color,
                "--delimiter=\t",
                "--with-nth=2..",
                "--height=40%",
                "--layout=reverse",
            ],
            input="".join(
                f"{index}\t{inline(label)}\n" for index, label in enumerate(labels)
            ).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
            check=False,
        )
    except KeyboardInterrupt as exc:
        raise typer.Abort() from exc
    except OSError as exc:
        log_error(f"could not run fzf: {exc}")
        raise typer.Exit(1) from exc
    if process.returncode != 0 or not process.stdout:
        raise typer.Abort()
    return [int(row.split(b"\t", 1)[0]) for row in process.stdout.splitlines()]
