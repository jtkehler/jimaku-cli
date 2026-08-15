import typer

from .api import JimakuClient
from .config import api_key
from .config import app as config_app
from .download import app as download_app
from .files import app as files_app
from .search import app as search_app

app = typer.Typer(no_args_is_help=True)

# Commands that must work before an API key is configured.
NO_CLIENT_COMMANDS = frozenset({"config"})


@app.callback()
def main(ctx: typer.Context):
    """A CLI for downloading subtitles from jimaku.cc."""
    if ctx.invoked_subcommand in NO_CLIENT_COMMANDS:
        return
    if not api_key:
        print(
            "No API key found. Please set the JIMAKU_API_KEY environment variable or add one to the config file (see `jimaku config`)."
        )
        raise typer.Exit(code=1)
    ctx.obj = JimakuClient(api_key=api_key)


app.add_typer(config_app)
app.add_typer(download_app)
app.add_typer(search_app)
app.add_typer(files_app)
