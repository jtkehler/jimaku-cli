import pytest
import requests

from jimaku_cli.api import JimakuClient, JimakuError
from jimaku_cli.output import diagnostic_session


@pytest.fixture
def http(monkeypatch):
    client = JimakuClient("secret-key")
    response = requests.Response()
    response.status_code = 200
    response._content = b"[]"
    response._content_consumed = True
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(client.session, "get", get)
    return client, response, calls


@pytest.mark.parametrize("verbose", [False, True])
def test_api_diagnostics_show_request_and_status_without_changing_request(
    http, capsys, verbose
):
    client, _, calls = http
    with diagnostic_session(verbose=verbose, api_key=client.api_key):
        assert client.get_files(1, 3) == []
    assert calls == [
        (
            "https://jimaku.cc/api/entries/1/files",
            {
                "params": {"episode": 3},
                "timeout": 10.0,
            },
        )
    ]
    captured = capsys.readouterr()
    assert captured.out == ""
    if not verbose:
        assert captured.err == ""
    else:
        assert "GET /api/entries/1/files {'episode': 3}" in captured.err
        assert "GET /api/entries/1/files -> 200" in captured.err
    assert "secret-key" not in captured.err and "Authorization" not in captured.err


@pytest.mark.parametrize(
    "status, body, exception",
    [
        (503, b'{"error":"private response body", "code":12}', JimakuError),
        (200, b"invalid JSON", requests.exceptions.JSONDecodeError),
    ],
)
def test_diagnostics_keep_http_status_when_response_decoding_fails(
    http, capsys, status, body, exception
):
    client, response, calls = http
    response.status_code = status
    response._content = body
    with diagnostic_session(verbose=True), pytest.raises(exception) as caught:
        client.get_files(1)
    text = capsys.readouterr().err
    assert len(calls) == 1
    assert f"-> {status}" in text
    assert "private response body" not in text and "invalid JSON" not in text
    if status == 503:
        assert (caught.value.status, caught.value.code) == (503, 12)


def test_transport_failure_preserves_exception_without_logging_its_sensitive_message(
    http, monkeypatch, capsys
):
    client, _, _ = http
    error = requests.ConnectTimeout("request contained secret-key")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(client.session, "get", fail)
    with (
        diagnostic_session(verbose=True),
        pytest.raises(requests.ConnectTimeout) as caught,
    ):
        client.get_files(1)
    assert caught.value is error
    text = capsys.readouterr().err
    assert "GET /api/entries/1/files" in text
    assert "secret-key" not in text
    assert "->" not in text


def test_transfer_diagnostics_use_filename_instead_of_signed_url(
    http, tmp_path, capsys
):
    client, response, calls = http
    response._content = b"subtitle content"
    destination = tmp_path / "Show.srt"
    url = "https://user:password@example.invalid/sub.srt?token=signed-secret#private"
    with diagnostic_session(verbose=True):
        client.download_file(url, destination)
    assert calls == [(url, {"timeout": 10.0, "stream": True})]
    assert destination.read_bytes() == b"subtitle content"
    assert list(tmp_path.iterdir()) == [destination]
    text = capsys.readouterr().err
    assert "GET subtitle Show.srt -> 200" in text
    assert all(
        secret not in text
        for secret in ("user:", "password", "signed-secret", "private")
    )


@pytest.mark.parametrize("failure", ["stream", "status", "install"])
def test_failed_transfer_preserves_original_and_cleanup(
    http, tmp_path, monkeypatch, capsys, failure
):
    client, response, calls = http
    destination = tmp_path / "Show.srt"
    destination.write_bytes(b"original")

    def chunks(chunk_size):
        assert chunk_size == 65536
        yield b"partial"
        raise requests.ConnectionError("sensitive request details")

    def fail_replace(source, dest):
        raise OSError("cannot install")

    if failure == "stream":
        monkeypatch.setattr(response, "iter_content", chunks)
    elif failure == "status":
        response.status_code = 503
    else:
        monkeypatch.setattr("jimaku_cli.api.os.replace", fail_replace)
    with diagnostic_session(verbose=True), pytest.raises((OSError, JimakuError)):
        client.download_file(
            "https://example.invalid/sub.srt?token=private", destination
        )
    assert len(calls) == 1
    assert destination.read_bytes() == b"original"
    text = capsys.readouterr().err
    assert "private" not in text and "sensitive request details" not in text
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("verbose", [False, True])
def test_diagnostics_do_not_replace_requests_invalid_url_error(tmp_path, verbose):
    destination = tmp_path / "Show.srt"
    partial = destination.with_suffix(".srt.part")
    partial.write_bytes(b"old partial")
    with (
        diagnostic_session(verbose=verbose),
        pytest.raises(requests.exceptions.InvalidURL),
    ):
        JimakuClient("secret-key").download_file("http://[bad", destination)
    assert partial.read_bytes() == b"old partial"
    assert list(tmp_path.iterdir()) == [partial]
    assert not destination.exists()
