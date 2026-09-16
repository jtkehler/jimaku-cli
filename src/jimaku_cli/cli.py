import os

import typer

from .api import JimakuClient
from .colorscheme import configure_typer
from .download import app as download_app
from .output import log_error
from .search import app as search_app

configure_typer()
app = typer.Typer(
    no_args_is_help=True,
    context_settings={"help_option_names": ["--help", "-h"]},
)


@app.callback()
def main(ctx: typer.Context):
    """A CLI for downloading subtitles from jimaku.cc."""
    api_key = os.environ.get("JIMAKU_API_KEY")
    if not api_key:
        log_error(
            "No API key found. Please set the JIMAKU_API_KEY environment variable."
        )
        raise typer.Exit(1)
    ctx.obj = JimakuClient(api_key=api_key)


app.add_typer(download_app)
app.add_typer(search_app)
