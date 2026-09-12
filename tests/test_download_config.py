"""Test config defaults in fresh processes because options bind at import time."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("flags", [[], ["--all"], ["--no-all"]])
def test_all_flags_override_config(tmp_path, configured, flags):
    config_dir = tmp_path / "config" / "jimaku"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        f"[download]\nall = {str(configured).lower()}\n", encoding="utf-8"
    )
    library = tmp_path / "videos"
    library.mkdir()
    (library / "[Group] Show - 01.mkv").touch()
    code = """
import json
import sys
from pathlib import Path
from typer.testing import CliRunner
from jimaku_cli import cli
from jimaku_cli.api import FileEntry

class Client:
    api_key = 'fixture-key'
    def get_files(self, *args):
        return [FileEntry('https://example.invalid/' + name, name, 8,
                          '2026-01-01T00:00:00Z')
                for name in ('[A] Show - 01.srt', '[B] Show - 01.srt')]
    def download_file(self, url, dest):
        dest.write_bytes(b'subtitle')

cli.JimakuClient = lambda **kwargs: Client()
result = CliRunner().invoke(cli.app, [
    'download', sys.argv[1], '--id', '1', '--release', 're:.',
    '--no-rename', '--no-overwrite', '--no-strip-ih', '--no-align',
    *json.loads(sys.argv[2]),
])
print(json.dumps({'exit': result.exit_code, 'stderr': result.stderr,
                  'stdout': result.stdout,
                  'files': sorted(p.name for p in Path(sys.argv[1]).glob('*.srt'))}))
"""
    process = subprocess.run(
        [sys.executable, "-B", "-c", code, str(library), json.dumps(flags)],
        env=dict(
            os.environ,
            XDG_CONFIG_HOME=str(config_dir.parent),
            JIMAKU_API_KEY="fixture-key",
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["exit"] == 0, result["stderr"]
    assert result["stdout"] == ""
    all_enabled = configured if not flags else flags == ["--all"]
    expected = ["[A] Show - 01.srt", "[B] Show - 01.srt"] if all_enabled else ["[A] Show - 01.srt"]
    assert result["files"] == expected


@pytest.mark.parametrize(
    "configured, flags, expected",
    [
        (None, [], "srt"),
        ("ass", [], "ass"),
        ("ASS", [], "ass"),
        ("ass", ["--prefer-format", "srt"], "srt"),
        ("srt", ["--prefer-format", "ass"], "ass"),
    ],
)
def test_prefer_format_config_default_and_cli_override(
    tmp_path, configured, flags, expected
):
    config_dir = tmp_path / "config" / "jimaku"
    config_dir.mkdir(parents=True)
    if configured is not None:
        (config_dir / "config.toml").write_text(
            f'[download]\nprefer_format = "{configured}"\n', encoding="utf-8"
        )
    library = tmp_path / "videos"
    library.mkdir()
    (library / "[Group] Show - 01.mkv").touch()
    code = """
import json
import sys
from pathlib import Path
from typer.testing import CliRunner
from jimaku_cli import cli
from jimaku_cli.api import FileEntry

class Client:
    api_key = 'fixture-key'
    def get_files(self, *args):
        return [FileEntry('https://example.invalid/' + name, name, 8,
                          '2026-01-01T00:00:00Z')
                for name in ('[Group] Show - 01.ass', '[Group] Show - 01.srt')]
    def download_file(self, url, dest):
        dest.write_bytes(b'subtitle')

cli.JimakuClient = lambda **kwargs: Client()
result = CliRunner().invoke(cli.app, [
    'download', sys.argv[1], '--id', '1', *json.loads(sys.argv[2]),
])
print(json.dumps({'exit': result.exit_code, 'stderr': result.stderr,
                  'stdout': result.stdout,
                  'files': sorted(p.name for p in Path(sys.argv[1]).iterdir()
                                  if p.suffix != '.mkv')}))
"""
    process = subprocess.run(
        [sys.executable, "-B", "-c", code, str(library), json.dumps(flags)],
        env=dict(
            os.environ,
            XDG_CONFIG_HOME=str(config_dir.parent),
            JIMAKU_API_KEY="fixture-key",
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["exit"] == 0, result["stderr"]
    assert result["stdout"] == ""
    assert result["files"] == [f"[Group] Show - 01.{expected}"]
    assert (library / result["files"][0]).read_bytes() == b"subtitle"
