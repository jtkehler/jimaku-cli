import typer

app = typer.Typer()


@app.command()
def files(ctx: typer.Context, entry_id: int, episode: int | None = None):
    """List files for a subtitle entry."""
    try:
        response = ctx.obj.get_files(entry_id=entry_id, episode=episode)
        print(response)
    except Exception as e:
        print(f"API connection failed: {e}")
        raise typer.Exit(code=1)
