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


def anchor(word: dict[str, Any]) -> float:
    """The moment that decides whether a word is cut: its sound's middle ("c", from the build),
    or the middle of its times for transcripts made before Cutzo measured that."""
    c = word.get("c")
    return float(c) if c is not None else (float(word["s"]) + float(word["e"])) / 2


def is_word_cut(word: dict[str, Any], cuts: list[Range]) -> bool:
    t = anchor(word)
    return any(a <= t < b for a, b in cuts)


def paragraphs(transcript: dict[str, Any], cuts: Iterable[Range] = (), gap: float = 1.6,
               soft: int = 45, hard: int = 120) -> list[dict[str, Any]]:
    """Group words into speaker turns; a long pause also starts a new paragraph. Without speakers
    (one combined recording) turns run long, so after `soft` words a sentence end starts a new
    paragraph, and `hard` words always do."""
    merged = merge(cuts)
    names = {s["id"]: s["name"] for s in transcript.get("speakers", [])}
    out: list[dict[str, Any]] = []
    for idx, w in enumerate(transcript.get("words", [])):
        size = len(out[-1]["words"]) if out else 0
        new_para = (
            not out
            or out[-1]["speaker_id"] != w.get("sp")
            or float(w["s"]) - out[-1]["end"] > gap
            or (size >= soft and out[-1]["words"][-1]["w"].rstrip("\"'”’)").endswith((".", "?", "!")))
            or size >= hard
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
            who = f"{para['speaker']}: " if para.get("speaker_id") else ""  # one combined recording: nobody to name
            lines.append(f"[{fmt_time(para['start'])}] {who}{text}")
    return "\n".join(lines)
