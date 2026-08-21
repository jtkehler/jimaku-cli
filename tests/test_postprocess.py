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


def write_srt(path: Path, cues: list[str], encoding: str = "utf-8") -> None:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            f"{index}\n00:00:{index:02},000 --> 00:00:{index:02},900\n{cue}\n"
        )
    path.write_text("\n".join(blocks), encoding=encoding)


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
