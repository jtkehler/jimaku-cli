"""Remove hearing-impaired annotations from subtitle files."""

import codecs
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Collection, Iterable
from pathlib import Path

import pysubs2
from pysubs2 import SSAEvent

__all__ = ["strip_ih"]

# Japanese SDH uses fullwidth parentheses for speaker IDs and sound effects, but
# Netflix also prescribes the same delimiters for whispered or mouthed dialogue.
# Parentheses therefore identify a *candidate*, not something that is safe to
# delete by themselves.
PAREN_PAIRS = {"（": "）", "(": ")"}
PAREN_CLOSE = frozenset(PAREN_PAIRS.values())

# Broadcast subtitles use top-level `((...))` for off-screen speech. Inside
# another group, the doubled delimiters still participate in normal nesting.
VOICE_MARKER = "()"

# Keep each display-line separator so tidying cannot change hard and soft breaks.
LINE_BREAK = re.compile(r"(\\N|\\n|\r\n|\n|\r)")

# SRT HTML and SubStation overrides are markup, not spoken text.
MARKUP = re.compile(SSAEvent.OVERRIDE_SEQUENCE.pattern + r"|</?[a-zA-Z][^>]*>")

# Match one container at a time so malformed ruby cannot consume a later one.
RUBY_ELEMENT = re.compile(
    r"<ruby\b[^>]*>(?:(?!</?ruby\b).)*?</ruby\s*>",
    re.IGNORECASE | re.DOTALL,
)
RUBY_READING = re.compile(r"<(rt|rp)\b[^>]*>[^<]*</\1\s*>", re.IGNORECASE)
RUBY_PART = re.compile(r"</?(?:rt|rp)\b", re.IGNORECASE)
RUBY_CONTAINER = re.compile(r"</?(?:ruby|rb)\b[^>]*>", re.IGNORECASE)

# Remove formatting pairs left empty by a stripped annotation.
EMPTY_PAIR = re.compile(
    r"\{\\(\w)1\}\s*\{\\\1 ?0\}"
    r"|<([a-zA-Z]+)[^>]*>\s*</\2>"
)

# Keep the note list narrow so unrelated symbols are not treated as IH.
MUSIC_PADDING = "~～〜ー-–—―‐"
MUSIC_NOTES = "♪♫♬♩"

# Source icons before a label leave with it. Dashes, bullets, quotation marks
# and music notes remain because they can carry dialogue structure or content.
ANNOTATION_MARKS = frozenset("≪≫∈＼")
MARK_CATEGORIES = frozenset(("So", "Co", "Cf"))

# Standalone groups require known accessibility vocabulary; ambiguous wording
# stays. `ベル` may end either a sound or a name, so context decides whether it
# vetoes a leading label. `鈴` is sound wording only as a whole word, protecting
# names such as `美鈴`.
AMBIGUOUS_SOUND_TAILS = ("ベル",)
SPEAKER_SUFFIXES = (
    "一同",
    "全員",
    "ナレーション",
    "ナレーター",
    "アナウンス",
    "通訳",
    "電子音声",
    # These name an off-screen speaker or audio source despite ending in 声.
    "の声",
    "の音声",
    "英語",
    "日本語",
    "外国語",
    "現地語",
    "韓国語",
    "中国語",
    "台湾語",
    "ドイツ語",
)

# Caption qualifiers that must not be learned as speaker labels.
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
# A trailing Latin letter distinguishes role IDs, but all-Latin names stay.
SPEAKER_ID = re.compile(r"[^A-Za-zＡ-Ｚａ-ｚ][A-Za-zＡ-Ｚａ-ｚ]$")
RUBY = re.compile(r"[ぁ-ゟァ-ヿー～〜・･\s]+")
HAN_AT_END = re.compile(r"[㐀-鿿々〆ヵヶ]$")
# Ignore nested furigana when classifying its outer label.
NESTED_RUBY = re.compile(r"\(" + RUBY.pattern + r"\)")
# A leading group before object particle を belongs to the sentence, not a label.
OBJECT_PARTICLE = "を"
NAME_MARKERS = re.compile(
    r"(?:ちゃん|さん|くん|君|さま|様|先生|せんせー|せんせい|たち|達)$"
)


def strip_ih(subtitle: Path) -> None:
    """Remove hearing-impaired annotations and ruby readings from a subtitle.

    Drops high-confidence speaker labels, sound effects, bare music markers,
    parenthesised kana readings after kanji and HTML ruby. Ambiguous
    parenthesised text is dialogue and stays. SRT, ASS and SSA are supported;
    every other format is left alone.
    """
    formats = {".ass": "ass", ".ssa": "ssa", ".srt": "srt"}
    format_ = formats.get(subtitle.suffix.casefold())
    if format_ is None:
        return

    encoding = subtitle_encoding(subtitle)
    subs = pysubs2.load(
        subtitle,
        encoding=encoding,
        format_=format_,
        keep_html_tags=True,
    )
    # The SRT writer omits drawing events. Leave such an unusual file whole.
    if format_ == "srt" and any(event.is_drawing for event in subs):
        return

    labels = speaker_labels(
        strip_html_ruby(event.text) for event in subs if event.is_text
    )
    changed = False
    kept: list[SSAEvent] = []
    for event in subs:
        # Comments and SubStation drawings are not dialogue.
        if not event.is_text:
            kept.append(event)
            continue

        original = event.text
        without_ruby = strip_html_ruby(original)
        stripped = strip_parenthesised(without_ruby, labels)
        if music_marker_only(stripped):
            stripped = ""
        elif stripped != without_ruby:
            stripped = tidy_lines(stripped, without_ruby)

        if stripped != original:
            event.text = stripped
            changed = True
        if stripped == original or visible(stripped):
            kept.append(event)

    # Avoid normalizing files that did not need any stripping.
    if not changed:
        return

    ensure_serializable_timestamps(kept, format_)
    subs.events = kept
    temporary = temporary_path(subtitle, ".stripih")
    try:
        subs.save(
            temporary,
            encoding=encoding,
            format_=format_,
            keep_ssa_tags=True,
        )
        shutil.copymode(subtitle, temporary)
        os.replace(temporary, subtitle)
    finally:
        temporary.unlink(missing_ok=True)


def subtitle_encoding(subtitle: Path) -> str:
    """Select a Unicode codec from a BOM, defaulting to UTF-8."""
    with subtitle.open("rb") as file:
        prefix = file.read(4)
    if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return "utf-8-sig" if prefix.startswith(codecs.BOM_UTF8) else "utf-8"


def ensure_serializable_timestamps(events: Iterable[SSAEvent], format_: str) -> None:
    """Refuse a rewrite when pysubs2 would clamp a cue timestamp."""
    maximum = 35_999_990 if format_ in {"ass", "ssa"} else 359_999_999
    if any(
        timestamp < 0 or timestamp > maximum
        for event in events
        for timestamp in (event.start, event.end)
    ):
        raise ValueError(f"{format_.upper()} timestamp is outside pysubs2's range")


def temporary_path(subtitle: Path, marker: str) -> Path:
    """Reserve a unique sibling temporary with the subtitle's format suffix."""
    prefix = f".{marker.strip('.')}-"
    with tempfile.NamedTemporaryFile(
        dir=subtitle.parent,
        prefix=prefix,
        suffix=subtitle.suffix.casefold(),
        delete=False,
    ) as file:
        return Path(file.name)


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
        # Top-level doubled halfwidth delimiters mark speech, not annotations.
        if not stack and char in VOICE_MARKER and text[index : index + 2] == char * 2:
            index += 2
            continue
        if char in PAREN_PAIRS:
            stack.append((PAREN_PAIRS[char], index))
        elif char in PAREN_CLOSE and stack:
            expected, start = stack[-1]
            if char != expected:
                # A malformed outer group is ambiguous; preserve it whole.
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
        or (unicodedata.category(char) in MARK_CATEGORIES and char not in MUSIC_NOTES)
        or char.isspace()
        for char in opening
    )


def rendered_positions(
    text: str, spans: Collection[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    """Return rendered-character spans without swallowing surrounding markup."""
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
    """Whether a group names the speaker of dialogue that follows it."""
    before, _ = line_context(text, spans, span)
    after = outside_text(text, spans, span[1], len(text))
    return (
        not opening_payload(before)
        and contextual_payload(after)
        and not bare(after).startswith(OBJECT_PARTICLE)
        and bool(body)
        and body not in NON_LABELS
        and not looks_spoken(body)
        and not vetoes_label(body)
    )


def vetoes_label(body: str) -> bool:
    """Whether sound wording rules a group out as the speaker of what follows."""
    return sound_annotation(body) and not without_nested_ruby(body).rstrip().endswith(
        AMBIGUOUS_SOUND_TAILS
    )


def looks_spoken(body: str) -> bool:
    """Whether a parenthetical plausibly contains speech rather than a label."""
    body = without_nested_ruby(body)
    if body in SPOKEN_WORDS:
        return True
    if any(char in SPOKEN_PUNCTUATION for char in body):
        return True
    # Check role IDs before treating Latin letters as evidence of speech.
    if SPEAKER_ID.search(body):
        return False
    if re.search(r"[A-Za-z]", body):
        return True
    # Punctuation still wins when an honorific appears in spoken dialogue.
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
    """Whether a halfwidth group is a kana reading immediately after kanji.

    Fullwidth groups remain ambiguous because captions also use them for spoken
    asides; stripping only halfwidth readings is deliberately conservative.
    """
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
    unmatched punctuation survives whole, while top-level `((...))` is never
    treated as an annotation. A group is removed when it
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

    # A marker and multiple groups can report the same span.
    for start, end in sorted(set(drop), reverse=True):
        text = text[:start] + text[end:]
    return text


def tidy_lines(text: str, original: str) -> str:
    """Drop emptied lines while carrying their markup onto surviving text.

    Preserve each separator and the spacing of lines the strip did not change.
    """
    parts = LINE_BREAK.split(text)
    before = LINE_BREAK.split(original)
    # Removals normally stay within one line; guard alignment before comparing.
    aligned = len(before) == len(parts)
    kept: list[tuple[str, str]] = []
    carry = ""
    for position in range(0, len(parts), 2):
        raw = parts[position]
        untouched = aligned and raw == before[position]
        line = raw if untouched else sweep_empty(raw.strip(" \t"))
        separator = parts[position + 1] if position + 1 < len(parts) else ""
        if visible(line):
            kept.append((sweep_empty(carry + line), separator))
            carry = ""
        else:
            # Carry markup, not whitespace from the removed line.
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
    return (
        bool(plain)
        and any(char in MUSIC_NOTES for char in plain)
        and all(
            char.isspace() or char in MUSIC_NOTES or char in MUSIC_PADDING
            for char in plain
        )
    )
