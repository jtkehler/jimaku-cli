import sys

import questionary
import typer

from .api import JimakuClient
from .config import Config, ConfigError, load_config

app = typer.Typer(no_args_is_help=True)