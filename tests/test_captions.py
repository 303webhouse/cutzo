"""Captions and the chapter list (captions.py)."""

from pathlib import Path

import pytest

from cutzo.core import captions, render


def _w(text, s, e, sp=None):
    return {"w": text, "s": s, "e": e, "sp": sp}


def _voice(start, end, at, label="Conversation"):
    return render.Piece(src=Path("voice.f32"), channels=1, start=start, end=end, at=at, kind="voice", label=label)


def test_words_land_where_they_play_and_cut_words_never_show():
    words = [_w("Hello", 10.0, 10.4), _w("there.", 10.5, 10.9), _w("Cut", 12.0, 12.3), _w("me.", 12.4, 12.7),
             _w("Welcome", 20.0, 20.5), _w("back.", 20.6, 21.0)]
    pieces = [
        _voice(20.0, 21.2, 1.5, "Teaser"),  # the teaser repeats a later line at the very start
        render.Piece(src=Path("music.f32"), channels=2, start=0, end=30, at=0, kind="music", label="Theme"),
        _voice(10.0, 11.0, 100.0),  # the talk: 11-20 s of the recording is cut
        _voice(20.0, 21.2, 101.0),
    ]
    placed = captions.place_words(words, pieces)
    assert [w["w"] for w in placed] == ["Welcome", "back.", "Hello", "there.", "Welcome", "back."]
    assert placed[0]["s"] == pytest.approx(1.5)
    assert placed[2]["s"] == pytest.approx(100.0) and placed[3]["e"] == pytest.approx(100.9)
    assert placed[4]["s"] == pytest.approx(101.0)


def test_a_word_on_a_join_belongs_to_one_piece_only():
    words = [_w("long", 9.8, 10.4)]  # its middle (10.1) is in the second piece
    placed = captions.place_words(words, [_voice(0.0, 10.0, 0.0), _voice(10.0, 20.0, 50.0)])
    assert len(placed) == 1 and placed[0]["s"] == pytest.approx(50.0)


def test_cues_drop_fillers_but_keep_their_punctuation_and_capitals():
    words = [_w("Um,", 0.0, 0.2), _w("so", 0.3, 0.5), _w("we", 0.5, 0.6), _w("went", 0.6, 0.9), _w("uh.", 1.0, 1.2),
             _w("It", 1.3, 1.4), _w("was", 1.4, 1.6), _w("great.", 1.6, 2.0)]
    items = captions.cues(words)
    assert [c.text for c in items] == ["So we went. It was great."]
    assert items[0].start == pytest.approx(0.3)


def test_cues_break_at_pauses_speakers_and_length():
    words = [_w("One", 0.0, 0.3, "S1"), _w("two", 0.4, 0.7, "S1"), _w("three", 2.0, 2.3, "S1"),  # 1.3 s pause
             _w("Four", 2.4, 2.7, "S2")]  # someone else
    assert [c.text for c in captions.cues(words)] == ["One two", "three", "Four"]
    long = [_w(f"word{k}", k * 0.25, k * 0.25 + 0.2) for k in range(60)]
    items = captions.cues(long)
    assert all(len(c.text) <= captions.MAX_CHARS * captions.MAX_LINES for c in items)
    assert all(c.end - c.start <= captions.MAX_SECONDS + captions.LINGER for c in items)
    assert all(a.end <= b.start for a, b in zip(items, items[1:]))  # never overlapping
    assert all(max(len(line) for line in c.lines()) <= captions.MAX_CHARS for c in items)


def test_two_lines_split_near_the_middle():
    cue = captions.Cue(0, 1, "This is a fairly long caption that needs two lines to fit".split())
    first, second = cue.lines()
    assert abs(len(first) - len(second)) < 12 and len(first) <= 42 and len(second) <= 42


def test_srt_format_and_reading_it_back():
    items = [captions.Cue(1.5, 3.25, ["Hello", "there."]), captions.Cue(3661.0, 3662.5, ["Later", "on."])]
    text = captions.srt(items)
    assert text.startswith("1\n00:00:01,500 --> 00:00:03,250\nHello there.\n\n2\n01:01:01,000 --> 01:01:02,500\n")
    assert captions.read_srt(text) == [(1.5, 3.25, "Hello there."), (3661.0, 3662.5, "Later on.")]


def test_chapter_list_for_youtube():
    text = captions.chapter_list([{"time": 0.0, "title": "Opening"}, {"time": 2354.2, "title": "Second segment"},
                                  {"time": 4741.0, "title": "Third segment"}])
    assert text == "0:00 Opening\n39:14 Second segment\n1:19:01 Third segment\n"
