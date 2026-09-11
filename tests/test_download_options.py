"""Output suffixes and configuration overrides at the download command boundary."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jimaku_cli.api import FileEntry
from jimaku_cli.download import app


class DownloadClient:
    api_key = "fixture-key"

    def __init__(self, filename: str):
        self.file = FileEntry(
            url="https://example.invalid/subtitle",
            name=filename,
            size=8,
            last_modified="2026-01-01T00:00:00Z",
        )
        self.downloaded: list[Path] = []

    def get_files(self, entry_id, episode=None):
        return [self.file]

    def download_file(self, url, dest):
        dest.write_bytes(b"subtitle")
        self.downloaded.append(dest)


def invoke(directory, client, *flags):
    return CliRunner().invoke(
        app,
        [
            "--id", "1", str(directory), "--release", "re:.",
            "--no-overwrite", "--no-strip-ih", "--no-align", *flags,
        ],
        obj=client,
        catch_exceptions=False,
    )


@pytest.mark.parametrize("suffix", [".SRT", ".Ass", ".SSA", ".VTT", ".SUB"])
@pytest.mark.parametrize("rename", [False, True])
def test_download_normalizes_only_the_final_extension(tmp_path, monkeypatch, suffix, rename):
    # Release parsing is independent of the output extension contract.
    monkeypatch.setattr("jimaku_cli.download.parse_release", lambda name: "Group")
    video = tmp_path / "[Group] Show - 01 [1080p].MKV"
    video.write_bytes(b"video")
    remote_stem = "[Group] Show - 01.WEB.JA"
    client = DownloadClient(remote_stem + suffix)
    expected_stem = video.stem + ".group.ja" if rename else remote_stem
    expected = tmp_path / (expected_stem + suffix.lower())

    result = invoke(tmp_path, client, "--rename" if rename else "--no-rename")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert client.downloaded == [expected]
    assert expected.read_bytes() == b"subtitle"
    assert video.read_bytes() == b"video"
    assert sorted(tmp_path.iterdir()) == sorted([video, expected])


def test_normalized_output_is_used_for_skip_existing(tmp_path):
    (tmp_path / "[Group] Show - 01.mkv").touch()
    existing = tmp_path / "[Group] Show - 01.srt"
    existing.write_bytes(b"existing")
    client = DownloadClient("[Group] Show - 01.SRT")

    result = invoke(tmp_path, client, "--no-rename")

    assert result.exit_code == 0
    assert client.downloaded == []
    assert existing.read_bytes() == b"existing"
    assert "[skip]" in result.stderr
    assert not (tmp_path / client.file.name).exists()
