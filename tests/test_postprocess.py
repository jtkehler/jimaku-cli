from __future__ import annotations

import os
from pathlib import Path

import pysubs2
import pytest

from jimaku_cli.postprocess import AlignError, sync_subtitle
from jimaku_cli.strip_ih import (
    music_marker_only,
    parenthesised_spans,
    speaker_labels,
    strip_html_ruby,
    strip_ih,
    strip_parenthesised,
    temporary_path,
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
        # Names who speaks, exactly as （電子音声） does, though it ends in 声.
        ("（リサの声）おはよう", "おはよう"),
        ("（テレビの音声）ニュースです", "ニュースです"),
        # A name that ends in the ベル it was listed as a doorbell for.
        ("（アベル）おはよう", "おはよう"),
        # A trailing letter tells one role from another; it is not speech.
        ("（店員A）いらっしゃいませ", "いらっしゃいませ"),
        ("（いじめっ子Ｂ）よお", "よお"),
        # Whole-word, so ベル is still the doorbell it was listed for.
        ("（ベル）", ""),
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
        # All latin, so no trailing letter singles a role out of several.
        "(NORI)おはよう",
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
    assert [text[start:end] for start, end in parenthesised_spans(text)] == ["（信子）"]


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
        "♪ （ヘンなの）",
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
    assert tidy_lines(r"<i>\Nせりふ</i>", r"<i>（信子）\Nせりふ</i>") == "<i>せりふ</i>"
    assert tidy_lines(r"せりふ\N</i>", r"せりふ\N（ドアの音）</i>") == "せりふ</i>"


def test_tidy_lines_leaves_the_spacing_of_lines_it_did_not_change() -> None:
    # The line that lost its label is tidied and goes; the line below it never
    # had a group on it, so the space it opens with is the provider's and stays.
    assert tidy_lines(r" \N おはよう", r" （信子）\N おはよう") == " おはよう"


def write_srt(
    path: Path,
    cues: list[str],
    encoding: str = "utf-8",
) -> None:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            f"{index}\n00:00:{index:02},000 --> 00:00:{index:02},900\n{cue}\n"
        )
    text = "\n".join(blocks)
    path.write_text(text, encoding=encoding)


ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)

SSA_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00\n"
    "\n"
    "[Events]\n"
    "Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def write_ass(
    path: Path,
    events: list[str],
) -> None:
    path.write_text(ASS_HEADER + "".join(f"{event}\n" for event in events))


def dialogue(text: str, start: str = "0:00:01.00", end: str = "0:00:02.00") -> str:
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"


def ssa_dialogue(text: str) -> str:
    return f"Dialogue: Marked=0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{text}"


def load_texts(path: Path, encoding: str = "utf-8-sig") -> list[str]:
    return [
        event.text
        for event in pysubs2.load(
            path,
            encoding=encoding,
            format_=path.suffix.removeprefix("."),
            keep_html_tags=True,
        )
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
            "（信子）",
            "（今日は仕事でしょ？）",
            "（君の声）を聞いた",
            "♪ 歌詞 ♪",
            "♥",
            "♪～",
            "♪～（BGM）",
            "<i>（ドアが開く音）\nせりふ</i>",
            r"{\an8}<i>（信子）おはよう</i>",
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
        r"{\an8}<i>おはよう</i>",
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
    assert not list(tmp_path.glob(".stripih-*"))


def test_strip_ih_parse_failure_leaves_the_file_untouched(tmp_path: Path) -> None:
    subtitle = tmp_path / "invalid.srt"
    subtitle.write_bytes(b"\x80not UTF-8")
    before = subtitle.read_bytes()

    with pytest.raises(UnicodeDecodeError):
        strip_ih(subtitle)

    assert subtitle.read_bytes() == before


def test_strip_ih_handles_bom_marked_utf16(tmp_path: Path) -> None:
    subtitle = tmp_path / "utf16.srt"
    write_srt(subtitle, ["（信子）おはよう"], encoding="utf-16")

    strip_ih(subtitle)

    assert load_texts(subtitle, encoding="utf-16") == ["おはよう"]


def test_strip_ih_replaces_from_a_sibling_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subtitle = tmp_path / "dialogue.srt"
    write_srt(subtitle, ["（信子）おはよう"])
    subtitle.chmod(0o640)
    replacements: list[tuple[Path, Path]] = []
    replace = os.replace

    def record(source: Path, destination: Path) -> None:
        replacements.append((Path(source), Path(destination)))
        replace(source, destination)

    monkeypatch.setattr("jimaku_cli.strip_ih.os.replace", record)

    strip_ih(subtitle)

    assert len(replacements) == 1
    source, destination = replacements[0]
    assert source.parent == destination.parent == subtitle.parent
    assert source.suffix == subtitle.suffix
    assert destination == subtitle
    assert subtitle.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("suffix", [".ass", ".ssa"])
def test_strip_ih_handles_substation_formats(tmp_path: Path, suffix: str) -> None:
    subtitle = tmp_path / f"dialogue{suffix}"
    if suffix == ".ass":
        write_ass(subtitle, [dialogue("（信子）おはよう"), dialogue("（ベル）")])
    else:
        subtitle.write_text(
            SSA_HEADER
            + ssa_dialogue("（信子）おはよう")
            + "\n"
            + ssa_dialogue("（ベル）")
            + "\n"
        )

    strip_ih(subtitle)

    assert load_texts(subtitle) == ["おはよう"]


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
        (
            r"-（雀）おはよう\N-（社員たち）おはようございます",
            r"-おはよう\N-おはようございます",
        ),
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


@pytest.mark.parametrize(
    ("start", "end"),
    [("10:23:35.06", "10:23:37.10"), ("-0:00:04.50", "-0:00:03.00")],
)
def test_strip_ih_refuses_timestamps_pysubs2_would_clamp(
    tmp_path: Path, start: str, end: str
) -> None:
    subtitle = tmp_path / "unrepresentable.ass"
    write_ass(subtitle, [dialogue("（信子）おはよう", start, end)])
    before = subtitle.read_bytes()

    with pytest.raises(ValueError, match="outside pysubs2's range"):
        strip_ih(subtitle)

    assert subtitle.read_bytes() == before


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

    assert load_texts(subtitle) == [r"おはよう\Nお元気ですか"]


def test_strip_ih_keeps_ideographic_indentation_on_an_untouched_line(
    tmp_path: Path,
) -> None:
    """U+3000 is the provider's indentation, not space the strip left behind."""
    subtitle = tmp_path / "indented.ass"
    write_ass(subtitle, [dialogue(r"（信子）おはよう\N　インデント")])

    strip_ih(subtitle)

    assert ass_lines(subtitle) == [dialogue(r"おはよう\N　インデント")]


def test_strip_ih_keeps_a_tentative_title_marker(tmp_path: Path) -> None:
    """`仮` qualifies the line rather than naming who says it."""
    assert (
        strip_parenthesised("（仮）みたいなスケジュール")
        == "（仮）みたいなスケジュール"
    )

    subtitle = tmp_path / "tentative.srt"
    write_srt(subtitle, ["（仮）みたいなスケジュール"])
    before = subtitle.read_bytes()

    strip_ih(subtitle)

    assert subtitle.read_bytes() == before


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


def test_strip_ih_safely_noops_an_srt_containing_a_drawing(tmp_path: Path) -> None:
    drawing = r"{\p1}（ドアが開く音）{\p0}"
    subtitle = tmp_path / "drawing.srt"
    write_srt(subtitle, [drawing, "（信子）おはよう"])
    before = subtitle.read_bytes()

    strip_ih(subtitle)

    assert subtitle.read_bytes() == before


def test_strip_ih_writes_a_name_that_leaves_no_room_for_the_marker(
    tmp_path: Path,
) -> None:
    subtitle = tmp_path / ("あ" * 82 + ".srt")
    assert len(subtitle.name.encode()) == 250
    write_srt(subtitle, ["（信子）おはよう"])

    strip_ih(subtitle)

    assert load_texts(subtitle) == ["おはよう"]
    assert not list(tmp_path.glob(".stripih-*"))


@pytest.mark.parametrize("marker", [".stripih", ".ffsubsync"])
def test_temporary_path_reserves_unique_siblings(tmp_path: Path, marker: str) -> None:
    subtitle = tmp_path / ("あ" * 82 + ".SRT")
    first = temporary_path(subtitle, marker)
    second = temporary_path(subtitle, marker)
    try:
        assert first != second
        assert first.parent == second.parent == subtitle.parent
        assert first.suffix == second.suffix == subtitle.suffix.casefold()
        assert first.exists() and second.exists()
        assert all(len(os.fsencode(path.name)) <= 255 for path in (first, second))
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)


class StubParser:
    def parse_args(self, arguments: list[str]) -> Path:
        return Path(arguments[arguments.index("-o") + 1])


def test_sync_subtitle_replaces_from_a_written_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subtitle = tmp_path / "dialogue.SRT"
    subtitle.write_text("original")
    subtitle.chmod(0o640)
    video = tmp_path / "video.mkv"
    video.touch()

    monkeypatch.setattr("jimaku_cli.postprocess.make_parser", StubParser)

    def run(output: Path) -> dict[str, bool]:
        assert output.suffix == ".srt"
        output.write_text("synced")
        return {"sync_was_successful": True}

    monkeypatch.setattr("jimaku_cli.postprocess.ffsubsync.run", run)

    sync_subtitle(subtitle, video)

    assert subtitle.read_text() == "synced"
    assert subtitle.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".ffsubsync-*"))


def test_sync_subtitle_does_not_replace_with_an_empty_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subtitle = tmp_path / "dialogue.srt"
    subtitle.write_text("original")
    video = tmp_path / "video.mkv"
    video.touch()
    monkeypatch.setattr("jimaku_cli.postprocess.make_parser", StubParser)
    monkeypatch.setattr(
        "jimaku_cli.postprocess.ffsubsync.run",
        lambda _args: {"sync_was_successful": True},
    )

    with pytest.raises(AlignError):
        sync_subtitle(subtitle, video)

    assert subtitle.read_text() == "original"
    assert not list(tmp_path.glob(".ffsubsync-*"))


def test_sync_subtitle_cleans_up_when_argument_parsing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subtitle = tmp_path / "dialogue.srt"
    subtitle.write_text("original")
    video = tmp_path / "video.mkv"
    video.touch()

    class BrokenParser:
        def parse_args(self, _arguments: list[str]) -> None:
            raise RuntimeError("bad arguments")

    monkeypatch.setattr("jimaku_cli.postprocess.make_parser", BrokenParser)

    with pytest.raises(RuntimeError, match="bad arguments"):
        sync_subtitle(subtitle, video)

    assert subtitle.read_text() == "original"
    assert not list(tmp_path.glob(".ffsubsync-*"))
