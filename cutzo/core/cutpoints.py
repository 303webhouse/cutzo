"""Where exactly to cut when words come out of the conversation.

Speech-to-text word times can be a couple of tenths of a second off. Cutting exactly there
can clip the end of the word before or leave a breath of the cut word. So each edge of a
word cut moves to the quietest moment (10 ms steps) in the gap between the words, searched a
little past the gap on both sides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import audio

REACH = 0.10  # seconds searched beyond each edge of the gap between two words
LEAD = 0.6  # at the very start or end of the transcript, how far past the word to look


def quietest(pcm: audio.Pcm, a: float, b: float) -> float:
    """The quietest 10 ms moment between a and b (seconds), as the middle of that moment."""
    a, b = max(0.0, a), min(pcm.duration, b)
    if b - a < 0.02:
        return (a + b) / 2
    env = audio.envelope(pcm, hop=audio.HOP, start=a, end=b)
    if not len(env):
        return (a + b) / 2
    # Ties (true silence) go to the middle of the quiet stretch, not its first frame.
    low = np.flatnonzero(env <= env.min() + 0.5)
    k = int(low[len(low) // 2])
    return round(a + (k + 0.5) * audio.HOP, 3)


def word_cut(voice: Path, words: list[dict[str, Any]], i: int, j: int, duration: float) -> tuple[float, float]:
    """Cut points for removing words[i:j] (j exclusive), in recording time."""
    s, e = float(words[i]["s"]), float(words[j - 1]["e"])
    pcm = audio.Pcm(voice)
    if not voice.exists() or pcm.frames == 0:
        return max(0.0, s - 0.02), min(duration, e + 0.04)
    prev_end = float(words[i - 1]["e"]) if i > 0 else max(0.0, s - LEAD)
    next_start = float(words[j]["s"]) if j < len(words) else min(duration, e + LEAD)
    a = quietest(pcm, min(prev_end, s) - REACH, s + REACH)
    b = quietest(pcm, e - REACH, max(next_start, e) + REACH)
    if b - a < 0.05:  # the search collapsed: fall back to the word times
        a, b = max(0.0, s - 0.02), min(duration, e + 0.04)
    return round(max(0.0, a), 3), round(min(duration, b), 3)


def words_in(words: list[dict[str, Any]], a: float, b: float) -> tuple[int, int] | None:
    """The run of words whose middles fall between a and b, as (first index, index after last)."""
    inside = [k for k, w in enumerate(words) if a <= (float(w["s"]) + float(w["e"])) / 2 <= b]
    if not inside:
        return None
    return inside[0], inside[-1] + 1
