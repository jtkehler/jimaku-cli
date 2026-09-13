"""Options bind at import time, so exercise configured search in a fresh process."""

import json
import os
import shlex
import subprocess
import sys

import pytest


@pytest.mark.parametrize("override", [False, True])
def test_search_captures_download_defaults_but_builds_its_own_releases(tmp_path, override):
    config_dir = tmp_path / "config" / "jimaku"
    config_dir.mkdir(parents=True)
    switches = ["all", "rename", "overwrite", "align", "strip-ih"]
    (config_dir / "config.toml").write_text(
        '[download]\nprefer_format = "ASS"\nrelease = ["not the chosen release"]\n'
        + "".join(f"{name.replace('-', '_')} = true\n" for name in switches),
        encoding="utf-8",
    )
    library = tmp_path / "videos"
    library.mkdir()
    (library / "Show - 01.mkv").touch()
    flags = (
        ["--prefer-format", "srt", *(f"--no-{name}" for name in switches)]
        if override else []
    )
    code = """
import json
import subprocess
import sys
from typer.testing import CliRunner
from unittest.mock import patch
from jimaku_cli.api import Entry, FileEntry
from jimaku_cli.search import app

class Client:
    def search_entries(self, **kwargs):
        return [Entry(42, 'Show', '2026-01-01T00:00:00Z')]
    def get_files(self, *args):
        return [FileEntry('https://example.invalid/subtitle', name, 8,
                          '2026-01-01T00:00:00Z') for name in
                ('[SrtGroup] Show - 01.srt', '[AssGroup] Show - 01.ass')]

def select(args, *, input, **options):
    row = input.splitlines()[0]
    return subprocess.CompletedProcess(args, 0, stdout=row + b'\\n')

with patch('jimaku_cli.search.subprocess.run', side_effect=select):
    result = CliRunner().invoke(app, [sys.argv[1], *json.loads(sys.argv[2])],
                                obj=Client())
print(json.dumps({'exit': result.exit_code, 'stdout': result.stdout,
                  'stderr': result.stderr}))
"""
    process = subprocess.run(
        [sys.executable, "-B", "-c", code, str(library), json.dumps(flags)],
        env=dict(os.environ, XDG_CONFIG_HOME=str(config_dir.parent)),
        capture_output=True, text=True, timeout=15, check=False,
    )

    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["exit"] == 0, result["stderr"]
    command = shlex.split(result["stdout"])
    assert command[command.index("--prefer-format") + 1] == ("srt" if override else "ass")
    assert command[command.index("--release") + 1] == ("SrtGroup" if override else "AssGroup")
    assert "not the chosen release" not in command
    assert all(f"--{'no-' if override else ''}{name}" in command for name in switches)
