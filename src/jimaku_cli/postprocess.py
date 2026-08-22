import os
from pathlib import Path

import ffsubsync
from ffsubsync.ffsubsync import make_parser

from .strip_ih import strip_ih, temporary_path


class AlignError(Exception):
    """ffsubsync ran to completion but did not produce a synced subtitle."""


def sync_subtitle(subtitle: Path, video: Path) -> None:
    """Align subtitle timing to video with ffsubsync.

    Replaces original subtitle on successful align.
    """
    synced = temporary_path(subtitle, ".ffsubsync")
    args = make_parser().parse_args(
        [str(video), "-i", str(subtitle), "-o", str(synced)]
    )
    try:
        result = ffsubsync.run(args)
        if not result.get("sync_was_successful"):
            raise AlignError(
                f"ffsubsync could not sync {subtitle.name} to {video.name}"
            )
        os.replace(synced, subtitle)
    finally:
        synced.unlink(missing_ok=True)
