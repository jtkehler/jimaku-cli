"""Remove hearing-impaired annotations from subtitle files."""

import codecs
import os
import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["strip_ih"]

# The strip only ever changes cue text, so the file is edited in place around
# the text fields rather than reserialized. Everything else -- timestamps, line
# endings, the BOM, headers, `[Aegisub Extradata]`, SRT coordinate fields,
# indentation -- is copied through as the bytes it arrived as, because it is
# never rewritten. pysubs2 clamped timestamps past ten hours and negative ones,
# normalized CRLF and dropped ideographic indentation on the way through.

# Japanese SDH uses fullwidth parentheses for speaker IDs and sound effects, but
# Netflix also prescribes the same delimiters for whispered or mouthed dialogue.
# Parentheses therefore identify a *candidate*, not something that is safe to
# delete by themselves.
PAREN_PAIRS = {"（": "）", "(": ")"}
PAREN_CLOSE = frozenset(PAREN_PAIRS.values())

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

# Captures the separator so `tidy_lines` can put back the break it found: SRT
# writes real newlines and SubStation writes `\N`, and a cue rejoined with the
# wrong one is a cue with a literal `\N` in its text or a broken block. `\r\n`
# is one break and has to match as one, or dropping the line before it strands
# a carriage return in the middle of the cue.
LINE_BREAK = re.compile(r"(\\N|\\n|\r\n|\n|\r)")

# SRT's HTML tags are markup on the same terms as an override block: neither is
# something the cue says. The brace pattern is pysubs2's `OVERRIDE_SEQUENCE`.
OVERRIDE_SEQUENCE = re.compile(r"\{[^}]*\}")
MARKUP = re.compile(OVERRIDE_SEQUENCE.pattern + r"|</?[a-zA-Z][^>]*>")

# `\p1`..`\p9` inside an override block open a vector drawing, which is not
# text. pysubs2 reads the scale as a single digit and calls the event a drawing
# when any fragment of it is scaled above zero; matching that keeps the raw path
# deciding what is editable exactly as `SSAEvent.is_text` did.
DRAWING = re.compile(r"\\p[1-9]")

# A file is split on its own terminators rather than with `str.splitlines`,
# which also breaks on U+2028 and friends -- characters that appear inside cue
# text and must not be mistaken for the end of a line.
FILE_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+")

# A SubStation event line, up to the colon that ends its keyword.
EVENT_LINE = re.compile(r"[ \t]*(Dialogue|Comment)[ \t]*:", re.IGNORECASE)

# An SRT cue is framed on its timing line -- two times joined by an arrow --
# and the ordinal above it is decoration. Framing on the times is what lets a
# blank line sit inside a cue without ending it; `timing_line` says why the
# arrow has to be there as well.
CUE_NUMBER = re.compile(r"([ \t]*)(\d+)([ \t]*(?:\r\n|\n|\r)?)")
TIMESTAMP = re.compile(r"\d{1,2}:\d{1,2}:\d{1,2}[.,]\d{1,3}")

# HTML ruby has useful semantic structure: the base text is dialogue, while
# <rt> is its reading and <rp> is fallback punctuation around that reading.
# Work one non-nested container at a time so an unclosed <rt> cannot consume
# dialogue through the next <ruby> element.
RUBY_ELEMENT = re.compile(
    r"<ruby\b[^>]*>(?:(?!</?ruby\b).)*?</ruby\s*>",
    re.IGNORECASE | re.DOTALL,
)
RUBY_READING = re.compile(r"<(rt|rp)\b[^>]*>[^<]*</\1\s*>", re.IGNORECASE)
RUBY_PART = re.compile(r"</?(?:rt|rp)\b", re.IGNORECASE)
RUBY_CONTAINER = re.compile(r"</?(?:ruby|rb)\b[^>]*>", re.IGNORECASE)

# A pair the strip left nothing between is litter: it renders as nothing, and
# keeping it would have `（信子）` cost the cue a `<i></i>` it never asked for.
# Both spellings, since SRT's tags reach the strip as literal text.
EMPTY_PAIR = re.compile(
    r"\{\\(\w)1\}\s*\{\\\1 ?0\}"
    r"|<([a-zA-Z]+)[^>]*>\s*</\2>"
)

# Wave dashes and dashes pad a bare music marker: "♪~", "~♪", "♪--". Keep the
# note list deliberately narrow so unrelated symbols are never treated as IH.
MUSIC_PADDING = "~～〜ー-–—―‐"
MUSIC_NOTES = "♪♫♬♩"

# A display line may open with a marker that belongs to the annotation rather
# than to the dialogue. Anything alphanumeric is real text and blocks the strip;
# the rest does not, and of that only an audio-source marker leaves with the
# group it annotates -- an icon for a phone, a television or a speaker, or the
# chevrons a rip writes around an off-screen voice. Everything else that renders
# is retained, because the strip did not put it there: a dash or a bullet still
# separates two speakers once their names are gone, a bracket still needs its
# other half, and a music marker is the music rule's to drop, not this one's.
# Category is the test, with the chevrons named because they are symbols that a
# bracket rule would otherwise catch: measured over the corpus `≪` carries a
# closing `≫` on 0.4% of the lines it opens, while `《`, `「` and `〈` carry
# theirs on 63-96%, so the chevrons mark and the brackets pair.
ANNOTATION_MARKS = frozenset("≪≫∈＼")
MARK_CATEGORIES = frozenset(("So", "Co", "Cf"))

# Standalone parentheticals are only removed when their wording is clearly an
# accessibility label. False negatives are intentional: `(今日は仕事でしょ？)`
# is dialogue in the corpus, while `（ドアが開く音）` is not. Keep these lists to
# stable caption vocabulary rather than trying to recognize arbitrary Japanese
# sentences as actions or sounds.
#
# The split carries as much weight as the contents. Sound wording is evidence
# that a group describes what is heard, so it vetoes learning that group as a
# speaker label. Wording that names *who* speaks is not: `（一同）はい！` labels
# the line it precedes exactly as `（信子）` does, and vetoing it would leave that
# label in place while the identical `（２人）` stripped.
#
# `鈴` is matched whole rather than as a tail, because it is also how a name ends
# and `（美鈴）` is a girl, not a bell. Measured over the corpus it is the only one
# worth that trade: `鐘`, `息` and `咳` end far more sound words than names, so
# they stay tails and `（震える息）` goes on stripping.
SPEAKER_SUFFIXES = (
    "一同",
    "全員",
    "ナレーション",
    "ナレーター",
    "アナウンス",
    "通訳",
    "電子音声",
    "英語",
    "日本語",
    "外国語",
    "現地語",
    "韓国語",
    "中国語",
    "台湾語",
    "ドイツ語",
)

# Caption notation that qualifies the line rather than naming its speaker.
# One corpus instance against 6,733 files, but deleting dialogue is the
# expensive direction. A minimum length would cost the 37,472 legitimate
# one-character labels -- given names, and the `２人`/`３人` group labels that
# sit alongside `（一同）` -- and rejecting a body that also appears mid-line in
# the same file would cost 11,434 strips, since characters say each other's
# names. A vocabulary entry is what the rest of this block already is.
NON_LABELS = frozenset(("仮",))

SOUND_WORDS = ("鈴",)

SOUND_SUFFIXES = (
    "咳",
    "息",
    "鐘",
    "BGM",
    "あくび",
    "いななき",
    "いびき",
    "うなり声",
    "おなら",
    "くしゃみ",
    "げっぷ",
    "さえずり",
    "ざわめき",
    "しゃっくり",
    "せき",
    "せきこみ",
    "せきばらい",
    "せき込み",
    "ため息",
    "どよめき",
    "ほえ声",
    "まね",
    "アラーム",
    "クラクション",
    "ゴング",
    "サイレン",
    "サウンド",
    "チャイム",
    "ノイズ",
    "ノック",
    "バイブレーター",
    "ファンファーレ",
    "ブザー",
    "ベル",
    "ホイッスル",
    "メロディ",
    "伴奏",
    "効果音",
    "口笛",
    "号砲",
    "吐息",
    "咆哮",
    "咳き込み",
    "咳払い",
    "喝采",
    "嗚咽",
    "地響き",
    "声",
    "声援",
    "寝息",
    "心音",
    "怒号",
    "息切れ",
    "息遣い",
    "悲鳴",
    "手拍子",
    "拍手",
    "指笛",
    "時報",
    "汽笛",
    "沈黙",
    "深呼吸",
    "演奏",
    "爆発",
    "着信",
    "笑い",
    "笑い声",
    "絶叫",
    "羽ばたき",
    "舌打ち",
    "読経",
    "警報",
    "警笛",
    "遠ぼえ",
    "遠吠え",
    "遠雷",
    "銃声",
    "雄たけび",
    "雷鳴",
    "静寂",
    "音",
    "音楽",
    "鳴きまね",
    "鳴き声",
    "鼓動",
    "鼻息",
    "鼻歌",
    "ＢＧＭ",
)

SPOKEN_PUNCTUATION = frozenset("！？?!…。、")
SPOKEN_WORDS = frozenset(
    ("ううん", "うん", "ええ", "いや", "はい", "ダメ", "無理", "駄目")
)
SPOKEN_ENDING = re.compile(
    r"(?:です|ます|ま[ー～〜]?す|ない|たい|って|けど|から|ので|"
    r"のに|"
    r"だ[よねぞぜなわさか]?|[てよねぞぜか])$"
)
HIRAGANA = re.compile(r"[ぁ-ゟ]")
RUBY = re.compile(r"[ぁ-ゟァ-ヿー～〜・･\s]+")
HAN_AT_END = re.compile(r"[㐀-鿿々〆ヵヶ]$")
# A group written halfwidth inside another is furigana, and counting its kana
# would read the label it annotates as speech: `(大谷敦士(おおたにあつし))` is
# 64% hiragana with the reading and 0% without it.
NESTED_RUBY = re.compile(r"\(" + RUBY.pattern + r"\)")
# An honorific or a role names a person, so a group ending in one is a label
# however much kana it holds.
NAME_MARKERS = re.compile(
    r"(?:ちゃん|さん|くん|君|さま|様|先生|せんせー|せんせい|たち|達)$"
)


@dataclass
class Cue:
    """One event, holding its raw text field and the bytes on either side.

    `prefix` and `suffix` are the rest of the event verbatim -- for SubStation
    the field list up to the last comma and the line terminator, for SRT the
    timestamp line and the blank line that closes the block -- so rendering an
    untouched cue reproduces it byte for byte, and dropping one takes its whole
    block with it.
    """

    text: str
    editable: bool
    prefix: str
    suffix: str
    # SRT only: the ordinal line, split so it can be renumbered in place.
    number: tuple[str, str, str] | None = None

    def render(self, ordinal: int, renumber: bool) -> str:
        head = self.prefix
        if self.number is not None:
            indent, digits, terminator = self.number
            head = indent + (str(ordinal) if renumber else digits) + terminator + head
        return head + self.text + self.suffix


@dataclass
class Document:
    """A subtitle file as literal text with the cue text fields carved out.

    Every byte of the file belongs to exactly one literal or one cue, and
    `literals` brackets `cues` on both sides, so a document nothing changed
    renders back to the bytes it was read from.
    """

    literals: list[str]
    cues: list[Cue]
    # SRT numbers its cues, and a gap left by a dropped one trips strict
    # parsers. The ordinals are rewritten only when a cue actually goes, since
    # otherwise the original digits are bytes we have no reason to touch.
    renumbers: bool = False

    def render(self, keep: list[bool]) -> str:
        renumber = self.renumbers and not all(keep)
        parts = [self.literals[0]]
        ordinal = 0
        for cue, literal, kept in zip(self.cues, self.literals[1:], keep):
            if kept:
                ordinal += 1
                parts.append(cue.render(ordinal, renumber))
            parts.append(literal)
        return "".join(parts)


def strip_ih(subtitle: Path) -> None:
    """Remove hearing-impaired annotations and ruby readings from a subtitle.

    Drops high-confidence speaker labels, sound effects, bare music markers,
    parenthesised kana readings after kanji and HTML ruby. Ambiguous
    parenthesised text is dialogue and stays. Edits SubStation and SRT in
    place, rewriting only the text of the cues that changed; every other
    format is left alone.
    """
    readers = {".ass": read_substation, ".ssa": read_substation, ".srt": read_subrip}
    # WebVTT and the binary `.sub` that `SUBTITLE_EXTS` also admits have no
    # reader here. A silent no-op is safer than corrupting a downloaded file.
    read = readers.get(subtitle.suffix.casefold())
    if read is None:
        return

    encoding, bom = subtitle_encoding(subtitle)
    text = subtitle.read_bytes()[len(bom) :].decode(encoding)
    document = read(text)

    labels = speaker_labels(
        strip_html_ruby(cue.text) for cue in document.cues if cue.editable
    )
    keep: list[bool] = []
    for cue in document.cues:
        # Comment lines and {\p1} drawings are not dialogue; leave them alone.
        if not cue.editable:
            keep.append(True)
            continue
        original = cue.text
        without_ruby = strip_html_ruby(original)
        stripped = strip_parenthesised(without_ruby, labels)
        if music_marker_only(stripped):
            stripped = ""
        elif stripped != without_ruby:
            stripped = tidy_lines(stripped)
        if stripped != original:
            cue.text = stripped
        keep.append(cue.text == original or visible(cue.text))

    payload = document.render(keep)
    # Nothing to strip: leave the file alone. Not an optimization -- it is what
    # makes a subtitle with no annotations byte-for-byte untouched.
    if payload == text:
        return

    stripped_file = temporary_path(subtitle, ".stripih")
    try:
        stripped_file.write_bytes(bom + payload.encode(encoding))
        os.replace(stripped_file, subtitle)
    finally:
        stripped_file.unlink(missing_ok=True)


# A temporary name has to fit the filesystem's limit -- 255 bytes on Linux --
# and one subtitle in the sample corpus is already 249 of them. The marker is
# what makes the name unique, and `os.replace` needs the file it renames to be
# a sibling, so the stem is what gives way.
NAME_MAX = 255


def temporary_path(subtitle: Path, marker: str) -> Path:
    """A sibling path for a rewritten file, short enough to create."""
    tail = marker + subtitle.suffix
    stem = subtitle.stem
    while stem and len(os.fsencode(stem + tail)) > NAME_MAX:
        stem = stem[:-1]
    return subtitle.with_name(stem + tail)


# Widest first: a UTF-32 LE mark opens with a UTF-16 LE one. The codecs name a
# byte order rather than leaving it to the encoder, which always writes little
# endian for the bare `utf-16` and `utf-32` names and would silently flip a
# big-endian file. The mark itself is written back verbatim.
BYTE_ORDER_MARKS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
    (codecs.BOM_UTF8, "utf-8"),
)


def subtitle_encoding(subtitle: Path) -> tuple[str, bytes]:
    """Return a codec and the byte-order mark to write back before its output."""
    with subtitle.open("rb") as file:
        prefix = file.read(4)
    for bom, encoding in BYTE_ORDER_MARKS:
        if prefix.startswith(bom):
            return encoding, bom
    return "utf-8", b""


def file_lines(text: str) -> list[str]:
    """Split into lines that each keep their own terminator."""
    return FILE_LINE.findall(text)


def line_terminator(line: str) -> str:
    """The newline a line ends with, if it has one."""
    for terminator in ("\r\n", "\n", "\r"):
        if line.endswith(terminator):
            return terminator
    return ""


def is_drawing(text: str) -> bool:
    """Whether a SubStation event is a vector drawing rather than text."""
    return bool(DRAWING.search("".join(OVERRIDE_SEQUENCE.findall(text))))


def read_substation(text: str) -> Document:
    """Carve the Text field out of every event line in an ASS or SSA file.

    `Text` is last in every Format variant the corpus carries, so the field
    runs from the comma that ends the one before it to the end of the line.
    The index is read from the file's own Format line rather than assumed.
    """
    literals: list[str] = []
    cues: list[Cue] = []
    pending: list[str] = []
    text_index: int | None = None
    in_events = False

    for line in file_lines(text):
        cue = None
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.casefold() == "[events]"
        elif in_events and stripped.casefold().startswith("format:"):
            fields = [field.strip() for field in stripped.split(":", 1)[1].split(",")]
            text_index = fields.index("Text") if "Text" in fields else None
        elif in_events and text_index is not None:
            cue = substation_cue(line, text_index)
        if cue is None:
            pending.append(line)
            continue
        literals.append("".join(pending))
        pending = []
        cues.append(cue)

    literals.append("".join(pending))
    return Document(literals, cues)


def substation_cue(line: str, text_index: int) -> Cue | None:
    """One event line split at the comma that opens its Text field."""
    keyword = EVENT_LINE.match(line)
    if keyword is None:
        return None
    position = keyword.end()
    for _ in range(text_index):
        comma = line.find(",", position)
        # Too few fields to locate Text: malformed, so copy it through whole.
        if comma < 0:
            return None
        position = comma + 1
    terminator = line_terminator(line)
    field = line[position : len(line) - len(terminator)]
    return Cue(
        text=field,
        editable=keyword.group(1).casefold() == "dialogue" and not is_drawing(field),
        prefix=line[:position],
        suffix=terminator,
    )


def timing_line(line: str) -> bool:
    """Whether a line frames an SRT cue: two times joined by an arrow.

    pysubs2 counted the times alone, which reads a dialogue line quoting two
    timecodes as a cue boundary -- splitting the cue, and taking the real line
    with the block it opens. The arrow has to sit between the two times rather
    than merely somewhere on the line, and each time is matched loosely, so a
    rip writing a three-digit hour still frames its cue.
    """
    times = list(TIMESTAMP.finditer(line))
    return len(times) == 2 and "-->" in line[times[0].end() : times[1].start()]


def read_subrip(text: str) -> Document:
    """Carve the text out of every cue in an SRT file.

    A cue runs from its timing line to the ordinal of the next one, so a blank
    line inside a cue stays part of it instead of ending it -- the text after
    such a break is dialogue the strip has to see, and a file that numbers its
    cues badly or not at all still parses. The timing line is copied whole,
    which keeps the `X1:...Y2:` coordinates some rips write after the times.
    """
    lines = file_lines(text)
    stamps = [index for index, line in enumerate(lines) if timing_line(line)]
    # The ordinal above a timestamp opens that cue's block, so it is where the
    # previous cue stops.
    starts = [
        stamp - 1 if stamp and CUE_NUMBER.fullmatch(lines[stamp - 1]) else stamp
        for stamp in stamps
    ]

    literals: list[str] = []
    cues: list[Cue] = []
    previous = 0
    for position, stamp in enumerate(stamps):
        start = starts[position]
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        region = lines[stamp + 1 : end]
        # Trailing blank lines close the block rather than belonging to the cue.
        while region and not region[-1].strip():
            region.pop()
        body = "".join(region)
        terminator = line_terminator(body)
        field = body[: len(body) - len(terminator)]
        number = CUE_NUMBER.fullmatch(lines[start]) if start < stamp else None

        literals.append("".join(lines[previous:start]))
        cues.append(
            Cue(
                text=field,
                editable=not is_drawing(field),
                prefix=lines[stamp],
                suffix=terminator + "".join(lines[stamp + 1 + len(region) : end]),
                number=number.groups() if number else None,
            )
        )
        previous = end

    literals.append("".join(lines[previous:]))
    return Document(literals, cues, renumbers=True)


def strip_html_ruby(text: str) -> str:
    """Remove HTML ruby readings while retaining their base text."""

    def clean(element: re.Match[str]) -> str:
        stripped = RUBY_READING.sub("", element.group())
        if RUBY_PART.search(stripped):
            return element.group()
        return RUBY_CONTAINER.sub("", stripped)

    return RUBY_ELEMENT.sub(clean, text)


def parenthesised_spans(text: str) -> list[tuple[int, int]]:
    """Find strictly balanced top-level parenthetical spans outside markup."""
    spans: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    index = 0
    while index < len(text):
        block = MARKUP.match(text, index)
        if block:
            index = block.end()
            continue

        char = text[index]
        # ARIB's doubled halfwidth opener marks speech, including when its close
        # is in another cue. It is never an annotation delimiter at top level.
        if not stack and char in VOICE_MARKER and text[index : index + 2] == char * 2:
            index += 2
            continue
        if char in PAREN_PAIRS:
            stack.append((PAREN_PAIRS[char], index))
        elif char in PAREN_CLOSE and stack:
            expected, start = stack[-1]
            if char != expected:
                # A malformed outer group is ambiguous. In particular, do not
                # delete a valid-looking inner pair and leave half a sentence.
                stack.clear()
            else:
                stack.pop()
                if not stack:
                    spans.append((start, index + 1))
        index += 1
    return spans


def outside_text(
    text: str, spans: Collection[tuple[int, int]], start: int, end: int
) -> str:
    """Text in a range excluding all balanced parenthetical spans."""
    parts: list[str] = []
    cursor = start
    for span_start, span_end in spans:
        if span_end <= start or span_start >= end:
            continue
        parts.append(text[cursor : max(cursor, span_start)])
        cursor = max(cursor, span_end)
    parts.append(text[cursor:end])
    return "".join(parts)


def group_body(text: str, span: tuple[int, int]) -> str:
    """Visible content inside a parenthetical span."""
    start, end = span
    return bare(text[start + 1 : end - 1])


def line_bounds(text: str, span: tuple[int, int]) -> tuple[int, int]:
    """Index range of the display line a span sits on."""
    start, end = span
    line_start = 0
    for line_break in LINE_BREAK.finditer(text, 0, start):
        line_start = line_break.end()
    next_break = LINE_BREAK.search(text, end)
    return line_start, next_break.start() if next_break else len(text)


def line_context(
    text: str, spans: Collection[tuple[int, int]], span: tuple[int, int]
) -> tuple[str, str]:
    """Visible-line text before and after a parenthetical span."""
    start, end = span
    line_start, line_end = line_bounds(text, span)
    return (
        outside_text(text, spans, line_start, start),
        outside_text(text, spans, end, line_end),
    )


def contextual_payload(text: str) -> bool:
    """Whether context contains more than markup or a bare music marker."""
    return visible(text) and not music_marker_only(text)


def opening_payload(before: str) -> bool:
    """Whether a line opens with dialogue rather than with a marker."""
    return any(char.isalnum() for char in bare(before))


def annotation_marker(opening: str) -> bool:
    """Whether everything a line renders before a group annotates its source."""
    return bool(opening) and all(
        char in ANNOTATION_MARKS
        or (
            unicodedata.category(char) in MARK_CATEGORIES
            and char not in MUSIC_NOTES
        )
        or char.isspace()
        for char in opening
    )


def rendered_positions(
    text: str, spans: Collection[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    """Each character a slice renders, one span apiece, markup and groups aside.

    Single characters rather than runs, so that dropping a marker cannot take an
    override block with it: `{\\pos(172,407)}📻{\\fscx50}（レミ）` must keep both
    blocks and lose only the icon.
    """
    positions: list[tuple[int, int]] = []
    index = start
    while index < end:
        span = next((s for s in spans if s[0] <= index < s[1]), None)
        if span is not None:
            index = span[1]
            continue
        block = MARKUP.match(text, index)
        if block and block.end() > index:
            index = min(block.end(), end)
            continue
        positions.append((index, index + 1))
        index += 1
    return positions


def is_leading_label(
    text: str,
    spans: Collection[tuple[int, int]],
    span: tuple[int, int],
    body: str,
) -> bool:
    """Whether a group names the speaker of dialogue that follows it.

    The question `search` asks while fast-forwarding is the one `download`
    answers, and the same holds here: learning a label and stripping one are the
    same test, so they are the same function.
    """
    before, _ = line_context(text, spans, span)
    after = outside_text(text, spans, span[1], len(text))
    return (
        not opening_payload(before)
        and contextual_payload(after)
        and bool(body)
        and body not in NON_LABELS
        and not looks_spoken(body)
        and not sound_annotation(body)
    )


def looks_spoken(body: str) -> bool:
    """Whether a parenthetical plausibly contains speech rather than a label."""
    body = without_nested_ruby(body)
    if body in SPOKEN_WORDS or re.search(r"[A-Za-z]", body):
        return True
    if any(char in SPOKEN_PUNCTUATION for char in body):
        return True
    # Checked after the punctuation, so a named line that is plainly spoken --
    # `(庄太さーん\N早く ごはん食べて！)` -- still reads as speech on its `！`.
    if NAME_MARKERS.search(body):
        return False
    if SPOKEN_ENDING.search(body):
        return True
    letters = [char for char in body if char.isalpha()]
    return len(letters) >= 5 and len(HIRAGANA.findall(body)) / len(letters) >= 0.6


def without_nested_ruby(body: str) -> str:
    """A group with any furigana written inside it removed."""
    return NESTED_RUBY.sub("", body)


def speaker_annotation(body: str) -> bool:
    """Whether a group names who is speaking rather than what is heard."""
    return without_nested_ruby(body).rstrip().endswith(SPEAKER_SUFFIXES)


def sound_annotation(body: str) -> bool:
    """Whether a group ends in established sound wording.

    Checked against the speaker vocabulary first, so `（電子音声）` is a source
    and not the `声` its tail would otherwise match.
    """
    if speaker_annotation(body):
        return False
    normalized = without_nested_ruby(body).rstrip()
    return normalized.endswith(SOUND_SUFFIXES) or normalized in SOUND_WORDS


def annotation_label(body: str) -> bool:
    """Whether a standalone group is established sound or source wording."""
    return speaker_annotation(body) or sound_annotation(body)


def ruby_reading(text: str, span: tuple[int, int], body: str) -> bool:
    """Whether a halfwidth group is a kana reading immediately after kanji."""
    start, _ = span
    if text[start] != "(" or not body or not RUBY.fullmatch(body):
        return False
    prefix = MARKUP.sub("", text[:start])
    if re.search(r"(?:\\N|\\n|\n)\s*$", prefix):
        return False
    return bool(HAN_AT_END.search(prefix))


def speaker_labels(texts: Iterable[str]) -> set[str]:
    """Learn labels used before visible dialogue in this subtitle."""
    labels: set[str] = set()
    for text in texts:
        spans = parenthesised_spans(text)
        for span in spans:
            body = group_body(text, span)
            if is_leading_label(text, spans, span, body):
                labels.add(body)
    return labels


def strip_parenthesised(text: str, labels: Collection[str] = ()) -> str:
    """Remove only high-confidence parenthetical annotations and ruby.

    Groups nest and markup is opaque. Strict matching means malformed or
    unmatched punctuation survives whole, while ARIB's top-level `((...))`
    voice marker is never treated as an annotation. A group is removed when it
    is a known leading label, clearly precedes visible dialogue, occupies a line
    as an explicit sound/source description, or is a halfwidth kana reading
    after kanji. A marker the line opens with goes when it annotates the same
    source the group did.
    """
    spans = parenthesised_spans(text)
    drop: list[tuple[int, int]] = []
    for span in spans:
        start, _ = span
        body = group_body(text, span)
        before, after_line = line_context(text, spans, span)
        if (
            (body in labels and not opening_payload(before))
            or is_leading_label(text, spans, span, body)
            or (
                annotation_label(body)
                and not opening_payload(before)
                and not contextual_payload(after_line)
            )
            or ruby_reading(text, span, body)
        ):
            drop.append(span)
            opening = bare(before)
            if annotation_marker(opening):
                line_start, _ = line_bounds(text, span)
                drop += rendered_positions(text, spans, line_start, start)

    # Sorted and deduplicated: a marker precedes the group it belongs to, and two
    # groups on one line report the same marker between them.
    for start, end in sorted(set(drop), reverse=True):
        text = text[:start] + text[end:]
    return text


def tidy_lines(text: str) -> str:
    """Drop emptied lines while carrying their markup onto surviving text.

    Each surviving line is rejoined with the break it originally carried, and a
    dropped line takes its break with it, so a cue keeps whichever of `\\N`,
    `\\n` or a real newline it was written with. Only spaces and tabs are
    trimmed -- the ideographic space a provider indents with is text the strip
    did not put there, and `visible` judges a line of it empty regardless.
    """
    parts = LINE_BREAK.split(text)
    kept: list[tuple[str, str]] = []
    carry = ""
    for position in range(0, len(parts), 2):
        line = sweep_empty(parts[position].strip(" \t"))
        separator = parts[position + 1] if position + 1 < len(parts) else ""
        if visible(line):
            kept.append((sweep_empty(carry + line), separator))
            carry = ""
        else:
            # Only the markup is worth carrying; a dropped line's whitespace
            # would arrive as a stray space or carriage return on the next one.
            carry += line.strip()
    if carry and kept:
        line, separator = kept[-1]
        kept[-1] = (sweep_empty(line + carry), separator)
    return "".join(
        line + (separator if position < len(kept) - 1 else "")
        for position, (line, separator) in enumerate(kept)
    )


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


def music_marker_only(text: str) -> bool:
    """Whether a cue is only music notes and their conventional padding."""
    plain = bare(text)
    return bool(plain) and any(char in MUSIC_NOTES for char in plain) and all(
        char.isspace() or char in MUSIC_NOTES or char in MUSIC_PADDING
        for char in plain
    )
