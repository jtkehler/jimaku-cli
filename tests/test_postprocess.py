from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from jimaku_cli import postprocess
from jimaku_cli.postprocess import AlignError, sync_subtitle, temporary_path


def test_temporary_path_reserves_unique_siblings(tmp_path: Path) -> None:
    subtitle = tmp_path / ("あ" * 82 + ".SRT")
    first = temporary_path(subtitle, ".ffsubsync")
    second = temporary_path(subtitle, ".ffsubsync")
    try:
        assert first != second
        assert first.parent == second.parent == subtitle.parent
        assert first.suffix == second.suffix == subtitle.suffix.casefold()
        assert first.exists() and second.exists()
        assert all(len(os.fsencode(path.name)) <= 255 for path in (first, second))
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)


class StubParser:
    def parse_args(self, arguments: list[str]) -> Path:
        return Path(arguments[arguments.index("-o") + 1])


def test_sync_subtitle_replaces_from_a_written_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subtitle = tmp_path / "dialogue.SRT"
    subtitle.write_text("original")
    subtitle.chmod(0o640)
    video = tmp_path / "video.mkv"
    video.touch()

    def run(output: Path) -> dict[str, bool]:
        assert output.suffix == ".srt"
        output.write_text("synced")
        return {"sync_was_successful": True}

    monkeypatch.setattr(
        "jimaku_cli.postprocess._load_ffsubsync",
        lambda: (SimpleNamespace(run=run), StubParser),
    )

    sync_subtitle(subtitle, video)

    assert subtitle.read_text() == "synced"
    assert subtitle.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".ffsubsync-*"))


def test_sync_subtitle_does_not_replace_with_an_empty_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subtitle = tmp_path / "dialogue.srt"
    subtitle.write_text("original")
    video = tmp_path / "video.mkv"
    video.touch()
    monkeypatch.setattr(
        "jimaku_cli.postprocess._load_ffsubsync",
        lambda: (
            SimpleNamespace(run=lambda _: {"sync_was_successful": True}),
            StubParser,
        ),
    )

    with pytest.raises(AlignError):
        sync_subtitle(subtitle, video)

    assert subtitle.read_text() == "original"
    assert not list(tmp_path.glob(".ffsubsync-*"))


def test_sync_subtitle_cleans_up_when_argument_parsing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subtitle = tmp_path / "dialogue.srt"
    subtitle.write_text("original")
    video = tmp_path / "video.mkv"
    video.touch()

    class BrokenParser:
        def parse_args(self, _arguments: list[str]) -> None:
            raise RuntimeError("bad arguments")

    monkeypatch.setattr(
        "jimaku_cli.postprocess._load_ffsubsync",
        lambda: (None, BrokenParser),
    )

    with pytest.raises(RuntimeError, match="bad arguments"):
        sync_subtitle(subtitle, video)

    assert subtitle.read_text() == "original"
    assert not list(tmp_path.glob(".ffsubsync-*"))


@pytest.mark.parametrize(
    ("ffsubsync_args", "expected"),
    [
        ("", []),
        (" \t ", []),
        (
            (
                "--reference-stream 0:s:2 --reference-stream 0:s:1 --no-fix-framerate "
                "--apply-offset-seconds -1.5 --ffmpeg-path '/opt/my ffmpeg/bin'"
            ),
            [
                "--reference-stream", "0:s:2", "--reference-stream", "0:s:1",
                "--no-fix-framerate", "--apply-offset-seconds", "-1.5",
                "--ffmpeg-path", "/opt/my ffmpeg/bin",
            ],
        ),
    ],
)
def test_sync_subtitle_appends_split_ffsubsync_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ffsubsync_args: str,
    expected: list[str],
) -> None:
    subtitle = tmp_path / "dialogue.srt"
    subtitle.write_text("original")
    video = tmp_path / "video.mkv"
    video.touch()
    parsed: list[list[str]] = []

    class RecordingParser:
        def parse_args(self, arguments: list[str]) -> Path:
            parsed.append(arguments)
            return Path(arguments[arguments.index("-o") + 1])

    def run(output: Path) -> dict[str, bool]:
        output.write_text("synced")
        return {"sync_was_successful": True}

    monkeypatch.setattr(
        "jimaku_cli.postprocess._load_ffsubsync",
        lambda: (SimpleNamespace(run=run), RecordingParser),
    )

    sync_subtitle(subtitle, video, ffsubsync_args=ffsubsync_args)

    [arguments] = parsed
    assert arguments[:4] == [str(video), "-i", str(subtitle), "-o"]
    assert arguments[5:] == expected
    assert subtitle.read_text() == "synced"


@pytest.mark.parametrize(
    "offset, scale, expected",
    [
        (0.25, 1.001, (0.25, 1.001)),
        (float("nan"), float("inf"), (None, None)),
        (None, "unknown", (None, None)),
    ],
)
def test_alignment_returns_available_metadata(
    tmp_path,
    monkeypatch,
    offset,
    scale,
    expected,
):
    subtitle = tmp_path / "Show.srt"
    subtitle.write_text("original")

    def backend(output):
        output.write_text("synced")
        return {
            "sync_was_successful": True,
            "offset_seconds": offset,
            "framerate_scale_factor": scale,
        }

    monkeypatch.setattr(
        postprocess,
        "_load_ffsubsync",
        lambda: (SimpleNamespace(run=backend), StubParser),
    )
    monkeypatch.setattr(postprocess, "_silence_native_progress", nullcontext)
    result = sync_subtitle(subtitle, tmp_path / "Show.mkv", show_progress=False)
    assert (result.offset_seconds, result.framerate_scale_factor) == expected
    assert subtitle.read_text() == "synced"
    assert not list(tmp_path.glob(".ffsubsync-*"))


def test_native_progress_suppression_is_local_and_restored_on_interrupt(capsys):
    postprocess._load_ffsubsync()
    import ffsubsync.speech_transformers as speech
    import tqdm

    original_binding = speech.tqdm
    original_factory = tqdm.tqdm
    with (
        pytest.raises(KeyboardInterrupt),
        postprocess._silence_native_progress(),
        speech.tqdm.tqdm(total=4, disable=False) as bar,
    ):
        bar.update(4)
        assert bar.disable
        assert tqdm.tqdm is original_factory
        raise KeyboardInterrupt
    assert speech.tqdm is original_binding
    assert tqdm.tqdm is original_factory
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
