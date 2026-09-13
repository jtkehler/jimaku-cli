"""Real bundled-fzf PTY tests; API responses are fixtures, never live requests.

Run with: uv run pytest -q tests/test_search_fzf.py
The child gets an isolated HOME/config and an explicit fixture API key. Its
stdout is a separate pipe, not part of the terminal carrying input and stderr.
"""

import errno
import json
import os
import re
import selectors
import shlex
import signal
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING, Self

import pytest

if TYPE_CHECKING or os.name == "posix":
    import fcntl
    import pty
    import termios

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires a Unix PTY")

TIMEOUT = 8.0
ANSI_CSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
FZF_STARTED = b"[real bundled fzf]"


def child_main(mode: str, directory: str) -> None:
    """Run the real chooser/CLI, replacing only the API and download boundary."""
    if sys.stdin.isatty():
        # Popen starts a new session; this makes fd 0 its controlling terminal.
        _ = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCSCTTY, 0)

    import iterfzf  # pyright: ignore[reportMissingTypeStubs]
    import typer

    bundled = iterfzf.BUNDLED_EXECUTABLE
    assert bundled is not None and bundled.is_file()

    def audit(event: str, args: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo"}:
            raise AssertionError("network access is forbidden in fixture API tests")
        if event == "subprocess.Popen":
            executable = args[0]
            assert isinstance(executable, str)
            assert Path(executable).resolve() == bundled.resolve(), executable
            print(FZF_STARTED.decode(), file=sys.stderr, flush=True)

    sys.addaudithook(audit)

    from jimaku_cli import cli, search
    from jimaku_cli.api import Entry, EntryFlags, FileEntry

    def forbidden_download(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fixture API search must never start a download")

    class FixtureAPI:
        """In-memory metadata only; no JimakuClient or HTTP session is created."""

        def __init__(self, *, api_key: str) -> None:
            assert api_key == "fixture-api-key"
            self.api_key: str = api_key

        def search_entries(self, query: str, *, anime: bool) -> list[Entry]:
            print(f"[fixture API] search_entries {query!r} anime={anime}", file=sys.stderr)
            if mode == "manual":
                return []
            return [
                Entry(42, "Fixture API: 別の作品", "2026-01-01T00:00:00Z", EntryFlags(anime=True)),
                Entry(99, "Fixture API: 葬送のフリーレン", "2026-01-01T00:00:00Z", EntryFlags(anime=True)),
            ]

        def get_files(self, entry_id: int, episode: int | None = None) -> list[FileEntry]:
            assert episode is not None
            print(f"[fixture API] get_files {entry_id} {episode}", file=sys.stderr)
            groups = ("Alpha", "Beta", "Gamma") if episode == 1 else ("Delta",)
            return [
                FileEntry(
                    "https://fixture.invalid/not-a-download",
                    f"[{group}] Show - {episode:02}.srt",
                    1,
                    "2026-01-01T00:00:00Z",
                )
                for group in groups
            ]

        def download_file(self, *_args: object, **_kwargs: object) -> None:
            forbidden_download()

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(cli, "JimakuClient", FixtureAPI)
        patcher.setattr(search, "download", forbidden_download)
        if mode.startswith("choose"):
            labels = ["Alpha 別の作品", "Beta 葬送のフリーレン", "Gamma 日本語"]
            if mode == "choose-collision":
                labels = ["Show\n", r"Show\n"]
            elif mode == "choose-bulk":
                labels = [f"Release {index:03} " + "日本語" * 60 for index in range(350)]
            try:
                result = search.choose(
                    "Fixture choice",
                    labels,
                    multi=mode in {"choose-multi", "choose-bulk"},
                )
            except typer.Abort:
                print("Aborted!", file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps(result))
        else:
            cli.app(
                args=["search", directory, "--no-download"],
                prog_name="jimaku",
            )


@dataclass(frozen=True)
class ChildCommand:
    environment: dict[str, str]
    videos: Path

    def command(self, mode: str) -> list[str]:
        return [sys.executable, str(Path(__file__).resolve()), mode, str(self.videos)]


@pytest.fixture
def child_command(tmp_path: Path) -> ChildCommand:
    """Do not inherit user credentials, config, fzf options, or shell hooks."""
    home = tmp_path / "home"
    home.mkdir()
    videos = tmp_path / "fixture API's 日本語 videos"
    videos.mkdir()
    (videos / "Show - 01.mkv").touch()
    environment = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PATH": os.defpath,
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "JIMAKU_API_KEY": "fixture-api-key",
        "FZF_DEFAULT_OPTS": "",
        "FZF_DEFAULT_COMMAND": "",
    }

    return ChildCommand(environment, videos)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    """Clean up the entire session, including fzf if its Python parent failed."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        _ = process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        _ = process.wait(timeout=2)


class TerminalSession:
    """Small PTY driver: finite waits, CPR replies, separate payload capture."""

    def __init__(self, command: list[str], environment: dict[str, str], cwd: Path) -> None:
        try:
            master, slave = pty.openpty()
        except OSError as exc:
            if exc.errno not in {errno.ENOENT, errno.ENODEV, errno.ENOSYS, errno.EPERM}:
                raise
            pytest.skip(f"PTY unavailable: {exc}")
        self.master: int = master
        _ = fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 140, 0, 0))
        try:
            self.process: subprocess.Popen[bytes] = subprocess.Popen(
                command,
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=slave,
                env=environment,
                cwd=cwd,
                start_new_session=True,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        assert self.process.stdout is not None
        self.selector: selectors.BaseSelector = selectors.DefaultSelector()
        self.stdout_pipe: IO[bytes] = self.process.stdout
        _ = self.selector.register(self.master, selectors.EVENT_READ)
        _ = self.selector.register(self.stdout_pipe, selectors.EVENT_READ)
        self.terminal: bytearray = bytearray()
        self.stdout: bytearray = bytearray()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self.process.poll() is None:
            # Let iterfzf reap its child on assertion failures before resorting
            # to killing the group; otherwise fzf could become an orphan zombie.
            try:
                _ = self.send(b"\x03")
                deadline = time.monotonic() + 1
                while self.process.poll() is None and time.monotonic() < deadline:
                    self.pump()
            except OSError:
                pass
        stop_process(self.process)
        self.selector.close()
        self.stdout_pipe.close()
        os.close(self.master)

    def pump(self, timeout: float = 0.05) -> None:
        for key, _ in self.selector.select(timeout):
            try:
                data = os.read(key.fd, 65536)
            except OSError as exc:
                if exc.errno != errno.EIO or key.fd != self.master:
                    raise
                data = b""  # Linux reports a closed PTY slave as EIO.
            if not data:
                _ = self.selector.unregister(key.fileobj)
                continue
            if key.fd != self.master:
                self.stdout.extend(data)
            else:
                requests_before = self.terminal.count(b"\x1b[6n")
                self.terminal.extend(data)
                requests = self.terminal.count(b"\x1b[6n") - requests_before
                if requests:
                    # fzf --height asks for the cursor position. A bare PTY is
                    # not a terminal emulator, so answer CPR, including split reads.
                    _ = os.write(self.master, b"\x1b[1;1R" * requests)

    def diagnostic(self) -> str:
        return (
            f"exit={self.process.poll()}, stdout={bytes(self.stdout)!r}\n"
            f"terminal={ANSI_CSI.sub(b'', bytes(self.terminal))[-6000:]!r}"
        )

    def wait_for(self, text: str, *, after: int = 0) -> None:
        deadline = time.monotonic() + TIMEOUT
        while text.encode() not in ANSI_CSI.sub(b"", bytes(self.terminal[after:])):
            assert time.monotonic() < deadline, f"waiting for {text!r}\n{self.diagnostic()}"
            assert self.process.poll() is None, self.diagnostic()
            self.pump()

    def send(self, keys: str | bytes) -> int:
        offset = len(self.terminal)
        _ = os.write(self.master, keys.encode() if isinstance(keys, str) else keys)
        return offset

    def query(self, text: str, total: int) -> None:
        # A pending frame for the previous query can also say 1/N. First force
        # an unmatched query, then wait for the new query's 0/N -> 1/N transition.
        offset = self.send(b"\x15__fixture_no_match__")
        self.wait_for(f"0/{total}", after=offset)
        offset = self.send(b"\x15" + text.encode())
        self.wait_for(f"1/{total}", after=offset)

    def finish(self) -> int:
        deadline = time.monotonic() + TIMEOUT
        while self.process.poll() is None or self.selector.get_map():
            assert time.monotonic() < deadline, self.diagnostic()
            self.pump()
        return self.process.wait(timeout=1)


def start_session(child: ChildCommand, mode: str) -> TerminalSession:
    return TerminalSession(child.command(mode), child.environment, child.videos.parent)


def test_choose_filters_japanese_and_returns_original_single_index(
    child_command: ChildCommand,
) -> None:
    with start_session(child_command, "choose") as session:
        session.wait_for("3/3")
        session.query("フリーレン", 3)
        assert session.stdout == b"", session.diagnostic()
        _ = session.send(b"\r")
        assert session.finish() == 0, session.diagnostic()
        assert session.stdout == b"[1]\n", session.diagnostic()
        assert session.terminal.count(FZF_STARTED) == 1
        assert b"Fixture choice:" in session.terminal
        assert "葬送のフリーレン".encode() in session.terminal


def test_choose_multi_returns_mark_order_not_input_order(
    child_command: ChildCommand,
) -> None:
    with start_session(child_command, "choose-multi") as session:
        session.wait_for("3/3")
        for count, label in enumerate(("Gamma", "Alpha", "Beta"), 1):
            session.query(label, 3)
            offset = session.send(b"\t")
            session.wait_for(f"({count})", after=offset)
            assert session.stdout == b"", session.diagnostic()
        _ = session.send(b"\r")
        assert session.finish() == 0, session.diagnostic()
        assert session.stdout == b"[2, 0, 1]\n", session.diagnostic()
        assert session.terminal.count(FZF_STARTED) == 1


def test_identical_escaped_labels_keep_distinct_indices(child_command: ChildCommand) -> None:
    with start_session(child_command, "choose-collision") as session:
        session.wait_for("2/2")
        # Both labels display as Show\\n; move to the second without searching.
        _ = session.send(b"\x1b[B\r")
        assert session.finish() == 0, session.diagnostic()
        assert session.stdout == b"[1]\n", session.diagnostic()


def test_large_multi_selection_does_not_fill_the_result_pipe(
    child_command: ChildCommand,
) -> None:
    with start_session(child_command, "choose-bulk") as session:
        session.wait_for("350/350")
        offset = session.send(b"\t" * 350)
        session.wait_for("(350)", after=offset)
        assert session.stdout == b"", session.diagnostic()
        _ = session.send(b"\r")
        assert session.finish() == 0, session.diagnostic()
        assert session.stdout.decode() == json.dumps(list(range(350))) + "\n"


def test_choose_multi_enter_without_marks_selects_current_row(
    child_command: ChildCommand,
) -> None:
    with start_session(child_command, "choose-multi") as session:
        session.wait_for("3/3")
        session.query("フリーレン", 3)
        _ = session.send(b"\r")
        assert session.finish() == 0, session.diagnostic()
        assert session.stdout == b"[1]\n", session.diagnostic()
        assert session.terminal.count(FZF_STARTED) == 1


@pytest.mark.parametrize("mode", ["choose", "choose-multi"])
@pytest.mark.parametrize("key", [b"\x1b", b"\x03", b"\x04"], ids=["esc", "ctrl-c", "eof"])
def test_choose_cancellation_emits_no_selection(
    child_command: ChildCommand, mode: str, key: bytes,
) -> None:
    with start_session(child_command, mode) as session:
        session.wait_for("3/3")
        # In fzf's raw terminal, Ctrl-D on an empty query is delete-char/eof.
        _ = session.send(key)
        assert session.finish() == 1, session.diagnostic()
        assert session.stdout == b"", session.diagnostic()
        assert b"Aborted" in session.terminal
        assert b"Traceback" not in session.terminal
        assert session.terminal.count(FZF_STARTED) == 1


def select_fixture_entry(session: TerminalSession) -> None:
    session.wait_for("2/2")
    session.query("フリーレン", 2)
    assert session.stdout == b"", session.diagnostic()
    _ = session.send(b"\r")
    session.wait_for("3/3")
    assert session.stdout == b"", session.diagnostic()


def test_search_fixture_api_emits_only_complete_command_in_mark_order(
    child_command: ChildCommand,
) -> None:
    with start_session(child_command, "search") as session:
        select_fixture_entry(session)
        # Mix Shift-Tab and Tab; order must be Gamma, Alpha, not listing order.
        for count, (label, key) in enumerate((("Gamma", b"\x1b[Z"), ("Alpha", b"\t")), 1):
            session.query(label, 3)
            offset = session.send(key)
            session.wait_for(f"({count})", after=offset)
            assert session.stdout == b"", session.diagnostic()
        _ = session.send(b"\r")
        assert session.finish() == 0, session.diagnostic()
        expected = [
            "jimaku", "download", str(child_command.videos.resolve()), "--id", "99",
            "--release", "Gamma", "--release", "Alpha", "--prefer-format", "srt",
            "--no-all", "--no-rename", "--no-overwrite", "--no-align", "--no-strip-ih",
        ]
        assert session.stdout.decode() == shlex.join(expected) + "\n", session.diagnostic()
        assert shlex.split(session.stdout.decode()) == expected
        assert session.terminal.count(FZF_STARTED) == 2
        assert b"[fixture API] get_files 99 1" in session.terminal
        assert b"Entry:" in session.terminal
        assert b"Subtitle release:" in session.terminal
        assert list(child_command.videos.iterdir()) == [child_command.videos / "Show - 01.mkv"]


@pytest.mark.parametrize("stage", ["entry", "release", "later-release"])
@pytest.mark.parametrize("key", [b"\x1b", b"\x03", b"\x04"], ids=["esc", "ctrl-c", "eof"])
def test_search_fixture_api_cancellation_has_no_partial_command(
    child_command: ChildCommand, stage: str, key: bytes,
) -> None:
    if stage == "later-release":
        (child_command.videos / "Show - 02.mkv").touch()
    with start_session(child_command, "search") as session:
        if stage == "entry":
            session.wait_for("2/2")
        else:
            select_fixture_entry(session)
            session.query("Gamma", 3)
            offset = session.send(b"\t")
            session.wait_for("(1)", after=offset)
            if stage == "later-release":
                # A release has already been accepted for an earlier episode.
                _ = session.send(b"\r")
                session.wait_for("1/1")
                assert b"[fixture API] get_files 99 2" in session.terminal
            else:
                offset = session.send(b"\x15")
                session.wait_for("3/3", after=offset)
        assert session.stdout == b"", session.diagnostic()
        _ = session.send(key)
        assert session.finish() == 1, session.diagnostic()
        assert session.stdout == b"", session.diagnostic()
        assert b"Aborted" in session.terminal
        assert b"Traceback" not in session.terminal
        expected_choosers = {"entry": 1, "release": 2, "later-release": 3}
        assert session.terminal.count(FZF_STARTED) == expected_choosers[stage]
        assert all(path.suffix == ".mkv" for path in child_command.videos.iterdir())


@pytest.mark.parametrize("key", [b"\x03", b"\x04"], ids=["ctrl-c", "eof"])
def test_search_fixture_api_manual_title_cancel_keeps_input_echo_off_stdout(
    child_command: ChildCommand, key: bytes,
) -> None:
    with start_session(child_command, "manual") as session:
        session.wait_for("Search title:")
        offset = session.send("見つからない作品\r")
        session.wait_for("Search title:", after=offset)
        assert session.stdout == b"", session.diagnostic()
        # Unlike raw fzf input, Ctrl-D here is real canonical-terminal EOF.
        _ = session.send(key)
        assert session.finish() == 1, session.diagnostic()
        assert session.stdout == b"", session.diagnostic()
        assert "見つからない作品".encode() in session.terminal
        assert b"Aborted" in session.terminal
        assert FZF_STARTED not in session.terminal


def test_pty_cleanup_reaps_fzf_after_a_test_failure(child_command: ChildCommand) -> None:
    session = start_session(child_command, "choose")
    with pytest.raises(RuntimeError, match="intentional cleanup probe"), session:
        session.wait_for("3/3")
        raise RuntimeError("intentional cleanup probe")
    assert session.process.returncode == 1, session.diagnostic()
    # Even a defunct fzf would keep this process group present until reaped.
    with pytest.raises(ProcessLookupError):
        os.killpg(session.process.pid, 0)


def test_search_fixture_api_without_controlling_tty_fails_cleanly(
    child_command: ChildCommand,
) -> None:
    # setsid + /dev/null stdin and pipes means even /dev/tty is unavailable.
    with subprocess.Popen(
        child_command.command("search"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_command.environment,
        cwd=child_command.videos.parent,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=TIMEOUT)
        finally:
            stop_process(process)
        assert process.returncode == 1, stderr.decode(errors="replace")
        assert stdout == b""
        assert any(
            message in stderr.lower()
            for message in (b"tty", b"terminal", b"inappropriate ioctl")
        ), stderr
        assert b"Traceback" not in stderr
        assert list(child_command.videos.iterdir()) == [child_command.videos / "Show - 01.mkv"]


if __name__ == "__main__":
    child_main(sys.argv[1], sys.argv[2])
