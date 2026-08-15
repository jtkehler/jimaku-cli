import os
import tomllib
from pathlib import Path
from typing import Any

import typer
from platformdirs import user_config_path

APP_NAME = "jimaku"
CONFIG_FILENAME = "config.toml"
API_KEY_ENV_VAR = "JIMAKU_API_KEY"

app = typer.Typer()


class ConfigError(Exception):
    """The config file exists but could not be read or understood."""


def get_config_path() -> Path:
    return user_config_path(APP_NAME) / CONFIG_FILENAME


def load_config() -> dict[str, Any]:
    """The config file as a plain dict, empty if there is none.

    Deliberately unvalidated: the file only overrides option defaults, which are
    declared on the commands themselves.
    """
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc


config = load_config()
api_key = os.environ.get(API_KEY_ENV_VAR) or config.get("api_key")


# Not `config`: that name is the dict above, and rebinding it here would leave
# `from .config import config` silently importing this function instead.
@app.command("config")
def config_path():
    """Display the path to the config file."""
    print(f"Config file path: {get_config_path()}")
