"""Download reporting and diagnostic logging, all on stderr."""

import logging
import os
import sys
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal, override

import typer

if TYPE_CHECKING:
    from .postprocess import AlignmentResult
    from .strip_ih import StripResult

Outcome = Literal["download", "skip", "missing", "failed"]
# Outcome: (summary label, color), in summary order.
_OUTCOMES: dict[Outcome, tuple[str, str | None]] = {
    "download": ("downloaded", typer.colors.GREEN),
    "skip": ("skipped", None),
    "missing": ("missing", typer.colors.YELLOW),
    "failed": ("failed", typer.colors.RED),
}
_TAG_WIDTH = len("[download]")


def inline(value: str) -> str:
    """Keep filenames and messages on one terminal line."""
    escapes = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}
    return "".join(
        escapes.get(char, f"\\x{ord(char):02x}")
        if ord(char) < 32 or ord(char) == 127
        else char
        for char in value
    )


def write_stderr(message: str) -> None:
    color = (
        False
        if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb"
        else None
    )
    typer.echo(message, err=True, color=color)


class Reporter:
    """Count outcomes and show the detail requested for this download run."""

    def __init__(self, *, quiet: bool = False, verbose: bool = False) -> None:
        self.quiet: bool = quiet
        self.shown: tuple[Outcome, ...] = (
            ("download", "failed") if quiet else tuple(_OUTCOMES)
        )
        self.counts: Counter[Outcome] = Counter()
        self.interactive: bool = (
            not quiet
            and not verbose
            and sys.stderr.isatty()
            and os.environ.get("TERM") != "dumb"
        )

    def record(
        self,
        outcome: Outcome,
        subject: str,
        message: str = "",
        *,
        target: str | None = None,
    ) -> None:
        self.counts[outcome] += 1
        if outcome not in self.shown:
            return
        tag = typer.style(
            f"[{outcome}]".ljust(_TAG_WIDTH),
            fg=_OUTCOMES[outcome][1],
            bold=True,
            dim=True if outcome == "skip" else None,
        )
        parts = [inline(subject)]
        if target and target != subject:
            parts.append(f"-> {inline(target)}")
        if message:
            parts.append(inline(message))
        write_stderr(f"{tag} {' '.join(parts)}")

    def step(self, subject: str, name: str, detail: str) -> None:
        write_stderr(
            f"{'':{_TAG_WIDTH}} {inline(subject)} {inline(name)}: {inline(detail)}"
        )

    def stripped(self, subject: str, result: StripResult) -> None:
        detail = {
            "updated": "updated",
            "unchanged": "unchanged",
            "unsupported": "not processed (unsupported format)",
            "preserved": "unchanged (SRT contains drawing events)",
        }[result.status]
        if result.status == "updated" and not self.quiet:
            detail += f"; {result.modified_cues} cues modified, {result.removed_cues} cues removed"
        self.step(subject, "strip_ih", detail)

    def aligned(self, subject: str, result: AlignmentResult) -> None:
        details = ["complete"]
        if not self.quiet:
            if result.offset_seconds is not None:
                details.append(f"offset {result.offset_seconds:+.3f}s")
            if result.framerate_scale_factor is not None:
                details.append(f"scale {result.framerate_scale_factor:.6f}")
        self.step(subject, "ffsubsync", "; ".join(details))

    def print_summary(self) -> None:
        if any(self.counts[outcome] for outcome in self.shown):
            tally = ", ".join(
                f"{self.counts[outcome]} {_OUTCOMES[outcome][0]}"
                for outcome in self.shown
            )
            write_stderr(f"summary: {tally}")

    @property
    def exit_code(self) -> int:
        return int(bool(self.counts["failed"]))


class _DiagnosticFormatter(logging.Formatter):
    def __init__(self, api_key: str) -> None:
        super().__init__("%(levelname)s %(name)s: %(message)s")
        self.api_key: str = api_key

    @override
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if self.api_key:
            message = message.replace(self.api_key, "[redacted]")
        return "\n".join(inline(line) for line in message.splitlines())


@contextmanager
def diagnostic_session(*, verbose: bool = False, api_key: str = "") -> Generator[None]:
    """Configure only our three logging namespaces for this command."""
    handler = logging.StreamHandler() if verbose else logging.NullHandler()
    handler.setFormatter(_DiagnosticFormatter(api_key))
    previous: list[tuple[logging.Logger, int, list[logging.Handler], bool]] = []
    for name in ("jimaku_cli", "ffsubsync", "srt"):
        logger = logging.getLogger(name)
        previous.append((logger, logger.level, logger.handlers, logger.propagate))
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    try:
        yield
    finally:
        for logger, level, handlers, propagate in previous:
            logger.handlers = handlers
            logger.propagate = propagate
            logger.setLevel(level)
        handler.close()


def log_error(message: str) -> None:
    prefix = typer.style("error:", fg=typer.colors.RED, bold=True)
    write_stderr(f"{prefix} {inline(message)}")
