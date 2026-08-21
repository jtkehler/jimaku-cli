import os
import re
import unicodedata
from pathlib import Path

import ffsubsync
import pysubs2
from ffsubsync.ffsubsync import make_parser
from pysubs2 import SSAEvent

# Halfwidth and fullwidth are both in use, and which one is used says nothing
# about what is inside: one provider writes speaker labels fullwidth and furigana
# halfwidth, another writes both halfwidth. Since furigana is stripped too, the
# distinction does not matter -- every balanced group goes.
PAREN_OPEN = "（("
PAREN_CLOSE = "）)"

# ARIB doubles a halfwidth delimiter -- `((...))` -- around a voice heard off
# screen, down a phone, or in memory. What it wraps is speech, so neither half is
# annotation and both pass through. Only at the top level: inside an open group
# the same pair is two nested closes, which is what a provider writing both
# speaker labels and furigana halfwidth produces. Only halfwidth doubles this
# way; `)）` is a nested ruby close and must still pair.
VOICE_MARKER = "()"

# Only parentheses. The other brackets carry dialogue: `＜＞` and `《》` mark
# interior monologue, `「」` quotation. One episode in the sample corpus is 101
# `＜...＞` lines, a third of its script, and stripping them would delete it.

LINE_BREAK = re.compile(r"\\N|\\n|\n")

# pysubs2 hands back SRT's HTML tags as literal text, so the strip has to read
# them the way it reads an override block: markup, not something the cue says.
MARKUP = re.compile(SSAEvent.OVERRIDE_SEQUENCE.pattern + r"|</?[a-zA-Z][^>]*>")

# A pair the strip left nothing between is litter: it renders as nothing, and
# keeping it would have `（信子）` cost the cue a `<i></i>` it never asked for.
# Both spellings, since SRT's tags reach the strip as literal text.
EMPTY_PAIR = re.compile(
    r"\{\\(\w)1\}\s*\{\\\1 ?0\}"
    r"|<([a-zA-Z]+)[^>]*>\s*</\2>"
)

# Wave dashes and dashes pad a bare music marker: "♪~", "~♪", "♪--". The notes
# themselves need no list -- they are symbols -- but this padding is dashes and a
# prolonged sound mark, which are punctuation and a letter.
MUSIC_PADDING = "~～〜ー-–—―‐"


class AlignError(Exception):
    """ffsubsync ran to completion but did not produce a synced subtitle."""


def sync_subtitle(subtitle: Path, video: Path) -> None:
    """Align subtitle timing to video with ffsubsync.

    Replaces original subtitle on successful align.
    """
    synced = subtitle.with_suffix(".ffsubsync" + subtitle.suffix)
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


def strip_ih(subtitle: Path) -> None:
    """Remove hearing-impaired annotations and furigana from a subtitle.

    Drops parenthesised speaker labels, sound effects and ruby readings, then
    any cue left with nothing but decoration. Replaces original in place.
    """
    # SRT is the only format whose tags pysubs2 rewrites on the way through:
    # it converts `<i>` to `{\i1}` on load and drops `{...}` on save. The pair
    # of flags is what round-trips a cue the strip never touched. Every format
    # takes both as **kwargs, so no branching is needed.
    subs = pysubs2.load(subtitle, encoding="utf-8-sig", keep_html_tags=True)

    before = [event.text for event in subs]
    for event in subs:
        # Comment lines and {\p1} drawings are not dialogue; leave them alone.
        if event.is_text:
            event.text = tidy_lines(strip_parenthesised(event.text))
    kept = [event for event in subs if not event.is_text or is_dialogue(event)]

    # Nothing to strip: leave the file alone rather than round-trip it through
    # pysubs2, which reformats margins and stamps its own header comment.
    if [event.text for event in kept] == before:
        return
    subs.events = kept

    stripped = subtitle.with_suffix(".stripih" + subtitle.suffix)
    try:
        subs.save(stripped, encoding="utf-8", keep_ssa_tags=True)
        os.replace(stripped, subtitle)
    finally:
        stripped.unlink(missing_ok=True)


def strip_parenthesised(text: str) -> str:
    """Remove every balanced parenthesised group, leaving override tags intact.

    Groups nest -- `（大谷敦士(おおたにあつし)）` puts furigana inside a speaker
    label -- so a regex stopping at the first close leaves a stray `）` behind.
    A doubled halfwidth delimiter is not a group *at the top level*: what
    `((...))` wraps is speech, so neither the marker nor the line goes, whether
    the span closes in this cue or runs on into a later one. Inside an open group
    the same pair is two nested closes -- `(大谷敦士(おおたにあつし))` -- and
    pairs normally. An unmatched parenthesis is punctuation the strip did not
    make, so it is left where it is.
    """
    # Markup is opaque: counting the parens inside `{\pos(320,240)}` would
    # turn it into `{\pos}`, and an HTML tag is markup for the same reason.
    tokens: list[str] = []
    index = 0
    while index < len(text):
        block = MARKUP.match(text, index)
        if block:
            tokens.append(block.group())
            index = block.end()
        else:
            tokens.append(text[index])
            index += 1

    drop = [False] * len(tokens)
    open_positions: list[int] = []
    position = 0
    while position < len(tokens):
        token = tokens[position]
        # What the marker wraps is speech, so it is not a group and neither half
        # is annotation: both are passed through. Only at the top level -- with a
        # group open, the pair is a nested close and its parent, not a marker.
        if (
            not open_positions
            and token in VOICE_MARKER
            and tokens[position + 1 : position + 2] == [token]
        ):
            position += 2
            continue
        if token in PAREN_OPEN:
            open_positions.append(position)
        elif token in PAREN_CLOSE and open_positions:
            for inside in range(open_positions.pop(), position + 1):
                drop[inside] = True
        position += 1

    return "".join(token for position, token in enumerate(tokens) if not drop[position])


def tidy_lines(text: str) -> str:
    """Drop lines a strip emptied, so a removed label leaves no blank line."""
    kept = (line.strip() for line in LINE_BREAK.split(text))
    return sweep_empty(r"\N".join(line for line in kept if visible(line)))


def sweep_empty(text: str) -> str:
    """Remove tag pairs the strip left with nothing between them."""
    previous = None
    while previous != text:
        previous, text = text, EMPTY_PAIR.sub("", text)
    return text


def bare(text: str) -> str:
    """What the cue renders as: markup and breaks gone, surrounding space dropped."""
    return LINE_BREAK.sub("", MARKUP.sub("", text)).strip()


def visible(text: str) -> bool:
    """Whether text renders as anything once markup is removed."""
    return bool(bare(text))


def decoration(char: str) -> bool:
    """Whether a character is an icon, its padding, or renders as nothing."""
    return (
        char.isspace()
        or char in MUSIC_PADDING
        or unicodedata.category(char)[0] in "SMC"
    )


def is_dialogue(event: SSAEvent) -> bool:
    """Whether a cue still says something -- not empty, not bare decoration.

    `♪～` marks music with no lyrics, and a `📱` left behind by `📱（受信音）`
    marks a sound: both are annotation a hearing viewer does not need. `♪ lyrics ♪`
    is a song a learner wants, so requiring a symbol *and* nothing else separates
    them -- and it is what keeps `…` and `！？`, which are punctuation, as lines.
    """
    # `plaintext` only knows about `{...}`, and SRT's HTML tags reach here as
    # literal text -- `<` and `>` are symbols, so `<i>♪～</i>` would read as a
    # line. Dropping breaks too is what keeps `♪～\N♪～` a dropped cue.
    plain = bare(event.text)
    if not plain:
        return False
    icon = any(unicodedata.category(char)[0] == "S" for char in plain)
    return not (icon and all(decoration(char) for char in plain))
