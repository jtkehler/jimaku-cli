"""Download visibility, stderr-only reporting, and verbosity-independent outcomes."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jimaku_cli import cli, postprocess
from jimaku_cli.api import FileEntry, JimakuError
from jimaku_cli.download import app
from jimaku_cli.files import app as files_app
from jimaku_cli.search import app as search_app

runner = CliRunner()

SUBTITLE_BODY = "1\n00:00:01,000 --> 00:00:02,000\n(shinko) ohayou\n"


def video_name(episode: int) -> str:
    return f"[Group] Show - {episode:02d} [1080p].mkv"


def subtitle_name(episode: int) -> str:
    return f"[Group] Show - {episode:02d} [1080p].ja.srt"


def remote(episode: int) -> FileEntry:
    return FileEntry(
        url=f"https://example.invalid/{episode}",
        name=subtitle_name(episode),
        size=len(SUBTITLE_BODY),
        last_modified="2024-01-01T00:00:00Z",
    )


class StubClient:
    """Stands in for JimakuClient over a canned listing.

    `listings` maps an episode number to what `get_files` should do: a list of
    entries, or an exception to raise. `download_errors` maps a destination filename
    to the failure its transfer should raise.
    """

    api_key = "fixture-key"

    def __init__(
        self,
        listings: dict[int | None, list[FileEntry] | Exception] | None = None,
        download_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.listings = listings or {}
        self.download_errors = download_errors or {}
        self.downloaded: list[Path] = []

    def get_files(self, entry_id: int, episode: int | None = None) -> list[FileEntry]:
        result = self.listings.get(episode, [])
        if isinstance(result, Exception):
            raise result
        return result

    def download_file(self, url: str, dest: Path) -> None:
        error = self.download_errors.get(dest.name)
        if error is not None:
            raise error
        dest.write_text(SUBTITLE_BODY, encoding="utf-8")
        self.downloaded.append(dest)


@pytest.fixture
def library(tmp_path: Path):
    """Build a directory of empty video files for the given episode numbers."""

    def build(*episodes: int) -> Path:
        for episode in episodes:
            (tmp_path / video_name(episode)).write_bytes(b"")
        return tmp_path

    return build


def run(
    directory: Path,
    client: StubClient,
    *args: str,
    release: str = "re:.",
    strip_ih: bool = False,
    align: bool = False,
):
    """Invoke `download` against a stub, pinning every option the tests rely on.

    `--release re:.` matches anything, so the ambient config file cannot reach in and
    change which candidates a test sees.
    """
    return runner.invoke(
        app,
        [
            "--id",
            "1",
            str(directory),
            "--release",
            release,
            "--no-rename",
            "--no-overwrite",
            "--strip-ih" if strip_ih else "--no-strip-ih",
            "--align" if align else "--no-align",
            *args,
        ],
        obj=client,
        catch_exceptions=False,
    )


def test_a_written_subtitle_is_reported_by_default(library):
    directory = library(1)

    result = run(directory, StubClient({1: [remote(1)]}))

    assert "[download]" in result.stderr
    assert subtitle_name(1) in result.stderr


def test_progress_never_reaches_stdout(library):
    directory = library(1)

    result = run(directory, StubClient({1: [remote(1)]}))

    assert result.stdout == ""


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_a_skipped_subtitle_prints_nothing_when_quiet(library, flag):
    directory = library(1)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)]}), flag)

    assert result.stderr == ""
    assert result.stdout == ""


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_a_missing_episode_prints_nothing_when_quiet(library, flag):
    directory = library(1)

    result = run(directory, StubClient({1: []}), flag)

    assert result.stderr == ""
    assert result.stdout == ""


def test_a_transfer_failure_is_reported_by_default(library):
    directory = library(1)
    client = StubClient(
        {1: [remote(1)]},
        download_errors={subtitle_name(1): JimakuError(503, "Service Unavailable")},
    )

    result = run(directory, client)

    assert "[failed]" in result.stderr
    assert "503" in result.stderr


def test_default_reports_a_skipped_subtitle(library):
    directory = library(1)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)]}))

    assert "[skip]" in result.stderr
    assert subtitle_name(1) in result.stderr


def test_default_reports_a_missing_episode(library):
    directory = library(1)

    result = run(directory, StubClient({1: []}))

    assert "[missing]" in result.stderr
    assert video_name(1) in result.stderr


def test_a_missing_episode_alone_exits_zero(library):
    """A season the provider has not uploaded yet is not a failure."""
    directory = library(1, 2)

    result = run(directory, StubClient({1: [], 2: []}))

    assert result.exit_code == 0


def test_a_fully_skipped_run_exits_zero(library):
    directory = library(1)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)]}))

    assert result.exit_code == 0


def test_a_transfer_failure_exits_nonzero(library):
    directory = library(1)
    client = StubClient(
        {1: [remote(1)]},
        download_errors={subtitle_name(1): JimakuError(503, "Service Unavailable")},
    )

    result = run(directory, client)

    assert result.exit_code != 0


def test_an_unreadable_episode_number_exits_nonzero(library, tmp_path):
    (tmp_path / "Show.S01E-broken.1080p.WEB-DL.mkv").write_bytes(b"")

    result = run(tmp_path, StubClient())

    assert result.exit_code != 0


def test_a_strip_failure_is_tagged_failed(library, monkeypatch):
    directory = library(1)
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))

    result = run(directory, StubClient({1: [remote(1)]}), strip_ih=True)

    assert "[failed]" in result.stderr
    assert "bad encoding" in result.stderr


def test_a_strip_failure_exits_nonzero(library, monkeypatch):
    directory = library(1)
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))

    result = run(directory, StubClient({1: [remote(1)]}), strip_ih=True)

    assert result.exit_code != 0


def test_a_strip_failure_leaves_the_subtitle_in_place(library, monkeypatch):
    """A postprocessing failure must not discard a subtitle that already landed."""
    directory = library(1)
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))

    run(directory, StubClient({1: [remote(1)]}), strip_ih=True)

    assert (directory / subtitle_name(1)).read_text(encoding="utf-8") == SUBTITLE_BODY


def test_an_align_failure_is_tagged_failed(library, monkeypatch):
    directory = library(1)
    monkeypatch.setattr(
        postprocess, "sync_subtitle", _raising(RuntimeError("ffmpeg missing"))
    )

    result = run(directory, StubClient({1: [remote(1)]}), align=True)

    assert "[failed]" in result.stderr
    assert "ffmpeg missing" in result.stderr


def test_a_failed_strip_still_gets_aligned(library, monkeypatch):
    directory = library(1)
    aligned: list[Path] = []
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))

    def align(subtitle, video, *, show_progress):
        aligned.append(subtitle)
        return postprocess.AlignmentResult()

    monkeypatch.setattr(postprocess, "sync_subtitle", align)

    run(directory, StubClient({1: [remote(1)]}), strip_ih=True, align=True)

    assert aligned == [directory / subtitle_name(1)]


def _raising(error: Exception):
    """A postprocessing step that always fails with `error`."""

    def step(*args, **kwargs):
        raise error

    return step


def test_a_run_that_wrote_something_ends_with_a_summary(library):
    directory = library(1)

    result = run(directory, StubClient({1: [remote(1)]}))

    assert "summary: 1 downloaded, 0 skipped, 0 missing, 0 failed" in result.stderr


def test_the_summary_counts_every_outcome_by_default(library):
    directory = library(1, 2)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)], 2: [remote(2)]}))

    assert "summary: 1 downloaded, 1 skipped, 0 missing, 0 failed" in result.stderr


def test_a_missing_api_key_is_reported_on_stderr(monkeypatch):
    monkeypatch.setattr(cli, "api_key", None)

    result = runner.invoke(cli.app, ["download", "--id", "1"])

    assert result.exit_code != 0
    assert "JIMAKU_API_KEY" in result.stderr
    assert result.stdout == ""


def test_an_api_failure_in_files_is_reported_on_stderr():
    result = runner.invoke(files_app, ["1"], obj=_ExplodingClient())

    assert result.exit_code != 0
    assert "upstream exploded" in result.stderr


def test_an_api_failure_in_search_is_reported_on_stderr(tmp_path):
    (tmp_path / "Frieren - 01.mkv").touch()
    result = runner.invoke(search_app, [str(tmp_path)], obj=_ExplodingClient())

    assert result.exit_code != 0
    assert "upstream exploded" in result.stderr


class _ExplodingClient:
    """Every call fails, so the command's own error path is what gets exercised."""

    def get_files(self, entry_id: int, episode: int | None = None):
        raise JimakuError(500, "upstream exploded")

    def search_entries(self, query: str, *, anime: bool = True):
        raise JimakuError(500, "upstream exploded")


def test_a_directory_that_is_not_one_exits_nonzero_on_stderr(tmp_path):
    """Bad invocation is not a per-file outcome: it ends the run."""
    result = run(tmp_path / "absent", StubClient())

    assert result.exit_code != 0
    assert "is not a directory" in result.stderr
    assert result.stdout == ""


def test_a_directory_with_no_videos_exits_nonzero_on_stderr(tmp_path):
    result = run(tmp_path, StubClient())

    assert result.exit_code != 0
    assert "no video files" in result.stderr
    assert result.stdout == ""


def test_one_bad_file_does_not_abort_the_batch(library):
    """The failure is reported and the next video is still attempted."""
    directory = library(1, 2)
    client = StubClient(
        {1: JimakuError(500, "listing exploded"), 2: [remote(2)]},
    )

    result = run(directory, client)

    assert "listing exploded" in result.stderr
    assert (directory / subtitle_name(2)).exists()
    assert result.exit_code != 0


@pytest.mark.parametrize("flags", [("-v",), ("--verbose",)])
def test_verbose_enables_diagnostics_and_restores_logging(library, flags):
    class LoggingClient(StubClient):
        def get_files(self, entry_id, episode=None):
            logging.getLogger("jimaku_cli.api").debug("listing fixture")
            return super().get_files(entry_id, episode)

    root = logging.getLogger()
    before = (root.level, root.handlers[:])
    directory = library(1)
    for options in (flags, (), ("-q",)):
        result = run(directory, LoggingClient(), *options)
        assert result.exit_code == 0
        assert result.stdout == ""
        assert result.stderr.count("listing fixture") == (1 if options == flags else 0)
        assert (root.level, root.handlers) == before
        if options == ("-q",):
            assert result.stderr == ""


@pytest.mark.parametrize("flags", [("-q",), (), ("-v",)])
def test_caught_failures_show_tracebacks_only_at_diagnostic_tier(library, flags):
    result = run(library(1), StubClient({1: JimakuError(503, "offline")}), *flags)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "[failed]" in result.stderr
    assert ("Traceback" in result.stderr) == (flags == ("-v",))


@pytest.mark.parametrize("flags", [("-q",), (), ("-v",)])
def test_strip_reports_actual_changes_without_adding_outcomes(library, flags):
    class JapaneseClient(StubClient):
        def download_file(self, url, dest):
            dest.write_text("1\n00:00:01,000 --> 00:00:02,000\n（信子）おはよう\n")

    directory = library(1)
    result = run(directory, JapaneseClient({1: [remote(1)]}), *flags, strip_ih=True)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert "strip_ih: updated" in result.stderr
    assert ("1 cues modified, 0 cues removed" in result.stderr) == (flags != ("-q",))
    assert "1 downloaded" in result.stderr and "0 failed" in result.stderr
    assert result.stderr.count("[download]") == 1
    assert "（信子）" not in (directory / subtitle_name(1)).read_text()


@pytest.fixture
def alignment_backend(monkeypatch):
    def install(*, succeeds=True):
        class Parser:
            def parse_args(self, arguments):
                return Path(arguments[arguments.index("-o") + 1])

        def backend(destination):
            logging.getLogger("ffsubsync").info("raw alignment detail")
            destination.write_text("synced")
            return {
                "sync_was_successful": succeeds,
                "offset_seconds": -0.25,
                "framerate_scale_factor": 1.001,
            }

        monkeypatch.setattr(
            postprocess,
            "_load_ffsubsync",
            lambda: (SimpleNamespace(run=backend), Parser),
        )
        monkeypatch.setattr(postprocess, "_silence_native_progress", nullcontext)

    return install


@pytest.mark.parametrize("flags", [("-q",), (), ("-v",)])
def test_alignment_reports_progress_and_completion(library, alignment_backend, flags):
    alignment_backend()
    directory = library(1)
    result = run(directory, StubClient({1: [remote(1)]}), *flags, align=True)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert "ffsubsync: aligning" in result.stderr
    assert "%" not in result.stderr
    assert "ffsubsync: complete" in result.stderr
    assert ("offset -0.250s" in result.stderr) == (flags != ("-q",))
    assert ("scale 1.001000" in result.stderr) == (flags != ("-q",))
    assert ("raw alignment detail" in result.stderr) == (flags == ("-v",))
    assert "1 downloaded" in result.stderr and "0 failed" in result.stderr
    assert (directory / subtitle_name(1)).read_text() == "synced"


@pytest.mark.parametrize("failure", ["alignment", "install"])
def test_failed_alignment_never_reports_completion(
    library, monkeypatch, alignment_backend, failure
):
    alignment_backend(succeeds=failure != "alignment")
    if failure == "install":
        monkeypatch.setattr(
            postprocess.os, "replace", _raising(OSError("install failed"))
        )
    directory = library(1)
    result = run(directory, StubClient({1: [remote(1)]}), align=True)
    assert "ffsubsync: aligning" in result.stderr
    assert "ffsubsync: complete" not in result.stderr
    assert "[failed]" in result.stderr
    assert result.exit_code == 1
    assert result.stdout == ""
    assert (directory / subtitle_name(1)).read_text() == SUBTITLE_BODY
    assert not list(directory.glob(".ffsubsync-*"))


@pytest.mark.parametrize("flags", [("-q",), (), ("-v",)])
def test_two_processing_failures_count_as_two_operations(library, monkeypatch, flags):
    directory = library(1)
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("strip failed")))
    monkeypatch.setattr(
        postprocess, "sync_subtitle", _raising(RuntimeError("align failed"))
    )
    result = run(
        directory, StubClient({1: [remote(1)]}), *flags, strip_ih=True, align=True
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.count("[failed]") == 2
    assert "1 downloaded" in result.stderr and "2 failed" in result.stderr
    assert (directory / subtitle_name(1)).read_text() == SUBTITLE_BODY


def test_command_redacts_configured_key_from_chained_tracebacks(library):
    class KeyClient(StubClient):
        api_key = "fixture-key"

        def get_files(self, entry_id, episode=None):
            raise JimakuError(503, "offline") from ValueError(self.api_key)

    result = run(library(1), KeyClient(), "-v")
    assert result.exit_code == 1
    assert "fixture-key" not in result.stderr
    assert "ValueError: [redacted]" in result.stderr
    assert "JimakuError: HTTP 503: offline" in result.stderr


def test_verbosity_help_does_not_suggest_a_numeric_argument():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for option in ("--quiet", "--verbose"):
        option_line = next(
            line for line in result.stdout.splitlines() if option in line
        )
        assert "<int>" not in option_line


@pytest.mark.parametrize(
    "flags, all_outcomes, diagnostics",
    [
        (("-q",), False, False),
        (("--quiet",), False, False),
        ((), True, False),
        (("-v",), True, True),
        (("--verbose",), True, True),
    ],
)
def test_public_output_modes(library, flags, all_outcomes, diagnostics):
    class LoggingClient(StubClient):
        def get_files(self, entry_id, episode=None):
            logging.getLogger("jimaku_cli.api").debug("listing fixture")
            return super().get_files(entry_id, episode)

    directory = library(1, 2, 3)
    existing = directory / subtitle_name(2)
    existing.write_text("existing subtitle", encoding="utf-8")
    client = LoggingClient({1: [remote(1)], 2: [remote(2)], 3: []})

    result = run(directory, client, *flags)

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "[download]" in result.stderr
    assert ("[skip]" in result.stderr) == all_outcomes
    assert ("[missing]" in result.stderr) == all_outcomes
    assert ("listing fixture" in result.stderr) == diagnostics
    summary = (
        "summary: 1 downloaded, 1 skipped, 1 missing, 0 failed"
        if all_outcomes
        else "summary: 1 downloaded, 0 failed"
    )
    assert result.stderr.splitlines()[-1] == summary
    assert client.downloaded == [directory / subtitle_name(1)]
    assert client.downloaded[0].read_text(encoding="utf-8") == SUBTITLE_BODY
    assert existing.read_text(encoding="utf-8") == "existing subtitle"


@pytest.mark.parametrize("flags", [("-q",), ("--quiet",)])
def test_quiet_reports_failures_without_downloads(library, flags):
    result = run(library(1), StubClient({1: JimakuError(503, "offline")}), *flags)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "[failed]" in result.stderr
    assert "offline" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stderr.splitlines()[-1] == "summary: 0 downloaded, 1 failed"


@pytest.mark.parametrize(
    "flags",
    [
        ("-q", "-v"),
        ("-v", "-q"),
        ("-qv",),
        ("--quiet", "--verbose"),
        ("--verbose", "--quiet"),
    ],
)
def test_conflicting_output_flags_do_no_work(library, monkeypatch, flags):
    class NoWorkClient(StubClient):
        def get_files(self, entry_id, episode=None):
            raise AssertionError("invalid flags must be rejected before work")

    monkeypatch.setattr(cli, "api_key", "fixture-key")
    monkeypatch.setattr(cli, "JimakuClient", lambda **kwargs: NoWorkClient())
    result = runner.invoke(
        cli.app,
        ["download", "--id", "1", str(library(1)), *flags],
        catch_exceptions=False,
    )
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "cannot be combined" in result.stderr
