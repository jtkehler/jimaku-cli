"""Format preferences rank subtitle candidates without excluding fallbacks."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jimaku_cli.api import FileEntry
from jimaku_cli.download import app, filter_release


def candidate(name: str, modified: str = "2026-01-01T00:00:00Z") -> FileEntry:
    return FileEntry(
        url="https://example.invalid/" + name,
        name=name,
        size=8,
        last_modified=modified,
    )


class DownloadClient:
    api_key = "fixture-key"

    def __init__(self, files: list[FileEntry] | dict[int | None, list[FileEntry]]):
        self.files = files
        self.listed: list[int | None] = []
        self.downloaded: list[Path] = []

    def get_files(self, entry_id, episode=None):
        self.listed.append(episode)
        if isinstance(self.files, dict):
            return self.files.get(episode, [])
        return self.files

    def download_file(self, url, destination):
        destination.write_bytes(b"subtitle")
        self.downloaded.append(destination)


@pytest.fixture
def download(tmp_path):
    (tmp_path / "[Group] Show - 01.mkv").touch()

    def invoke(files, *flags):
        client = DownloadClient(files)
        result = CliRunner().invoke(
            app,
            [
                "--id", "1", str(tmp_path),
                "--no-all", "--no-rename", "--no-overwrite",
                "--no-strip-ih", "--no-align", *flags,
            ],
            obj=client,
            catch_exceptions=False,
        )
        return result, client

    return invoke


@pytest.mark.parametrize("flags", [[], ["--release", "re:."]])
def test_default_prefers_srt_over_newer_ass(download, tmp_path, flags):
    ass = candidate("[Group] Show - 01.ass", "2026-02-01T00:00:00Z")
    srt = candidate("[Group] Show - 01.SRT")

    result, client = download([ass, srt], *flags)

    assert result.exit_code == 0, result.stderr
    assert result.stdout == ""
    expected = tmp_path / "[Group] Show - 01.srt"
    assert client.downloaded == [expected]
    assert expected.read_bytes() == b"subtitle"
    assert not (tmp_path / ass.name).exists()


@pytest.mark.parametrize("preferred", ["ass", "ASS", "ssa", "vtt", "sub"])
def test_prefer_format_overrides_srt_default(download, tmp_path, preferred):
    srt = candidate("[Group] Show - 01.srt", "2026-02-01T00:00:00Z")
    other = candidate("[Group] Show - 01." + preferred)

    result, client = download([srt, other], "--prefer-format", preferred)

    assert result.exit_code == 0, result.stderr
    expected = tmp_path / ("[Group] Show - 01." + preferred.lower())
    assert client.downloaded == [expected]
    assert expected.read_bytes() == b"subtitle"
    assert not (tmp_path / srt.name).exists()


@pytest.mark.parametrize("suffix", ["ass", "ssa", "vtt", "sub"])
def test_missing_preferred_format_falls_back(download, tmp_path, suffix):
    archive = candidate("[Group] Show - 01.srt.zip")
    fallback = candidate(f"[Group] Show - 01.{suffix}")

    result, client = download([archive, fallback])

    assert result.exit_code == 0, result.stderr
    assert client.downloaded == [tmp_path / fallback.name]
    assert not (tmp_path / archive.name).exists()


@pytest.mark.parametrize("patterns", [["First", "Second"], [r"re:\[First\]", "re:."]])
def test_release_priority_outranks_format_preference(download, tmp_path, patterns):
    first = candidate("[First] Show - 01.ass")
    second = candidate("[Second] Show - 01.srt", "2026-02-01T00:00:00Z")
    flags = [argument for pattern in patterns for argument in ("--release", pattern)]

    result, client = download([second, first], *flags)

    assert result.exit_code == 0, result.stderr
    assert client.downloaded == [tmp_path / first.name]


def test_preferred_format_cannot_bypass_release_matching(download, tmp_path):
    srt = candidate("[Other] Show - 01.srt")
    ass = candidate("[First] Show - 01.ass")

    result, client = download([srt, ass], "--release", "First")

    assert result.exit_code == 0, result.stderr
    assert client.downloaded == [tmp_path / ass.name]


def test_all_keeps_every_supported_format(download, tmp_path):
    ass = candidate("[Group] Show - 01.ass", "2026-02-01T00:00:00Z")
    srt = candidate("[Group] Show - 01.srt")
    archive = candidate("[Group] Show - 01.zip")

    result, client = download([archive, ass, srt], "--all")

    assert result.exit_code == 0, result.stderr
    assert client.downloaded == [tmp_path / srt.name, tmp_path / ass.name]
    assert all(path.read_bytes() == b"subtitle" for path in client.downloaded)


@pytest.mark.parametrize("patterns", [[], ["re:."]])
def test_equal_format_rank_uses_recency_then_filename(patterns):
    old_srt = candidate("[Group] Show - 01.srt")
    new_srt_b = candidate("[Group] Show - 01 B.srt", "2026-02-01T00:00:00Z")
    new_srt_a = candidate("[Group] Show - 01 A.srt", "2026-02-01T00:00:00Z")
    old_ass = candidate("[Group] Show - 01.ass")
    new_vtt = candidate("[Group] Show - 01.vtt", "2026-02-01T00:00:00Z")
    files = [old_ass, old_srt, new_srt_b, new_vtt, new_srt_a]
    original = files.copy()

    assert filter_release(files, patterns) == [
        new_srt_a, new_srt_b, old_srt, new_vtt, old_ass,
    ]
    assert files == original


def test_preference_is_applied_independently_per_episode(download, tmp_path):
    (tmp_path / "[Group] Show - 02.mkv").touch()
    first_ass = candidate("[Group] Show - 01.ass")
    first_srt = candidate("[Group] Show - 01.srt")
    second_ass = candidate("[Group] Show - 02.ass")

    result, client = download({1: [first_ass, first_srt], 2: [second_ass]})

    assert result.exit_code == 0, result.stderr
    assert client.listed == [1, 2]
    assert client.downloaded == [tmp_path / first_srt.name, tmp_path / second_ass.name]


def test_invalid_format_is_rejected_before_listing(download):
    result, client = download(
        [candidate("[Group] Show - 01.srt")], "--prefer-format", "zip"
    )

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr
    assert "--prefer-format" in result.stderr
    assert client.listed == client.downloaded == []
