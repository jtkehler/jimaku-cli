import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
import requests

from jimaku_cli.api import JimakuClient


def transfer_client(monkeypatch, chunks):
    client = JimakuClient("test-key")
    response = requests.Response()
    response.status_code = 200
    response._content_consumed = True
    monkeypatch.setattr(response, "iter_content", chunks)
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response)
    return client


@pytest.mark.parametrize("first_fails", [False, True])
def test_overlapping_transfers_own_their_temporary_files(
    tmp_path, monkeypatch, first_fails
):
    destination = tmp_path / "Show.srt"
    destination.write_bytes(b"original")
    first_streaming = Event()
    finish_first = Event()

    def first_chunks(chunk_size):
        yield b"first-"
        first_streaming.set()
        assert finish_first.wait(5), "first transfer was not released"
        if first_fails:
            raise requests.ConnectionError("stream failed")
        yield b"complete"

    first = transfer_client(monkeypatch, first_chunks)
    second = transfer_client(monkeypatch, lambda chunk_size: iter([b"second-complete"]))
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(first.download_file, "https://example.invalid/1", destination)
        try:
            assert first_streaming.wait(5), "first transfer never started"
            assert destination.read_bytes() == b"original"
            second.download_file("https://example.invalid/2", destination)
            assert destination.read_bytes() == b"second-complete"
        finally:
            finish_first.set()
        if first_fails:
            with pytest.raises(requests.ConnectionError, match="stream failed"):
                pending.result(timeout=5)
        else:
            pending.result(timeout=5)
    assert destination.read_bytes() == (
        b"second-complete" if first_fails else b"first-complete"
    )
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("failure", [None, "stream", "install"])
def test_collision_never_modifies_or_removes_unowned_file(
    tmp_path, monkeypatch, failure
):
    destination = tmp_path / "Show.srt"
    destination.write_bytes(b"original")
    collision = tmp_path / ".jimaku-collision.part"
    collision.write_bytes(b"another transfer")
    tokens = iter(["collision", "owned"])
    monkeypatch.setattr("jimaku_cli.api.secrets.token_hex", lambda size: next(tokens))
    error = OSError("transfer failed")

    def chunks(chunk_size):
        yield b"complete"
        if failure == "stream":
            raise error

    def fail_install(source, dest):
        assert source.read_bytes() == b"complete"
        raise error

    client = transfer_client(monkeypatch, chunks)
    if failure == "install":
        monkeypatch.setattr("jimaku_cli.api.os.replace", fail_install)
    if failure:
        with pytest.raises(OSError) as caught:
            client.download_file("https://example.invalid/sub", destination)
        assert caught.value is error
    else:
        client.download_file("https://example.invalid/sub", destination)
    assert destination.read_bytes() == (b"original" if failure else b"complete")
    assert collision.read_bytes() == b"another transfer"
    assert set(tmp_path.iterdir()) == {destination, collision}


@pytest.mark.parametrize("overwrite", [False, True])
def test_transfer_uses_normal_umask_permissions(tmp_path, monkeypatch, overwrite):
    destination = tmp_path / "Show.srt"
    if overwrite:
        destination.write_bytes(b"original")
        destination.chmod(0o600)
    client = transfer_client(monkeypatch, lambda chunk_size: iter([b"complete"]))
    previous_umask = os.umask(0o027)
    try:
        client.download_file("https://example.invalid/sub", destination)
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640


def test_long_destination_name_does_not_lengthen_temporary(tmp_path, monkeypatch):
    limit = os.pathconf(tmp_path, "PC_NAME_MAX")
    destination = tmp_path / ("a" * (limit - len(".srt")) + ".srt")
    client = transfer_client(monkeypatch, lambda chunk_size: iter([b"complete"]))
    client.download_file("https://example.invalid/sub", destination)
    assert destination.read_bytes() == b"complete"
    assert list(tmp_path.iterdir()) == [destination]


def test_owned_handle_is_exclusive_and_closed_before_install(tmp_path, monkeypatch):
    destination = tmp_path / "Show.srt"
    original_open = Path.open
    original_replace = os.replace
    handles = []

    def track_open(path, mode="r", *args, **kwargs):
        assert mode == "xb"
        assert path.parent == destination.parent
        handle = original_open(path, mode, *args, **kwargs)
        handles.append(handle)
        return handle

    def check_replace(source, dest):
        assert len(handles) == 1
        assert handles[0].closed
        original_replace(source, dest)

    client = transfer_client(monkeypatch, lambda chunk_size: iter([b"complete"]))
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", track_open)
        scoped.setattr("jimaku_cli.api.os.replace", check_replace)
        client.download_file("https://example.invalid/sub", destination)
    assert destination.read_bytes() == b"complete"
