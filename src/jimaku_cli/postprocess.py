import os
import shutil
from pathlib import Path

import ffsubsync
from ffsubsync.ffsubsync import make_parser

from .strip_ih import strip_ih, temporary_path

__all__ = ["strip_ih", "sync_subtitle"]


class AlignError(Exception):
    """ffsubsync ran to completion but did not produce a synced subtitle."""


def sync_subtitle(subtitle: Path, video: Path) -> None:
    """Align subtitle timing to video with ffsubsync.

    Replaces original subtitle on successful align.
    """
    synced = temporary_path(subtitle, ".ffsubsync")
    try:
        args = make_parser().parse_args(
            [str(video), "-i", str(subtitle), "-o", str(synced)]
        )
        result = ffsubsync.run(args)
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
