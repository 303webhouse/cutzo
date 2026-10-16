"""Captions (.srt) and the YouTube chapter list for a finished episode.

The words come from the transcript (recording time). Each word is placed where it plays in the
finished file by the render plan's voice pieces (the teaser, the conversation, the sign-off): a
piece plays recording time `start..end` from finished time `at`. Cut words are in no piece, so
they never show; words the teaser or sign-off repeat show again there.

Words are grouped into captions the way broadcast subtitles are: at most two lines of 42
characters, at most 7 seconds, a new caption after a pause or when someone else speaks (a word or
two on their own joins the next caption instead of flashing up alone), and "um" and "uh" left out
(the rest is word for word, with a capital after the end of a sentence).
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Any, Iterable

from .timecode import fmt_time
from .transcript import anchor

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "hm", "mm", "mmm"}
MAX_CHARS = 42  # per line
MAX_LINES = 2
MAX_SECONDS = 7.0
PAUSE = 1.0  # a silence this long starts a new caption
TINY_WORDS = 2  # a caption this short joins the next one if it follows within JOIN seconds
JOIN = 1.5
LINGER = 0.4  # a caption stays up a little after its last word (if the next one isn't due yet)
MIN_SECONDS = 1.0
_ENDS = (".", "?", "!")
_STRIP = "\"'“”‘’()[],;:"


@dataclass
class Cue:
    start: float
    end: float
    words: list[str] = field(default_factory=list)
    speaker: Any = None

    @property
    def text(self) -> str:
        return " ".join(self.words)

    def lines(self, max_chars: int = MAX_CHARS) -> list[str]:
        """One line, or two balanced lines split at the best word break near the middle."""
        text = self.text
        if len(text) <= max_chars or len(self.words) < 2:
            return [text]
        best, best_score = 1, float("inf")
        for k in range(1, len(self.words)):
            first, second = " ".join(self.words[:k]), " ".join(self.words[k:])
            score = abs(len(first) - len(second)) + 100 * (max(len(first), len(second)) > max_chars)
            if self.words[k - 1].endswith((",", ".", "?", "!", ";", ":")):
                score -= 8  # a break after punctuation reads better
            if score < best_score:
                best, best_score = k, score
        return [" ".join(self.words[:best]), " ".join(self.words[best:])]


def place_words(words: list[dict[str, Any]], pieces: Iterable[Any]) -> list[dict[str, Any]]:
    """Every word that plays in the finished file, at its finished time, in order:
    [{"w", "s", "e", "sp"}]. `pieces` are render.Piece objects; only the voice ones carry words."""
    ordered = sorted(words, key=lambda w: float(w["s"]))
    starts = [float(w["s"]) for w in ordered]
    out: list[dict[str, Any]] = []
    for p in pieces:
        if getattr(p, "kind", "") != "voice":
            continue
        k = bisect_left(starts, p.start - 5.0)  # a long word can start before its anchor
        for w in ordered[k:]:
            if float(w["s"]) >= p.end:
                break
            if not p.start <= anchor(w) < p.end:
                continue
            s = p.at + max(0.0, float(w["s"]) - p.start)
            e = p.at + min(p.end, float(w["e"])) - p.start
            out.append({"w": str(w["w"]), "s": s, "e": max(e, s + 0.05), "sp": w.get("sp")})
    out.sort(key=lambda w: w["s"])
    return out


def _bare(word: str) -> str:
    return word.strip(_STRIP + ".?!-").lower()


def cues(words: list[dict[str, Any]], *, max_chars: int = MAX_CHARS, max_lines: int = MAX_LINES,
         max_seconds: float = MAX_SECONDS, pause: float = PAUSE) -> list[Cue]:
    """Group placed words (see place_words) into captions."""
    out: list[Cue] = []
    current: Cue | None = None
    last_end, last_speaker = 0.0, None
    capitalise = False
    room = max_chars * max_lines
    for w in words:
        text = " ".join(str(w["w"]).split())
        if not text:
            continue
        if _bare(text) in FILLERS:
            # Left out. Keep what it carried: the end of a sentence, or the capital of a new one.
            if current and current.words and text.endswith(_ENDS) and not current.words[-1].endswith(_ENDS):
                current.words[-1] += text[-1]
            capitalise = capitalise or text[:1].isupper()
            continue
        if capitalise or (current is not None and current.words[-1].endswith(_ENDS)):
            text = text[:1].upper() + text[1:]
        capitalise = False
        if current is not None:
            joined = len(current.text) + 1 + len(text)
            new_speaker = w.get("sp") is not None and last_speaker is not None and w.get("sp") != last_speaker
            sentence_done = current.words[-1].endswith(_ENDS) and len(current.text) >= max_chars * 0.6
            if (w["s"] - last_end > pause or joined > room or w["e"] - current.start > max_seconds
                    or new_speaker or sentence_done):
                out.append(current)
                current = None
        if current is None:
            current = Cue(start=float(w["s"]), end=float(w["e"]), speaker=w.get("sp"))
        current.words.append(text)
        current.end = float(w["e"])
        last_end, last_speaker = float(w["e"]), w.get("sp")
    if current is not None:
        out.append(current)
    # A word or two on their own ("I ... I don't know") join the next caption when it comes soon.
    joined_up: list[Cue] = []
    for cue in out:
        if joined_up:
            prev = joined_up[-1]
            tiny = len(prev.words) <= TINY_WORDS or len(cue.words) <= TINY_WORDS
            fits = len(prev.text) + 1 + len(cue.text) <= room and cue.end - prev.start <= max_seconds
            if tiny and fits and cue.start - prev.end < JOIN and prev.speaker == cue.speaker:
                prev.words += cue.words
                prev.end = cue.end
                continue
        joined_up.append(cue)
    out = joined_up
    # Timing: linger a little, stay up at least MIN_SECONDS when there's room, never overlap.
    for k, cue in enumerate(out):
        nxt = out[k + 1].start if k + 1 < len(out) else float("inf")
        cue.end = min(max(cue.end + LINGER, cue.start + MIN_SECONDS), nxt - 0.04)
        cue.end = max(cue.end, cue.start + 0.2)
    return out


def _stamp(t: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def srt(items: list[Cue]) -> str:
    blocks = [f"{n}\n{_stamp(c.start)} --> {_stamp(c.end)}\n" + "\n".join(c.lines()) for n, c in enumerate(items, 1)]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def chapter_list(chapters: list[dict[str, Any]]) -> str:
    """The lines for a YouTube description: '0:00 Opening' and so on."""
    return "\n".join(f"{fmt_time(c['time'])} {c['title']}" for c in chapters) + ("\n" if chapters else "")


def read_srt(text: str) -> list[tuple[float, float, str]]:
    """(start, end, text) for each caption of an .srt file (for checks and tests)."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                    " ".join(lines[2:])))
    return out
