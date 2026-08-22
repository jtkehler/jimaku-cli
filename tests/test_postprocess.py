from __future__ import annotations

import codecs
from pathlib import Path

import pysubs2
import pytest

from jimaku_cli.strip_ih import (
    music_marker_only,
    parenthesised_spans,
    speaker_labels,
    strip_html_ruby,
    strip_ih,
    strip_parenthesised,
    tidy_lines,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("（信子）おはよう", "おはよう"),
        (r"（信子）\Nおはよう", r"\Nおはよう"),
        ("（ドアが開く音）", ""),
        ("（車のクラクション）", ""),
        ("（女性隊員・英語）", ""),
        ("（村西の鼻歌）", ""),
        ("（竜の咆哮(ほうこう)）", ""),
        ("漢字(かんじ)", "漢字"),
        (r"{\pos(320,240)}（信子）おはよう", r"{\pos(320,240)}おはよう"),
    ],
)
def test_strip_parenthesised_removes_high_confidence_annotations(
    text: str, expected: str
) -> None:
    assert strip_parenthesised(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "（今日は仕事でしょ？）",
        "（ちょっと待って…）",
        "（この音！）",
        "（うん）そうだね",
        "（はい）分かった",
        "（無理）できない",
        "(hello) world",
        "今日は（たぶん）行く",
        "私は（君の声）を聞いた",
        "あれは（風の音）だった",
        "これは（沈黙）ではない",
        "（君の声）を聞いた",
        "（風の音）だった",
        "（沈黙）ではない",
        "(hello)",
        "ALL CAPS",
        "＜心の声＞",
        "《内心のせりふ》",
        "((電話越しのせりふ))",
        "(outer(inner)",
        "（幅が合わない)",
    ],
)
def test_strip_parenthesised_preserves_ambiguous_or_dialogue_text(text: str) -> None:
    assert strip_parenthesised(text) == text


def test_parenthesised_spans_does_not_salvage_inner_pair_from_unmatched_outer() -> None:
    assert parenthesised_spans("(outer(inner)") == []


def test_parenthesised_spans_ignores_parentheses_inside_markup() -> None:
    text = r"{\pos(320,240)}（信子）おはよう"
    assert [text[start:end] for start, end in parenthesised_spans(text)] == [
        "（信子）"
    ]


def test_learned_label_is_removed_when_it_later_appears_alone() -> None:
    spoken = "（ちょっと待って…）"
    texts = ["（信子）おはよう", "（信子）", spoken]
    labels = speaker_labels(texts)

    assert labels == {"信子"}
    assert strip_parenthesised("（信子）", labels) == ""
    assert strip_parenthesised(spoken, labels) == spoken


def test_spoken_parenthetical_before_another_line_is_preserved() -> None:
    text = r"（ちょっと待って）\N今 行く"
    assert strip_parenthesised(text) == text


def test_leading_labels_are_recognized_on_each_display_line() -> None:
    text = r"（信子）hi\N（太郎）bye"
    assert strip_parenthesised(text) == r"hi\Nbye"


@pytest.mark.parametrize(
    "text",
    [
        r"(庄太さーん\N早く ごはん食べて！)",
        "（何？）",
        "（何なの？）",
        "♪（Talkin' 'bout my generation）",
        "((旅の呪いだ))",
        "｟ノイゼル：うっ…　うぅ…｠",
    ],
)
def test_corpus_dialogue_regressions_are_preserved(text: str) -> None:
    assert strip_parenthesised(text) == text


def test_known_label_does_not_hide_adjacent_parenthesised_dialogue() -> None:
    text = "（まお）（フォアグラみたいな…）"
    expected = "（フォアグラみたいな…）"
    assert strip_parenthesised(text, {"まお"}) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<ruby>和音<rt>コード</rt></ruby>", "和音"),
        ("<ruby>あ<rt>•</rt>れ<rt>•</rt></ruby>", "あれ"),
        (
            "<ruby><rb>和音</rb><rp>（</rp><rt>コード</rt><rp>）</rp></ruby>",
            "和音",
        ),
    ],
)
def test_strip_html_ruby_keeps_base_text(text: str, expected: str) -> None:
    assert strip_html_ruby(text) == expected


def test_strip_html_ruby_preserves_malformed_reading() -> None:
    text = "<ruby>和音<rt>コード</ruby>"
    assert strip_html_ruby(text) == text


def test_malformed_html_ruby_cannot_consume_a_later_container() -> None:
    text = "<ruby>A<rt>bad</ruby> dialogue <ruby>B<rt>read</rt></ruby>"
    expected = "<ruby>A<rt>bad</ruby> dialogue B"
    assert strip_html_ruby(text) == expected


@pytest.mark.parametrize("text", ["♪～", "～♪", "♬～", "<i>♪--</i>"])
def test_music_marker_only(text: str) -> None:
    assert music_marker_only(text)


@pytest.mark.parametrize("text", ["♪ 歌詞 ♪", "♥", "…", "！？"])
def test_content_is_not_a_music_marker(text: str) -> None:
    assert not music_marker_only(text)


def test_tidy_lines_keeps_markup_that_spans_a_removed_line() -> None:
    assert tidy_lines(r"<i>\Nせりふ</i>") == "<i>せりふ</i>"
    assert tidy_lines(r"せりふ\N</i>") == "せりふ</i>"


def write_srt(
    path: Path,
    cues: list[str],
    encoding: str = "utf-8",
    newline: str = "\n",
) -> None:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            f"{index}\n00:00:{index:02},000 --> 00:00:{index:02},900\n{cue}\n"
        )
    text = "\n".join(blocks)
    path.write_bytes(text.replace("\n", newline).encode(encoding))


ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def write_ass(
    path: Path,
    events: list[str],
    trailer: str = "",
    newline: str = "\n",
    encoding: str = "utf-8",
) -> None:
    text = ASS_HEADER + "".join(f"{event}\n" for event in events) + trailer
    path.write_bytes(text.replace("\n", newline).encode(encoding))


def dialogue(text: str, start: str = "0:00:01.00", end: str = "0:00:02.00") -> str:
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"


def load_texts(path: Path, encoding: str = "utf-8") -> list[str]:
    return [
        event.text
        for event in pysubs2.load(path, encoding=encoding, keep_html_tags=True)
        if event.is_text
    ]


def test_strip_ih_preserves_dialogue_lyrics_and_unrelated_symbol_cues(
    tmp_path: Path,
) -> None:
    subtitle = tmp_path / "provider.ja.srt"
    write_srt(
        subtitle,
        [
            "（信子）おはよう",
            "（今日は仕事でしょ？）",
            "（君の声）を聞いた",
            "♪ 歌詞 ♪",
            "♥",
            "♪～",
            "♪～（BGM）",
            "<i>（ドアが開く音）\nせりふ</i>",
            "“悪魔の<ruby>和音<rt>コード</rt></ruby>〟だ",
        ],
    )

    strip_ih(subtitle)

    assert load_texts(subtitle) == [
        "おはよう",
        "（今日は仕事でしょ？）",
        "（君の声）を聞いた",
        "♪ 歌詞 ♪",
        "♥",
        "<i>せりふ</i>",
        "“悪魔の和音〟だ",
    ]


def test_strip_ih_leaves_noop_file_byte_identical(tmp_path: Path) -> None:
    subtitle = tmp_path / "dialogue.srt"
    write_srt(
        subtitle,
        [
            "（今日は仕事でしょ？）",
            "♪（Talkin' 'bout my generation）",
            "ALL CAPS",
            "＜心の声＞",
            "♪ 歌詞 ♪",
        ],
    )
    before = subtitle.read_bytes()

    strip_ih(subtitle)

    assert subtitle.read_bytes() == before


def test_strip_ih_reads_and_preserves_utf16_bom(tmp_path: Path) -> None:
    subtitle = tmp_path / "utf16.srt"
    write_srt(subtitle, ["（信子）おはよう"], encoding="utf-16")

    strip_ih(subtitle)

    assert subtitle.read_bytes().startswith(codecs.BOM_UTF16_LE)
    assert load_texts(subtitle, encoding="utf-16") == ["おはよう"]


def test_strip_ih_leaves_webvtt_byte_identical(tmp_path: Path) -> None:
    subtitle = tmp_path / "settings.vtt"
    subtitle.write_text(
        "WEBVTT\n\n"
        "speaker-id\n"
        "00:00:01.000 --> 00:00:02.000 align:start position:10%\n"
        "（信子）おはよう\n\n"
        "other-id\n"
        "00:00:03.000 --> 00:00:04.000 line:10%\n"
        "せりふ\n",
        encoding="utf-8",
    )
    before = subtitle.read_bytes()

    strip_ih(subtitle)

    assert subtitle.read_bytes() == before


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # `一同` and `電子音声` name who speaks, so their wording no longer vetoes
        # learning the group as a label the way sound wording does.
        ("（一同）はい！", "はい！"),
        ("（電子音声）「起動します」", "「起動します」"),
        # `美鈴` is a girl, not the bell her name ends in.
        ("（美鈴）うん", "うん"),
        # Furigana written inside a label used to make it read as speech.
        ("(大谷敦士(おおたにあつし))おはよう", "おはよう"),
        # An honorific or a role names a person however much kana follows.
        ("（ホネちゃん）ああ", "ああ"),
        ("（殺せんせー）えー", "えー"),
        ("（社員たち）おはようございます", "おはようございます"),
    ],
)
def test_strip_parenthesised_removes_labels_that_used_to_read_as_speech(
    text: str, expected: str
) -> None:
    assert strip_parenthesised(text) == expected


@pytest.mark.parametrize(
    "text", ["（震える息）", "（終業の鐘）", "（神主の咳）", "（２人のため息）"]
)
def test_sound_wording_still_strips_where_a_name_only_resembles_it(text: str) -> None:
    assert strip_parenthesised(text) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The dash still marks two speakers once their names are gone.
        (r"-（雀）おはよう\N-（社員たち）おはようございます", r"-おはよう\N-おはようございます"),
        # An icon annotates the same source the group did, so it leaves with it.
        ("📻（レミ）はい", "はい"),
        ("≪(足音)", ""),
        # A bracket is not a marker: removing its opening half would strand `》`.
        ("《（鎖々美）ふふふ…》", "《ふふふ…》"),
        # Only the rendered marker goes; the override blocks around it stay.
        (
            r"{\pos(172,407)}📻{\fscx50}（レミ）はい",
            r"{\pos(172,407)}{\fscx50}はい",
        ),
    ],
)
def test_a_line_may_open_with_a_marker_that_is_not_dialogue(
    text: str, expected: str
) -> None:
    assert strip_parenthesised(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Bare kana names are ambiguous by shape and deliberately out of scope.
        "（しんのすけ）ほっほーい！",
        "（はるか）なんか",
        # Anything alphanumeric before the group is real text and blocks the strip.
        "Kayano (2 votes)",
        "かまじ（釜次）",
    ],
)
def test_out_of_scope_groups_are_deliberately_left_alone(text: str) -> None:
    assert strip_parenthesised(text) == text


def test_leading_label_is_stripped_without_any_learned_labels() -> None:
    """`search` and `download` share one matcher; it has to work standalone."""
    assert strip_parenthesised("（大谷）おはよう") == "おはよう"


def test_strip_ih_drops_a_cue_that_was_only_a_marker_and_a_sound(
    tmp_path: Path,
) -> None:
    subtitle = tmp_path / "marked.ja.srt"
    write_srt(subtitle, ["≪(足音)", "📻（レミ）はい", "-（雀）おはよう", "♪～（BGM）"])

    strip_ih(subtitle)

    assert load_texts(subtitle) == ["はい", "-おはよう"]


def ass_lines(path: Path, prefix: str = "Dialogue:") -> list[str]:
    text = path.read_bytes().decode("utf-8")
    return [line for line in text.split("\n") if line.startswith(prefix)]


def test_strip_ih_preserves_timestamps_past_ten_hours(tmp_path: Path) -> None:
    """pysubs2 clamped anything past 9:59:59.99 on the way through."""
    subtitle = tmp_path / "long.ass"
    write_ass(
        subtitle,
        [
            dialogue("♪♪～", "10:23:32.08", "10:23:34.12"),
            dialogue("（信子）おはよう", "10:23:35.06", "10:23:37.10"),
        ],
    )

    strip_ih(subtitle)

    assert ass_lines(subtitle) == [
        dialogue("おはよう", "10:23:35.06", "10:23:37.10")
    ]
    assert "9:59:59.99" not in subtitle.read_bytes().decode("utf-8")


def test_strip_ih_preserves_a_negative_timestamp(tmp_path: Path) -> None:
    """pysubs2 clamped a negative start to zero."""
    subtitle = tmp_path / "negative.ass"
    write_ass(
        subtitle,
        [
            dialogue("（ドアが開く音）", "-0:00:02.00", "0:00:01.00"),
            dialogue("（信子）おはよう", "-0:00:04.50", "-0:00:03.00"),
        ],
    )

    strip_ih(subtitle)

    assert ass_lines(subtitle) == [
        dialogue("おはよう", "-0:00:04.50", "-0:00:03.00")
    ]


def test_strip_ih_keeps_crlf_line_endings(tmp_path: Path) -> None:
    subtitle = tmp_path / "windows.srt"
    write_srt(subtitle, ["（信子）おはよう", "せりふ"], newline="\r\n")

    strip_ih(subtitle)

    raw = subtitle.read_bytes()
    assert "おはよう" in raw.decode("utf-8")
    assert raw.count(b"\r\n") == raw.count(b"\n") == raw.count(b"\r")


@pytest.mark.parametrize("separator", ["\\N", "\\n"])
def test_strip_ih_keeps_the_break_token_a_changed_cue_was_written_with(
    tmp_path: Path, separator: str
) -> None:
    subtitle = tmp_path / "breaks.ass"
    write_ass(
        subtitle, [dialogue(f"せりふ{separator}（ドアが開く音）{separator}次の行")]
    )

    strip_ih(subtitle)

    assert ass_lines(subtitle) == [dialogue(f"せりふ{separator}次の行")]


def test_strip_ih_keeps_a_real_newline_in_a_changed_srt_cue(tmp_path: Path) -> None:
    subtitle = tmp_path / "newline.srt"
    write_srt(subtitle, ["（信子）おはよう\nお元気ですか"])

    strip_ih(subtitle)

    assert subtitle.read_bytes().decode("utf-8").endswith("おはよう\nお元気ですか\n")


def test_strip_ih_keeps_ideographic_indentation_on_an_untouched_line(
    tmp_path: Path,
) -> None:
    """U+3000 is the provider's indentation, not space the strip left behind."""
    subtitle = tmp_path / "indented.ass"
    write_ass(subtitle, [dialogue(r"（信子）おはよう\N　インデント")])

    strip_ih(subtitle)

    assert ass_lines(subtitle) == [dialogue(r"おはよう\N　インデント")]


def test_strip_ih_preserves_sections_after_the_events(tmp_path: Path) -> None:
    extradata = "\n[Aegisub Extradata]\nData: 1,_aegi_perspective_ambient_plane,foo\n"
    subtitle = tmp_path / "extradata.ass"
    write_ass(subtitle, [dialogue("（信子）おはよう")], trailer=extradata)

    strip_ih(subtitle)

    assert subtitle.read_bytes().decode("utf-8").endswith(extradata)


def test_strip_ih_preserves_subrip_coordinate_fields(tmp_path: Path) -> None:
    times = "00:00:01,000 --> 00:00:02,900  X1:040 X2:600 Y1:460 Y2:500"
    subtitle = tmp_path / "coords.srt"
    subtitle.write_bytes(f"1\n{times}\n（信子）おはよう\n".encode())

    strip_ih(subtitle)

    assert subtitle.read_bytes().decode("utf-8") == f"1\n{times}\nおはよう\n"


def test_strip_ih_renumbers_subrip_blocks_after_a_deletion(tmp_path: Path) -> None:
    subtitle = tmp_path / "renumber.srt"
    write_srt(subtitle, ["せりふ", "（ドアが開く音）", "次の行"])

    strip_ih(subtitle)

    text = subtitle.read_bytes().decode("utf-8")
    assert [line for line in text.split("\n") if line.isdigit()] == ["1", "2"]
    assert "（ドアが開く音）" not in text


def test_strip_ih_keeps_a_tentative_title_marker(tmp_path: Path) -> None:
    """`仮` qualifies the line rather than naming who says it."""
    assert strip_parenthesised("（仮）みたいなスケジュール") == "（仮）みたいなスケジュール"

    subtitle = tmp_path / "tentative.srt"
    write_srt(subtitle, ["（仮）みたいなスケジュール"])
    before = subtitle.read_bytes()

    strip_ih(subtitle)

    assert subtitle.read_bytes() == before


def test_strip_ih_reads_a_cue_split_by_a_blank_line(tmp_path: Path) -> None:
    """A blank line inside a cue does not end it, so the text below still strips."""
    subtitle = tmp_path / "blank.srt"
    subtitle.write_bytes(
        "1\n00:00:01,000 --> 00:00:02,900\n\n（信子）おはよう\n\n"
        "2\n00:00:03,000 --> 00:00:04,900\nせりふ\n".encode()
    )

    strip_ih(subtitle)

    assert "（信子）" not in subtitle.read_bytes().decode("utf-8")


@pytest.mark.parametrize("suffix", [".vtt", ".sub"])
def test_strip_ih_leaves_unsupported_formats_byte_identical(
    tmp_path: Path, suffix: str
) -> None:
    subtitle = tmp_path / f"provider{suffix}"
    subtitle.write_bytes("\x00\x01（\uff9f binary or webvtt \x00".encode())
    before = subtitle.read_bytes()

    strip_ih(subtitle)

    assert subtitle.read_bytes() == before


def test_strip_ih_leaves_comments_and_drawings_alone(tmp_path: Path) -> None:
    comment = "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,（信子）おはよう"
    drawing = dialogue(r"{\p1}（信子）m 0 0 l 100 0 100 100{\p0}")
    subtitle = tmp_path / "notdialogue.ass"
    write_ass(subtitle, [comment, drawing, dialogue("（太郎）やあ")])

    strip_ih(subtitle)

    assert ass_lines(subtitle, "Comment:") == [comment]
    assert ass_lines(subtitle) == [drawing, dialogue("やあ")]


def test_strip_ih_preserves_a_big_endian_byte_order_mark(tmp_path: Path) -> None:
    """Python's bare `utf-16` encoder always writes little endian."""
    subtitle = tmp_path / "utf16be.srt"
    write_srt(subtitle, ["（信子）おはよう"], encoding="utf-16-be")
    subtitle.write_bytes(codecs.BOM_UTF16_BE + subtitle.read_bytes())

    strip_ih(subtitle)

    raw = subtitle.read_bytes()
    assert raw.startswith(codecs.BOM_UTF16_BE)
    assert raw[len(codecs.BOM_UTF16_BE) :].decode("utf-16-be").endswith("おはよう\n")
