"""Times, file names and the cut arithmetic."""

import pytest

from cutzo.core import CutzoError
from cutzo.core import timeline
from cutzo.core.media import classify, streamyard_offset
from cutzo.core.timecode import fmt_duration, fmt_time, parse_time
from cutzo.core.transcript import find_phrase, tokens


@pytest.mark.parametrize("text, seconds", [
    ("12:03", 723), ("1:02:45.5", 3765.5), ("75", 75), (75.25, 75.25), ("90s", 90), ("2m10s", 130),
    ("1h2m3s", 3723), ("0:09,5", 9.5), (" 4:00 ", 240),
])
def test_parse_time(text, seconds):
    assert parse_time(text) == pytest.approx(seconds)


@pytest.mark.parametrize("bad", ["", "abc", "1::2", "-3", "5ms", None, True, "1:2:3:4"])
def test_parse_time_rejects(bad):
    with pytest.raises(CutzoError):
        parse_time(bad)


def test_format_times():
    assert fmt_time(723) == "12:03"
    assert fmt_time(3765.5) == "1:02:45"
    assert fmt_time(9.96, precise=True) == "0:10.0"
    assert fmt_duration(45) == "45 s"
    assert fmt_duration(200) == "3 min 20 s"
    assert fmt_duration(4320) == "1 h 12 min"


def test_streamyard_names():
    name = r"G:\My Drive\TNH\TNH_Episode_1-Nick_Baren-webcam-00h_00m_00s_571ms-StreamYard.wav"
    info = classify(name)
    assert info["name"] == "TNH_Episode_1-Nick_Baren-webcam-00h_00m_00s_571ms-StreamYard.wav"
    assert info["source"] == "streamyard-track"
    assert info["person"] == "Nick Baren"
    assert info["offset"] == pytest.approx(0.571)
    assert streamyard_offset("x-01h_02m_03s_004ms-StreamYard.wav") == pytest.approx(3723.004)
    assert classify("P1000123.MOV")["source"] == "gh5"
    assert classify("Spooky Sode Raw.mp3")["source"] == "mix"


def test_cut_arithmetic():
    cuts = timeline.merge([(10, 12), (11, 15), (20, 21), (30, 29)])
    assert cuts == [(10, 15), (20, 21)]
    assert timeline.kept(40, cuts) == [(0, 10), (15, 20), (21, 40)]
    assert timeline.to_output(5, cuts) == 5
    assert timeline.to_output(13, cuts) == 10  # inside a cut snaps to the cut point
    assert timeline.to_output(25, cuts) == 19


def test_phrase_search_ignores_case_and_punctuation():
    words = [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate("Of course it won't. They never do, of course.".split())]
    assert tokens("Won\u2019t!") == ["won't"]
    assert find_phrase(words, "of COURSE") == [(0, 2), (7, 9)]
    assert find_phrase(words, "it won't") == [(2, 4)]
    assert find_phrase(words, "") == []
