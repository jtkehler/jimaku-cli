import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import user_config_path

APP_NAME = "jimaku"
CONFIG_FILENAME = "config.toml"
API_KEY_ENV_VAR = "JIMAKU_API_KEY"


class ConfigError(Exception):
    """The config file exists but could not be read or understood."""


@dataclass
class Config:
    api_key: str | None = None

def get_config_path() -> Path:
    return user_config_path(APP_NAME) / CONFIG_FILENAME

def load_config() -> Config:
    env_api_key = os.environ.get(API_KEY_ENV_VAR)
    path = get_config_path()
    if not path.exists():
        return Config(api_key=env_api_key)

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    api_key = data.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise ConfigError(f"{path}: 'api_key' must be a string")

    return Config(api_key=env_api_key or api_key)