"""Remove hearing-impaired annotations from subtitle files."""

import codecs
import os
import re
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

# Standalone parentheticals are only removed when their wording is clearly an
# accessibility label. False negatives are intentional: `(今日は仕事でしょ？)`
# is dialogue in the corpus, while `（ドアが開く音）` is not. Keep this list to
# stable caption vocabulary rather than trying to recognize arbitrary Japanese
# sentences as actions or sounds.
ANNOTATION_SUFFIXES = (
    "BGM",
    "ＢＧＭ",
    "あくび",
    "いななき",
    "いびき",
    "うなり声",
    "おなら",
    "げっぷ",
    "さえずり",
    "ざわめき",
    "しゃっくり",
    "くしゃみ",
    "せき",
    "せきこみ",
    "せき込み",
    "せきばらい",
    "ため息",
    "どよめき",
    "ほえ声",
    "まね",
    "アナウンス",
    "アラーム",
    "クラクション",
    "サイレン",
    "サウンド",
    "チャイム",
    "ナレーション",
    "ナレーター",
    "ノイズ",
    "ノック",
    "ブザー",
    "ファンファーレ",
    "ホイッスル",
    "ベル",
    "メロディ",
    "一同",
    "全員",
    "伴奏",
    "効果音",
    "口笛",
    "号砲",
    "吐息",
    "咆哮",
    "地響き",
    "時報",
    "声",
    "声援",
    "寝息",
    "悲鳴",
    "手拍子",
    "拍手",
    "指笛",
    "演奏",
    "爆発",
    "笑い",
    "笑い声",
    "絶叫",
    "羽ばたき",
    "舌打ち",
    "警笛",
    "警報",
    "読経",
    "通訳",
    "遠ぼえ",
    "遠吠え",
    "遠雷",
    "銃声",
    "鐘",
    "雷鳴",
    "音",
    "音楽",
    "鳴き声",
    "鳴きまね",
    "鼓動",
    "鼻息",
    "鼻歌",
    "汽笛",
    "沈黙",
    "深呼吸",
    "静寂",
    "心音",
    "息",
    "息切れ",
    "息遣い",
    "雄たけび",
    "嗚咽",
    "咳",
    "咳き込み",
    "咳払い",
    "英語",
    "日本語",
    "外国語",
    "現地語",
    "韓国語",
    "中国語",
    "台湾語",
    "ドイツ語",
    "着信",
    "バイブレーター",
    "ゴング",
    "喝采",
    "怒号",
    "鈴",
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


def strip_ih(subtitle: Path) -> None:
    """Remove hearing-impaired annotations and furigana from a subtitle.

    Drops high-confidence speaker labels, sound effects, ruby readings and bare
    music markers. Ambiguous parenthesised text is dialogue and stays. Replaces
    supported formats in place; WebVTT is left unchanged.
    """
    # pysubs2 does not preserve WebVTT cue identifiers or settings. A silent
    # no-op is safer than corrupting a downloaded subtitle.
    if subtitle.suffix.casefold() == ".vtt":
        return

    # SRT is the only format whose tags pysubs2 rewrites on the way through:
    # it converts `<i>` to `{\i1}` on load and drops `{...}` on save. The pair
    # of flags is the best available tag round-trip. Every format takes both as
    # **kwargs, so no branching is needed.
    encoding = subtitle_encoding(subtitle)
    subs = pysubs2.load(subtitle, encoding=encoding, keep_html_tags=True)

    before = [event.text for event in subs]
    labels = speaker_labels(
        strip_html_ruby(event.text) for event in subs if event.is_text
    )
    changed: set[int] = set()
    for event in subs:
        # Comment lines and {\p1} drawings are not dialogue; leave them alone.
        if event.is_text:
            original = event.text
            without_ruby = strip_html_ruby(original)
            stripped = strip_parenthesised(without_ruby, labels)
            if music_marker_only(stripped):
                stripped = ""
            elif stripped != without_ruby:
                stripped = tidy_lines(stripped)
            if stripped != original:
                event.text = stripped
            if event.text != original:
                changed.add(id(event))
    kept = [
        event
        for event in subs
        if not (event.is_text and id(event) in changed and not is_dialogue(event))
    ]

    # Nothing to strip: leave the file alone rather than round-trip it through
    # pysubs2, which reformats margins and stamps its own header comment.
    if [event.text for event in kept] == before:
        return
    subs.events = kept

    stripped = subtitle.with_suffix(".stripih" + subtitle.suffix)
    try:
        subs.save(stripped, encoding=encoding, keep_ssa_tags=True)
        os.replace(stripped, subtitle)
    finally:
        stripped.unlink(missing_ok=True)


def subtitle_encoding(subtitle: Path) -> str:
    """Return a BOM-aware encoding without guessing legacy byte encodings."""
    with subtitle.open("rb") as file:
        prefix = file.read(4)
    if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if prefix.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    return "utf-8"


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


def line_context(
    text: str, spans: Collection[tuple[int, int]], span: tuple[int, int]
) -> tuple[str, str]:
    """Visible-line text before and after a parenthetical span."""
    start, end = span
    line_start = 0
    for line_break in LINE_BREAK.finditer(text, 0, start):
        line_start = line_break.end()
    next_break = LINE_BREAK.search(text, end)
    line_end = next_break.start() if next_break else len(text)
    return (
        outside_text(text, spans, line_start, start),
        outside_text(text, spans, end, line_end),
    )


def contextual_payload(text: str) -> bool:
    """Whether context contains more than markup or a bare music marker."""
    return visible(text) and not music_marker_only(text)


def looks_spoken(body: str) -> bool:
    """Whether a parenthetical plausibly contains speech rather than a label."""
    if body in SPOKEN_WORDS or re.search(r"[A-Za-z]", body):
        return True
    if any(char in SPOKEN_PUNCTUATION for char in body):
        return True
    if SPOKEN_ENDING.search(body):
        return True
    letters = [char for char in body if char.isalpha()]
    return len(letters) >= 5 and len(HIRAGANA.findall(body)) / len(letters) >= 0.6


def annotation_label(body: str) -> bool:
    """Whether a standalone group ends in established sound/source wording."""
    # Ignore nested halfwidth ruby when checking `（竜の咆哮(ほうこう)）`.
    without_ruby = re.sub(r"\([ぁ-ゟァ-ヿー～〜・･\s]+\)", "", body)
    normalized = without_ruby.rstrip()
    return normalized.endswith(ANNOTATION_SUFFIXES)


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
            start, end = span
            before, _ = line_context(text, spans, span)
            after = outside_text(text, spans, end, len(text))
            body = group_body(text, span)
            if (
                not contextual_payload(before)
                and contextual_payload(after)
                and body
                and not looks_spoken(body)
                and not annotation_label(body)
            ):
                labels.add(body)
    return labels


def strip_parenthesised(text: str, labels: Collection[str] = ()) -> str:
    """Remove only high-confidence parenthetical annotations and ruby.

    Groups nest and markup is opaque. Strict matching means malformed or
    unmatched punctuation survives whole, while ARIB's top-level `((...))`
    voice marker is never treated as an annotation. A group is removed when it
    is a known leading label, clearly precedes visible dialogue, occupies a line
    as an explicit sound/source description, or is a halfwidth kana reading
    after kanji.
    """
    spans = parenthesised_spans(text)
    drop: list[tuple[int, int]] = []
    for span in spans:
        start, end = span
        body = group_body(text, span)
        before, after_line = line_context(text, spans, span)
        after = outside_text(text, spans, end, len(text))
        explicit_annotation = annotation_label(body)
        leading_label = (
            not contextual_payload(before)
            and contextual_payload(after)
            and body
            and not looks_spoken(body)
            and not explicit_annotation
        )
        if (
            (body in labels and not contextual_payload(before))
            or leading_label
            or (
                explicit_annotation
                and not contextual_payload(before)
                and not contextual_payload(after_line)
            )
            or ruby_reading(text, span, body)
        ):
            drop.append(span)

    for start, end in reversed(drop):
        text = text[:start] + text[end:]
    return text


def tidy_lines(text: str) -> str:
    """Drop emptied lines while carrying their markup onto surviving text."""
    kept: list[str] = []
    carry = ""
    for raw_line in LINE_BREAK.split(text):
        line = sweep_empty(raw_line.strip())
        if visible(line):
            kept.append(sweep_empty(carry + line))
            carry = ""
        else:
            carry += line
    if carry and kept:
        kept[-1] = sweep_empty(kept[-1] + carry)
    return r"\N".join(kept)


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


def is_dialogue(event: SSAEvent) -> bool:
    """Whether a cue still renders any payload after a high-confidence strip."""
    return visible(event.text)
