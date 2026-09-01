import typer

from .output import log_error

app = typer.Typer()


@app.command()
def search(ctx: typer.Context, query: str):
    """Search for subtitle entries."""
    try:
        response = ctx.obj.search_entries(query=query)
        print(response)
    except Exception as e:
        log_error(f"API connection failed: {e}")
        raise typer.Exit(1)
