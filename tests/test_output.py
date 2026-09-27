import pytest
import typer
from typer.testing import CliRunner

from jimaku_cli import output


@pytest.mark.parametrize("quiet", [False, True])
def test_outcomes_keep_visibility_summary_and_exit_independent(capsys, quiet):
    report = output.Reporter(quiet=quiet)
    report.record("download", "新作 [01].srt", "downloaded")
    report.record("skip", "existing.srt", "already present")
    report.record("missing", "Show.mkv", "no subtitle")
    report.step("新作 [01].srt", "strip_ih", "updated")
    assert report.exit_code == 0
    report.record("failed", "新作 [01].srt", "strip failed")
    report.record("failed", "新作 [01].srt", "alignment failed")
    report.print_summary()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "新作 [01].srt" in captured.err
    assert "[download]" in captured.err and "[failed]" in captured.err
    assert ("[skip]" in captured.err) == (not quiet)
    assert ("[missing]" in captured.err) == (not quiet)
    assert report.exit_code == 1
    expected = (
        "summary: 1 downloaded, 2 failed"
        if quiet
        else "summary: 1 downloaded, 1 skipped, 1 missing, 2 failed"
    )
    assert captured.err.splitlines()[-1] == expected


def test_hidden_outcomes_leave_a_noop_run_silent(capsys):
    report = output.Reporter(quiet=True)
    report.record("skip", "existing.srt")
    report.record("missing", "Show.mkv")
    report.print_summary()
    assert capsys.readouterr().err == ""
    assert report.exit_code == 0


def test_a_second_reporter_has_no_counts(capsys):
    output.Reporter().record("failed", "bad.srt")
    capsys.readouterr()
    second = output.Reporter()
    second.print_summary()
    assert second.exit_code == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "target, detail",
    [
        (None, "Show.srt downloaded"),
        ("Show.srt", "Show.srt downloaded"),
        ("動画.ja.srt", "Show.srt -> 動画.ja.srt downloaded"),
    ],
)
def test_optional_destination_does_not_add_empty_fields(capsys, target, detail):
    output.Reporter().record(
        "download",
        "Show.srt",
        "downloaded",
        target=target,
    )
    assert capsys.readouterr().err == f"[download] {detail}\n"


def test_control_characters_cannot_break_output_records(capsys):
    output.Reporter().record(
        "failed",
        "新作\n.srt",
        "bad\r\t\x1b[31m\x7f",
    )
    output.log_error("bad\npath")
    assert capsys.readouterr().err.splitlines() == [
        r"[failed]   新作\n.srt bad\r\t\x1b[31m\x7f",
        r"error: bad\npath",
    ]


@pytest.mark.parametrize(
    "environment, forced, colored",
    [
        ({}, False, False),
        ({}, True, True),
        ({"NO_COLOR": "1"}, True, False),
        ({"TERM": "dumb"}, True, False),
    ],
)
def test_output_respects_color_policy(monkeypatch, environment, forced, colored):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    app = typer.Typer()

    @app.command()
    def report():
        output.Reporter().record("skip", "existing.srt")
        output.Reporter().record("download", "new.srt")

    result = CliRunner().invoke(app, color=forced)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert ("\x1b[" in result.stderr) == colored
    if colored:
        assert "\x1b[1m" in result.stderr and "\x1b[2m" in result.stderr
        assert "\x1b[22m" not in result.stderr


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize(
    "status, expected",
    [
        ("updated", "updated"),
        ("unchanged", "unchanged"),
        ("unsupported", "not processed (unsupported format)"),
        ("preserved", "unchanged (SRT contains drawing events)"),
    ],
)
def test_strip_details_do_not_claim_changes_for_untouched_files(
    capsys, quiet, status, expected
):
    from jimaku_cli.strip_ih import StripResult

    report = output.Reporter(quiet=quiet)
    report.stripped("Show.srt", StripResult(status))
    report.print_summary()
    if status == "updated" and not quiet:
        expected += "; 0 cues modified, 0 cues removed"
    assert capsys.readouterr().err.strip() == f"Show.srt strip_ih: {expected}"
    assert report.exit_code == 0


def test_default_reporter_shows_every_outcome(capsys):
    report = output.Reporter()
    report.record("skip", "existing.srt")
    report.record("missing", "Show.mkv")
    report.print_summary()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[skip]" in captured.err and "[missing]" in captured.err
    assert captured.err.splitlines()[-1] == (
        "summary: 0 downloaded, 1 skipped, 1 missing, 0 failed"
    )
    assert report.exit_code == 0


@pytest.mark.parametrize(
    "options, progress",
    [({}, True), ({"quiet": True}, False), ({"verbose": True}, False)],
)
@pytest.mark.parametrize(
    "tty, term, terminal",
    [(True, "xterm", True), (False, "xterm", False), (True, "dumb", False)],
)
def test_native_progress_only_in_default_terminal_mode(
    monkeypatch, options, progress, tty, term, terminal
):
    monkeypatch.setattr(output.sys.stderr, "isatty", lambda: tty)
    monkeypatch.setenv("TERM", term)
    assert output.Reporter(**options).interactive == (progress and terminal)
