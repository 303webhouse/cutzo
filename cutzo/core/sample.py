"""Sample episodes and the built-in test clip (Phase 1: everything here is made up).

Each sample script becomes:
  - a word-level transcript with timings, and
  - a short WAV "test clip" whose sound follows those words (humming voices,
    pauses for dead air), so the waveform, playback, cuts and chapters can be
    tried without any real recording.
"""

from __future__ import annotations

import math
import random
import wave
from array import array
from pathlib import Path
from typing import Any

from .media import classify
from .transcript import tokens

SAMPLE_RATE = 16000
FILLERS = {"um", "uh", "erm", "uhm"}

# Script items: ("chapter", title) | ("pause", seconds) | (speaker_id, text)
SCRIPTS: dict[str, dict[str, Any]] = {
    "dig": {
        "speakers": [("S1", "Host"), ("S2", "Guest 1"), ("S3", "Guest 2")],
        "lines": [
            ("chapter", "Welcome back"),
            ("S1", "Welcome back to The Dig. This is part two of our season debrief, and today is all about impossible trades."),
            ("S2", "Um, I made a list, and uh, some of these are pretty wild."),
            ("pause", 2.8),
            ("S1", "Quick reminder: everything in this preview is sample text, so nothing here is real."),
            ("chapter", "The rules"),
            ("S3", "Rule one. Every trade has to be something no front office would ever accept."),
            ("S2", "Which makes it, you know, way more fun to argue about."),
            ("pause", 2.3),
            ("chapter", "Impossible trade number one"),
            ("S1", "Okay, first impossible trade. Go."),
            ("S3", "So, uh, picture a three team deal that sends half the league to Denver."),
            ("S2", "That is completely impossible. I love it."),
            ("S1", "We'll be right back after this."),
        ],
    },
    "spooky": {
        "speakers": [("S1", "Host"), ("S2", "Guest")],
        "lines": [
            ("chapter", "The New Heretics"),
            ("S1", "Welcome to The New Heretics. It's the spooky episode, so grab a blanket and turn the lights down."),
            ("S2", "Um, I brought a ghost story, and uh, it's a good one."),
            ("pause", 2.6),
            ("S1", "Fair warning, this preview uses sample text, so the ghosts are made up too."),
            ("chapter", "Ghost stories"),
            ("S2", "It starts with an old house, a long hallway, and a door that will not stay closed."),
            ("S1", "Of course it won't. They never do."),
            ("pause", 2.1),
            ("S2", "And every night at three, you hear a page turn by itself."),
            ("chapter", "Heretical Haiku"),
            ("S1", "Nope. That's it. I'm out. Time for the haiku."),
        ],
    },
    "tnh-ep1": {
        "speakers": [("S1", "Nick Baren"), ("S2", "Guest One"), ("S3", "Guest Two")],
        "lines": [
            ("chapter", "The New Heretics"),
            ("S1", "Welcome to the very first episode of The New Heretics."),
            ("S2", "Thanks for having me. Um, this is exciting."),
            ("S3", "Uh, can everyone hear me okay? My camera froze for a second."),
            ("pause", 2.6),
            ("S1", "We hear you fine. Sample text again, so don't quote any of this."),
            ("chapter", "Define Your Terms"),
            ("S2", "Let's start by defining our terms, because words matter on this show."),
            ("S3", "Agreed. Nobody else was asking the strange questions."),
            ("S1", "Exactly. Let's get into it."),
        ],
    },
    "generic": {
        "speakers": [("S1", "Host"), ("S2", "Guest")],
        "lines": [
            ("chapter", "Intro"),
            ("S1", "This is a sample transcript. Real transcription arrives in Phase 3."),
            ("S2", "Um, so everything you see here is made up, uh, just to test the screens."),
            ("pause", 2.4),
            ("S1", "Try cutting a few words, adding a chapter, or exporting."),
            ("chapter", "Main conversation"),
            ("S2", "You can also ask Claude to do any of this from chat."),
            ("S1", "And every change shows up in the activity log."),
        ],
    },
}

_G = r"G:\My Drive"
SAMPLE_EPISODES: list[dict[str, Any]] = [
    {
        "id": "the-dig-season-debrief-part-2",
        "show": "the-dig",
        "title": "2025-26 Season Debrief Part 2: Impossible Trades",
        "script": "dig",
        "test_episode": True,
        "built": True,
        "media": [rf"{_G}\Denver Stiffs\The Dig\Season Debrief Part 2\Season Debrief Part 2.mp3"],
        "persons": {},
    },
    {
        "id": "the-new-heretics-spooky-sode",
        "show": "the-new-heretics",
        "title": "Spooky Sode (JW Ghost Stories)",
        "script": "spooky",
        "test_episode": True,
        "built": True,
        "media": [
            rf"{_G}\The New Heretics\Podcast\Episodes\Spooky Sode\Spooky Sode Raw.mp3",
            rf"{_G}\The New Heretics\Podcast\Episodes\Spooky Sode\Spooky Haiku.wav",
        ],
        "persons": {},
    },
    {
        "id": "the-new-heretics-episode-1",
        "show": "the-new-heretics",
        "title": "Episode 1 (StreamYard tracks)",
        "script": "tnh-ep1",
        "test_episode": False,
        "built": False,
        "media": [
            rf"{_G}\The New Heretics\Podcast\Episodes\Episode 1\TNH_Episode_1-Nick_Baren-webcam-00h_00m_00s_571ms-StreamYard.wav",
            rf"{_G}\The New Heretics\Podcast\Episodes\Episode 1\TNH_Episode_1-Guest_One-webcam-00h_00m_01s_204ms-StreamYard.wav",
            rf"{_G}\The New Heretics\Podcast\Episodes\Episode 1\TNH_Episode_1-Guest_Two-webcam-00h_00m_03s_880ms-StreamYard.wav",
            rf"{_G}\The New Heretics\Podcast\Episodes\Episode 1\TNH_Episode_1-Nick_Baren-webcam-00h_00m_00s_571ms-StreamYard.mp4",
            rf"{_G}\The New Heretics\Podcast\Episodes\Episode 1\TNH_Episode_1-Guest_One-webcam-00h_00m_01s_204ms-StreamYard.mp4",
            rf"{_G}\The New Heretics\Podcast\Episodes\Episode 1\TNH_Episode_1-Guest_Two-webcam-00h_00m_03s_880ms-StreamYard.mp4",
        ],
        "persons": {},
    },
]

# Voice colour of each speaker in the test clip: (base pitch Hz, loudness)
_VOICES = {"S1": (118.0, 0.62), "S2": (196.0, 0.40), "S3": (152.0, 0.52), "S4": (172.0, 0.5)}


def sample_media(paths: list[str]) -> list[dict[str, Any]]:
    out = []
    for n, path in enumerate(paths, start=1):
        info = classify(path)
        info["id"] = f"m{n}"
        info["original"] = True
        info["exists"] = False  # sample paths are examples, not checked on disk
        out.append(info)
    return out


def script_for(name: str, speaker_names: list[str] | None = None) -> dict[str, Any]:
    script = SCRIPTS[name]
    speakers = list(script["speakers"])
    if speaker_names:
        speakers = [(sid, speaker_names[i] if i < len(speaker_names) else label)
                    for i, (sid, label) in enumerate(speakers)]
    return {"speakers": speakers, "lines": script["lines"]}


def perform(script: dict[str, Any], seed: int = 7) -> dict[str, Any]:
    """Lay the script out in time: words with start/end, pauses, chapter marks."""
    rnd = random.Random(seed)
    t = 0.6
    words: list[dict[str, Any]] = []
    pauses: list[tuple[float, float]] = []
    chapters: list[dict[str, Any]] = []
    pending_chapter: str | None = None
    for item in script["lines"]:
        kind, value = item
        if kind == "chapter":
            pending_chapter = value
            continue
        if kind == "pause":
            pauses.append((round(t, 3), round(t + float(value), 3)))
            t += float(value)
            continue
        if pending_chapter is not None:
            chapters.append({"time": 0.0 if not chapters else round(max(0.0, t - 0.2), 3), "title": pending_chapter})
            pending_chapter = None
        for raw in value.split():
            letters = sum(ch.isalnum() for ch in raw)
            token = (tokens(raw) or [""])[0]
            dur = 0.38 if token in FILLERS else min(0.62, max(0.16, 0.11 + 0.052 * letters))
            words.append({"w": raw, "s": round(t, 3), "e": round(t + dur, 3), "sp": kind})
            t += dur + 0.06 + rnd.random() * 0.05
            if raw.endswith(","):
                t += 0.14
            elif raw.endswith((".", "?", "!", ":")):
                t += 0.32
        t += 0.38
    duration = round(t + 1.0, 3)
    return {
        "speakers": [{"id": sid, "name": name} for sid, name in script["speakers"]],
        "words": words,
        "pauses": pauses,
        "chapters": chapters,
        "duration": duration,
    }


def auto_edits(performance: dict[str, Any]) -> list[dict[str, Any]]:
    """What Phase 1's pretend Auto-edit finds: long pauses and um/uh."""
    edits = []
    for a, b in performance["pauses"]:
        if b - a >= 1.5:
            edits.append({"type": "cut", "start": round(a + 0.25, 3), "end": round(b - 0.25, 3),
                          "reason": "dead air", "text": f"{b - a:.1f} s of silence"})
    for w in performance["words"]:
        if (tokens(w["w"]) or [""])[0] in FILLERS:
            edits.append({"type": "cut", "start": round(w["s"] - 0.02, 3), "end": round(w["e"] + 0.04, 3),
                          "reason": "filler word", "text": w["w"].strip(",.")})
    edits.sort(key=lambda e: e["start"])
    return edits


def synthesize(performance: dict[str, Any], path: Path, seed: int = 11) -> None:
    """Write the test clip: a hum that follows each word, quiet room noise between."""
    rnd = random.Random(seed)
    sr = SAMPLE_RATE
    n = int(performance["duration"] * sr) + 1
    buf = array("f", [0.0]) * n

    size = 2048
    mask = size - 1
    harmonics = [1.0, 0.62, 0.48, 0.36, 0.26, 0.16, 0.1, 0.06]
    table = [sum(a * math.sin(2 * math.pi * (k + 1) * i / size) for k, a in enumerate(harmonics)) for i in range(size)]
    peak = max(abs(v) for v in table)
    table = [v / peak for v in table]

    two_pi = 2 * math.pi
    for w in performance["words"]:
        base, loud = _VOICES.get(w.get("sp"), (140.0, 0.5))
        i0 = int(w["s"] * sr)
        i1 = min(n, int(w["e"] * sr))
        length = max(1, i1 - i0)
        letters = max(1, sum(ch.isalnum() for ch in w["w"]))
        syllables = max(1, round(letters / 3))
        syl_len = length / syllables
        wobble = rnd.uniform(0, two_pi)
        bend = rnd.uniform(0.92, 1.1)
        phase = rnd.uniform(0, size)
        gain = loud * rnd.uniform(0.8, 1.05)
        step = size / sr
        for j in range(length):
            pos = (j % syl_len) / syl_len
            env = math.sin(math.pi * pos) ** 0.7
            edge = min(1.0, j / 120.0, (length - j) / 160.0)
            f = base * bend * (1.0 + 0.07 * math.sin(two_pi * 1.7 * j / sr + wobble))
            phase += f * step
            buf[i0 + j] += table[int(phase) & mask] * env * edge * gain

    out = array("h", [0]) * n
    for i in range(n):
        v = buf[i] + (rnd.random() - 0.5) * 0.008
        v = 1.0 if v > 1.0 else (-1.0 if v < -1.0 else v)
        out[i] = int(v * 29000)

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(out.tobytes())


def waveform_peaks(path: Path, buckets: int = 1200) -> dict[str, Any]:
    """Loudness outline of a WAV for drawing: one 0..1 value per bucket."""
    with wave.open(str(path), "rb") as wav:
        sr = wav.getframerate()
        channels = wav.getnchannels()
        frames = wav.readframes(wav.getnframes())
    samples = array("h")
    samples.frombytes(frames)
    if channels > 1:
        samples = samples[::channels]
    total = len(samples)
    buckets = max(10, min(int(buckets), 8000))
    size = max(1, total // buckets)
    peaks = []
    for b in range(buckets):
        chunk = samples[b * size:(b + 1) * size]
        if not chunk:
            peaks.append(0.0)
            continue
        peaks.append(round(max(max(chunk), -min(chunk)) / 32768.0, 4))
    return {"duration": round(total / sr, 3), "peaks": peaks}
