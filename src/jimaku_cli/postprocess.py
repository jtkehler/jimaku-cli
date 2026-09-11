import logging
import math
import os
import shutil
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from .strip_ih import strip_ih, temporary_path

__all__ = ["AlignmentResult", "strip_ih", "sync_subtitle"]


@dataclass(frozen=True)
class AlignmentResult:
    offset_seconds: float | None = None
    framerate_scale_factor: float | None = None


class AlignError(Exception):
    """ffsubsync ran to completion but did not produce a synced subtitle."""


def _load_ffsubsync():
    # Prevent ffsubsync's import-time basicConfig from configuring root logging.
    root = logging.getLogger()
    guard = logging.NullHandler()
    root.addHandler(guard)
    try:
        import ffsubsync
        from ffsubsync.ffsubsync import make_parser
    finally:
        root.removeHandler(guard)
        guard.close()
    return ffsubsync, make_parser


@contextmanager
def _silence_native_progress() -> Generator[None]:
    # ffsubsync 0.5.1 has no option to hide just its progress bar.
    # Replace only its module binding, never the global tqdm factory.
    from ffsubsync import speech_transformers as speech

    original = speech.tqdm

    def quiet_tqdm(*args, **kwargs):
        return original.tqdm(*args, **(kwargs | {"disable": True}))

    speech.tqdm = SimpleNamespace(tqdm=quiet_tqdm)
    try:
        yield
    finally:
        speech.tqdm = original


def _finite_number(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return float(value)
    return None


def sync_subtitle(
    subtitle: Path,
    video: Path,
    *,
    show_progress: bool = True,
) -> AlignmentResult:
    """Align subtitle timing to video with ffsubsync.

    Replaces original subtitle on successful align.
    """
    synced = temporary_path(subtitle, ".ffsubsync")
    try:
        backend, make_parser = _load_ffsubsync()
        args = make_parser().parse_args(
            [str(video), "-i", str(subtitle), "-o", str(synced)]
        )
        with nullcontext() if show_progress else _silence_native_progress():
            result = backend.run(args)
        if (
            not result.get("sync_was_successful")
            or not synced.exists()
            or synced.stat().st_size == 0
        ):
            raise AlignError(
                f"ffsubsync could not sync {subtitle.name} to {video.name}"
            )
        shutil.copymode(subtitle, synced)
        os.replace(synced, subtitle)
    finally:
        synced.unlink(missing_ok=True)
    return AlignmentResult(
        offset_seconds=_finite_number(result.get("offset_seconds")),
        framerate_scale_factor=_finite_number(result.get("framerate_scale_factor")),
    )
