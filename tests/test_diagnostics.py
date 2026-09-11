import logging
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("verbose", [False, True])
def test_only_diagnostic_tier_emits_selected_logs(capsys, verbose):
    from jimaku_cli.output import diagnostic_session

    with diagnostic_session(verbose=verbose, api_key="secret-key"):
        logging.getLogger("jimaku_cli.api").debug("request %s", "secret-key")
        logging.getLogger("ffsubsync").info("alignment details")
        logging.getLogger("srt").warning("parse details")
        logging.getLogger("urllib3").debug("unrelated request")
    assert capsys.readouterr().err.splitlines() == (
        []
        if not verbose
        else [
            "DEBUG jimaku_cli.api: request [redacted]",
            "INFO ffsubsync: alignment details",
            "WARNING srt: parse details",
        ]
    )


@pytest.mark.parametrize("fails", [False, True])
def test_configured_loggers_are_restored_after_each_command(capsys, monkeypatch, fails):
    from jimaku_cli.output import diagnostic_session

    root = logging.getLogger()
    root_before = (root.level, root.handlers[:])
    logger = logging.getLogger("ffsubsync")
    monkeypatch.setattr(logger, "handlers", [logging.NullHandler()])
    monkeypatch.setattr(logger, "propagate", False)
    before = (logger.level, logger.handlers[:], logger.propagate)
    try:
        with diagnostic_session(verbose=True):
            logging.getLogger("ffsubsync.child").debug("one record")
            if fails:
                raise RuntimeError("fixture failure")
    except RuntimeError:
        assert fails
    assert capsys.readouterr().err.count("one record") == 1
    assert (root.level, root.handlers) == root_before
    assert (logger.level, logger.handlers, logger.propagate) == before


def test_diagnostic_tracebacks_redact_keys(capsys):
    from jimaku_cli.output import diagnostic_session

    with diagnostic_session(verbose=True, api_key="secret-key"):
        try:
            raise ValueError("rejected secret-key\nsecond line")
        except ValueError:
            logging.getLogger("jimaku_cli.download").debug("failed", exc_info=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" in captured.err and "ValueError" in captured.err
    assert "secret-key" not in captured.err
    assert "[redacted]" in captured.err
    assert captured.err.startswith("DEBUG jimaku_cli.download: failed\nTraceback")
    assert "diagnostic: " not in captured.err


@pytest.mark.parametrize(
    "operation",
    [
        "import jimaku_cli.cli",
        "from jimaku_cli.postprocess import _load_ffsubsync; _load_ffsubsync()",
    ],
)
def test_fresh_import_leaves_root_logging_alone(tmp_path, operation):
    code = (
        "import logging\n"
        "root = logging.getLogger()\n"
        "root.setLevel(logging.ERROR)\n"
        "assert not root.handlers\n"
        f"{operation}\n"
        "assert not root.handlers\n"
        "assert root.level == logging.ERROR\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
        env=dict(os.environ, XDG_CONFIG_HOME=str(tmp_path)),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
