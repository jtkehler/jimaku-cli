import pytest
from typer import rich_utils
from typer.testing import CliRunner

from jimaku_cli import cli


@pytest.mark.parametrize("command", [[], ["download"], ["search"]])
@pytest.mark.parametrize(
    "environment, terminal, colored",
    [({}, True, True), ({}, False, False), ({"NO_COLOR": "1"}, True, False),
     ({"TERM": "dumb"}, True, False)],
)
def test_help_uses_jimaku_accents(
    command: list[str], environment: dict[str, str], terminal: bool, colored: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "api_key", "fixture-key")
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", terminal)
    monkeypatch.setattr(rich_utils, "COLOR_SYSTEM", "auto")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    result = CliRunner().invoke(cli.app, [*command, "--help"], color=True)

    assert result.exit_code == 0, result.output
    assert ("38;2;196;160;88" in result.stdout) == colored
    assert ("38;2;51;126;204" in result.stdout) == colored
