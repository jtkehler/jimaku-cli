from pathlib import Path
from typing import Annotated

import typer

from .api import JimakuClient
from .config import get_config_path, load_config
from .download import DownloadSpec, run_download

app = typer.Typer(no_args_is_help=True)

# Commands that must work before an API key is configured.
NO_CLIENT_COMMANDS = frozenset({"config"})


@app.callback()
def main(ctx: typer.Context):
    """A CLI for downloading subtitles from jimaku.cc."""
    if ctx.invoked_subcommand in NO_CLIENT_COMMANDS:
        return
    cfg = load_config()
    if not cfg.api_key:
        print(
            "No API key found. Please set the JIMAKU_API_KEY environment variable or add one to the config file (see `jimaku config`)."
        )
        raise typer.Exit(code=1)
    ctx.obj = JimakuClient(api_key=cfg.api_key)


@app.command()
def config():
    """Display the path to the config file."""
    path = get_config_path()
    print(f"Config file path: {path}")


@app.command()
def download(
    ctx: typer.Context,
    entry_id: Annotated[
        int, typer.Option("--id", help="jimaku entry ID.")
    ],
    directory: Annotated[
        Path, typer.Argument(help="Directory of video files to subtitle.")
    ] = Path("."),
    release: Annotated[
        list[str] | None,
        typer.Option(
            "--release",
            help=(
                "Release to fetch, repeatable; every one that matches is downloaded, "
                "each to its own file. Matched case-insensitively against the release "
                "group or streaming service in the remote filename, or prefix with "
                "`re:` for a regex. Omit to take whatever is there."
            ),
        ),
    ] = None,
    offset: Annotated[
        int,
        typer.Option(
            "--offset",
            help="Shift local episode numbers to remote ones; -12 maps local 13 to remote 1.",
        ),
    ] = 0,
    lang: Annotated[
        str,
        typer.Option(
            "--lang",
            help=(
                "Language suffix for the written file. Naming only -- jimaku's API "
                "has no language filter, so this does not narrow what is fetched."
            ),
        ),
    ] = "ja",
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Re-download episodes that already have subtitles."),
    ] = False,
    align: Annotated[
        bool,
        typer.Option("--align", help="Accepted but not implemented yet; has no effect."),
    ] = False,
    strip_ih: Annotated[
        bool,
        typer.Option("--strip-ih", help="Accepted but not implemented yet; has no effect."),
    ] = False,
):
    """Download subtitles for the video files in a directory."""
    spec = DownloadSpec(
        entry_id=entry_id,
        directory=directory,
        release=split_patterns(release or []),
        offset=offset,
        lang=lang,
        align=align,
        strip_ih=strip_ih,
        overwrite=overwrite,
    )
    raise typer.Exit(run_download(ctx.obj, spec))


def split_patterns(values: list[str]) -> list[str]:
    """Expand comma-separated --release values, a convenience for hand-typed input.

    Regexes are left alone: their quantifiers contain commas.
    """
    patterns = []
    for value in values:
        if value.startswith("re:"):
            patterns.append(value)
            continue
        patterns.extend(part.strip() for part in value.split(",") if part.strip())
    return patterns


@app.command()
def search(ctx: typer.Context, query: str):
    """Search for subtitle entries."""
    try:
        response = ctx.obj.search_entries(query=query)
        print(response)
    except Exception as e:
        print(f"API connection failed: {e}")
        raise typer.Exit(code=1)


@app.command()
def files(ctx: typer.Context, entry_id: int, episode: int | None = None):
    """List files for a subtitle entry."""
    try:
        response = ctx.obj.get_files(entry_id=entry_id, episode=episode)
        print(response)
    except Exception as e:
        print(f"API connection failed: {e}")
        raise typer.Exit(code=1)
