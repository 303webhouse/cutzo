"""Word-level transcripts: finding phrases and producing readable text.

Format stored in each episode folder (transcript.json):
  {"speakers": [{"id": "S1", "name": "Host"}],
   "words": [{"w": "Welcome", "s": 0.32, "e": 0.61, "sp": "S1"}, ...]}
Times are recording time in seconds.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .timecode import fmt_time
from .timeline import Range, merge

_TOKEN = re.compile(r"[a-z0-9']+")


def tokens(text: str) -> list[str]:
    return [t.strip("'") for t in _TOKEN.findall(text.lower().replace("’", "'")) if t.strip("'")]


def word_token(word: dict[str, Any]) -> str:
    toks = tokens(word["w"])
    return toks[0] if toks else ""


def find_phrase(words: list[dict[str, Any]], text: str) -> list[tuple[int, int]]:
    """All places a phrase is said, as (first word index, index after last word)."""
    wanted = tokens(text)
    if not wanted:
        return []
    have = [word_token(w) for w in words]
    hits = []
    n = len(wanted)
    for i in range(len(have) - n + 1):
        if have[i:i + n] == wanted:
            hits.append((i, i + n))
    return hits


def span(words: list[dict[str, Any]], i: int, j: int) -> Range:
    return (float(words[i]["s"]), float(words[j - 1]["e"]))


def is_word_cut(word: dict[str, Any], cuts: list[Range]) -> bool:
    mid = (float(word["s"]) + float(word["e"])) / 2
    return any(a <= mid < b for a, b in cuts)


def paragraphs(transcript: dict[str, Any], cuts: Iterable[Range] = (), gap: float = 1.6) -> list[dict[str, Any]]:
    """Group words into speaker turns; a long pause also starts a new paragraph."""
    merged = merge(cuts)
    names = {s["id"]: s["name"] for s in transcript.get("speakers", [])}
    out: list[dict[str, Any]] = []
    for idx, w in enumerate(transcript.get("words", [])):
        new_para = (
            not out
            or out[-1]["speaker_id"] != w.get("sp")
            or float(w["s"]) - out[-1]["end"] > gap
        )
        if new_para:
            out.append({
                "speaker_id": w.get("sp"),
                "speaker": names.get(w.get("sp"), w.get("sp") or "Speaker"),
                "start": float(w["s"]),
                "end": float(w["e"]),
                "words": [],
            })
        para = out[-1]
        para["end"] = float(w["e"])
        para["words"].append({"i": idx, "w": w["w"], "s": w["s"], "e": w["e"], "cut": is_word_cut(w, merged)})
    return out


def as_text(transcript: dict[str, Any], cuts: Iterable[Range] = (), *, include_cut: bool = False) -> str:
    """Readable transcript with a [m:ss] time per paragraph (recording time).

    Cut words are left out, or shown as [cut: ...] when include_cut is True.
    """
    lines = []
    for para in paragraphs(transcript, cuts):
        pieces: list[str] = []
        run: list[str] = []
        for w in para["words"]:
            if w["cut"]:
                run.append(w["w"])
                continue
            if run:
                if include_cut:
                    pieces.append(f"[cut: {' '.join(run)}]")
                run = []
            pieces.append(w["w"])
        if run and include_cut:
            pieces.append(f"[cut: {' '.join(run)}]")
        text = " ".join(pieces).strip()
        if text:
            lines.append(f"[{fmt_time(para['start'])}] {para['speaker']}: {text}")
    return "\n".join(lines)
