"""Accent colors from https://jimaku.cc/static/base.css."""

from typer import rich_utils

GOLD = "#c4a058"  # --branding / --link-text
BLUE = "#337ecc"  # --form-input-focus-border
MUTED = "#9e9e9e"  # --text-muted
FZF_COLORS = (
    f"hl:{GOLD},hl+:{GOLD},prompt:{GOLD},pointer:{GOLD},marker:{GOLD},"
    f"info:{MUTED},header:{MUTED},separator:{MUTED},spinner:{BLUE},border:{BLUE}"
)


def configure_typer() -> None:
    """Theme help accents without changing terminal backgrounds or error colors."""
    rich_utils.STYLE_OPTION = f"bold {GOLD}"
    rich_utils.STYLE_SWITCH = f"bold {GOLD}"
    rich_utils.STYLE_NEGATIVE_OPTION = f"bold {BLUE}"
    rich_utils.STYLE_NEGATIVE_SWITCH = f"bold {BLUE}"
    rich_utils.STYLE_TYPES = f"bold {BLUE}"
    rich_utils.STYLE_USAGE = GOLD
    rich_utils.STYLE_OPTION_ENVVAR = f"dim {GOLD}"
    rich_utils.STYLE_OPTIONS_PANEL_BORDER = f"dim {BLUE}"
    rich_utils.STYLE_COMMANDS_PANEL_BORDER = f"dim {BLUE}"
    rich_utils.STYLE_COMMANDS_TABLE_FIRST_COLUMN = f"bold {GOLD}"
