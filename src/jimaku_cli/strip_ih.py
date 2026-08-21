"""Remove hearing-impaired annotations from subtitle files."""

import codecs
import os
import re
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
