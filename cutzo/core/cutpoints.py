"""Where exactly to cut when words come out of the conversation.

Speech-to-text word times are good to about a tenth of a second (Parakeet works in 80 ms steps),
and a short "um" is often given less time than it really takes. Cutting exactly at those times
could clip the word before, or leave half of an "um". So both edges of a word cut are chosen
together, on the loudness outline of the cleaned voices (10 ms steps):

- each edge goes to the quietest moment near the gap between a kept word and a cut one, searched
  REACH beyond the gap, with moments outside the gap counting as SLOPE dB per second louder;
- the stretch taken out must hold real sound near the cut words. Without this, when an "um"
  starts a run of talk ("uh, state of the NBA"), both edges could land in the silence before it
  and the cut would take nothing out;
- inside a stretch of equal quiet, an edge goes to its middle, so some pause stays on each side.

Tuned on every "um" and "uh" in ten minutes of Season Debrief Part 2 (Oct 15, 2026), looking at
each one's loudness outline: the old rule (quietest moment within 0.1 s of each word edge) missed
the "um" entirely in 4 of the first 12; this one took out all 12.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import audio

REACH = 0.12  # seconds searched beyond each gap between a kept word and a cut one
SLOPE = 20.0  # dB added for each second outside the gap
NEED = 0.4  # the cut must hold sound for this share of the cut words' length...
NEED_MIN, NEED_MAX = 0.05, 0.25  # ...but at least 50 ms and at most 250 ms of it
NEAR = 0.15  # ...counting only sound within this many seconds of the cut words
LONG = 3.0  # cut words longer than this: choose the two edges separately (no need to check)
CONTEXT = 15.0  # seconds of sound either side used to judge what counts as quiet here
LEAD = 0.6  # at the very start or end of the transcript, how far past the word to look


def word_cut(voice: Path, words: list[dict[str, Any]], i: int, j: int, duration: float) -> tuple[float, float]:
    """Cut points for removing words[i:j] (j exclusive), in recording time."""
    s, e = float(words[i]["s"]), float(words[j - 1]["e"])
    fallback = (round(max(0.0, s - 0.02), 3), round(min(duration, e + 0.04), 3))
    if not voice.exists():
        return fallback
    pcm = audio.Pcm(voice)
    if pcm.frames == 0:
        return fallback
    prev_end = float(words[i - 1]["e"]) if i > 0 else max(0.0, s - LEAD)
    next_start = float(words[j]["s"]) if j < len(words) else min(duration, e + LEAD)
    before = (min(prev_end, s), s)  # the gap between the last kept word and the first cut one
    after = (e, max(next_start, e))  # ...and between the last cut word and the next kept one
    lo, hi = max(0.0, before[0] - REACH), min(pcm.duration, after[1] + REACH)
    if hi - lo < 0.05:
        return fallback
    around = audio.envelope(pcm, start=max(0.0, lo - CONTEXT), end=min(pcm.duration, hi + CONTEXT))
    quiet = audio.suggest_threshold(around) if len(around) else -60.0
    if e - s > LONG:
        a = _edge(pcm, before, quiet)
        b = _edge(pcm, after, quiet)
    else:
        a, b = _both_edges(pcm, lo, hi, before, after, (s, e), quiet)
    if b - a < 0.05:  # the search collapsed: fall back to the word times
        return fallback
    return round(max(0.0, a), 3), round(min(duration, b), 3)


def _cost(env: np.ndarray, t: np.ndarray, gap: tuple[float, float]) -> np.ndarray:
    """How bad each moment is as a cut edge: its loudness, plus SLOPE dB per second outside the
    gap; moments more than REACH outside the gap can't be used at all."""
    outside = np.maximum(0.0, np.maximum(gap[0] - t, t - gap[1]))
    cost = env.astype(np.float64) + SLOPE * outside
    cost[outside > REACH] = np.inf
    return cost


def _middle(cost: np.ndarray, env: np.ndarray, quiet: float, k: int) -> int:
    """The middle of the run of moments around k that are as good as k: all of the silence k is
    in (anything under the `quiet` level counts the same), or else what is within 0.5 dB of k."""
    ok = (env < quiet) & np.isfinite(cost) if env[k] < quiet else np.abs(cost - cost[k]) <= 0.5
    a = k
    while a > 0 and ok[a - 1]:
        a -= 1
    b = k
    while b + 1 < len(cost) and ok[b + 1]:
        b += 1
    return (a + b) // 2


def _edge(pcm: audio.Pcm, gap: tuple[float, float], quiet: float) -> float:
    """One edge on its own: the best moment near one gap."""
    lo, hi = max(0.0, gap[0] - REACH), min(pcm.duration, gap[1] + REACH)
    env = audio.envelope(pcm, start=lo, end=hi)
    if not len(env):
        return (gap[0] + gap[1]) / 2
    t = lo + (np.arange(len(env)) + 0.5) * audio.HOP
    cost = _cost(env, t, gap)
    return float(t[_middle(cost, env, quiet, int(np.argmin(cost)))])


def _both_edges(pcm: audio.Pcm, lo: float, hi: float, before: tuple[float, float], after: tuple[float, float],
                cut: tuple[float, float], quiet: float) -> tuple[float, float]:
    """Both edges together, so the stretch between them holds the cut words' sound."""
    env = audio.envelope(pcm, start=lo, end=hi)
    t = lo + (np.arange(len(env)) + 0.5) * audio.HOP
    ca, cb = _cost(env, t, before), _cost(env, t, after)
    near = (t >= cut[0] - NEAR) & (t <= cut[1] + NEAR)
    sound = np.concatenate([[0], np.cumsum((env >= quiet) & near)])
    need = int(round(min(max(NEED * (cut[1] - cut[0]), NEED_MIN), NEED_MAX) / audio.HOP))
    starts, ends = np.flatnonzero(np.isfinite(ca)), np.flatnonzero(np.isfinite(cb))
    best, pick = np.inf, None
    for x in starts:
        ok = ends[(ends > x) & (sound[ends] - sound[x + 1] >= need)]
        if not len(ok):
            continue
        y = int(ok[np.argmin(cb[ok])])
        if ca[x] + cb[y] < best - 1e-9:
            best, pick = ca[x] + cb[y], (int(x), y)
    if pick is None:  # no stretch with enough sound (a very quiet word): each edge on its own
        return _edge(pcm, before, quiet), _edge(pcm, after, quiet)
    x, y = pick
    return float(t[_middle(ca, env, quiet, x)]), float(t[_middle(cb, env, quiet, y)])


def words_in(words: list[dict[str, Any]], a: float, b: float) -> tuple[int, int] | None:
    """The run of words whose middles fall between a and b, as (first index, index after last)."""
    inside = [k for k, w in enumerate(words) if a <= (float(w["s"]) + float(w["e"])) / 2 <= b]
    if not inside:
        return None
    return inside[0], inside[-1] + 1
