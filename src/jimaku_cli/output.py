"""Progress reporting, which belongs to stderr alone.

`download` writes nothing to stdout and `search`'s stdout is its emitted command, so
every line about the tool's own progress goes to stderr -- errors included, since an
error is progress reporting too.
"""

import typer

# Padded into a column: `[download]` is the longest tag at ten characters.
_TAG_WIDTH = len("[download]")

_OUTCOME_COLORS = {
    "download": typer.colors.GREEN,
    "skip": None,
    "missing": typer.colors.YELLOW,
    "failed": typer.colors.RED,
}


def log_status(tag: str, message: str) -> None:
    """Report one file's outcome. Empty details drop out rather than pad the line."""
    formatted_tag = typer.style(
        f"[{tag}]".ljust(_TAG_WIDTH),
        fg=_OUTCOME_COLORS[tag],
        bold=True,
        dim=True if tag == "skip" else None,
    )
    typer.echo(f"{formatted_tag} {message}".rstrip(), err=True)


def log_error(message: str) -> None:
    """Report a failure that ends the run, rather than one file's outcome."""
    prefix = typer.style("error:", fg=typer.colors.RED, bold=True)
    typer.echo(f"{prefix} {message}", err=True)


def summary(tally: str) -> None:
    """The run's closing tally. Callers leave it out when there is nothing to tally."""
    typer.echo(f"summary: {tally}", err=True)
