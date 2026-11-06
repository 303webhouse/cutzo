"""Parakeet (NVIDIA's parakeet-tdt-0.6b-v3, run by whisper.cpp's parakeet-cli): speech to text
with a time for every word, on the graphics card.

Chosen over Whisper on Oct 15, 2026, after trying both on ten minutes of Season Debrief Part 2:
- It writes down "um" and "uh" by itself (Whisper drops them unless a prompt nags it).
- Its word times come from the model's own frame-by-frame decisions (80 ms steps). About 2% of
  words landed in silence, against 6% for Whisper, and none were seconds off. When every "um"
  was cut, 85% of the cut edges landed in silence, against 70% with Whisper's times.
- It is about 65 times faster than real time on Nick's RTX 2070 SUPER (a 90-minute episode
  takes about a minute and a half).

parakeet-cli is fast only for audio up to 50 s: longer files switch to a mode that is ten times
slower and runs out of graphics memory after about ten minutes. So the recording goes in as
pieces of at most 48 s. Each piece is responsible for its own stretch and also hears 7 s on
either side, because the model is less reliable near the ends of what it hears (it sometimes
drops the last few words). Each word is kept from the piece whose own stretch it starts in, and
the line between two stretches is put at the quietest moment around there.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..core.errors import CutzoError
from . import require, require_file

CREATE_NO_WINDOW = 0x08000000
RATE = 16000
HOP = 0.01  # seconds per loudness reading when choosing where pieces meet
PIECE = 48.0  # longest piece, in seconds (the fast mode takes up to 50 s)
MARGIN = 7.0  # what each piece hears beyond its own stretch, on both sides
OWN = PIECE - 2 * MARGIN  # longest stretch one piece is responsible for (34 s)
SEARCH = 8.0  # where a stretch may end: the quietest moment in its last 8 s
BATCH = 40  # pieces per parakeet-cli run (the model loads once per run, in about 1.5 s)

_TOKEN = re.compile(r'^\s*\[\s*\d+\]\s+id=\s*\d+\s+frame=\s*\d+\s+dur_idx=\s*\d+\s+dur_val=\s*\d+\s+'
                    r'p=([0-9.]+)\s+plog=\S+\s+t0=\s*(\d+)\s+t1=\s*(\d+)\s+word_start=(true|false)\s+"(.*)"\s*$')
_FILE = re.compile(r"^\s*Processing file: (.*?)\s*$")
_SPECIAL = re.compile(r"^<[^<>]*>$")  # <unk>, </s> and the like
WORD_MARK = "▁"  # SentencePiece's "a new word starts here"


@dataclass(frozen=True)
class Piece:
    start: float  # where the piece's sound starts (seconds)
    end: float
    own_from: float  # the stretch whose words this piece supplies
    own_to: float


@dataclass(frozen=True)
class Token:
    text: str
    t0: float  # seconds from the start of the piece
    t1: float
    p: float
    word_start: bool


def transcribe(wav16k: Path, *, threads: int | None = None, progress: Callable[[float], None] | None = None,
               check: Callable[[], None] | None = None, work_dir: Path | None = None) -> dict[str, Any]:
    """Transcribe a 16 kHz mono 16-bit WAV. Returns {"words": [{"w", "s", "e", "p"}...], "text": ...}
    with times in seconds from the start of the file."""
    exe = require("parakeet-cli")
    model = require_file("parakeet-model")
    threads = threads or max(1, min(8, (os.cpu_count() or 2) // 2))
    levels, duration = loudness_outline(wav16k)
    if duration < 0.3:
        return {"words": [], "text": ""}
    pieces = plan(levels, duration)
    found: list[list[dict[str, Any]]] = []
    with tempfile.TemporaryDirectory(dir=work_dir, prefix="parakeet-") as tmp, wave.open(str(wav16k), "rb") as src:
        for first in range(0, len(pieces), BATCH):
            batch = pieces[first:first + BATCH]
            files = [Path(tmp) / f"piece-{first + k:04d}.wav" for k in range(len(batch))]
            for piece, file in zip(batch, files):
                _write_piece(src, piece, file)

            def started(k: int, first: int = first) -> None:
                if progress:
                    progress(min(0.99, (first + k) / len(pieces)))

            for piece, tokens in zip(batch, run(exe, model, files, threads=threads, on_file=started, check=check)):
                last = piece.own_to >= duration
                found.append([w for w in words_from_tokens(tokens, offset=piece.start)
                              if piece.own_from <= w["s"] and (w["s"] < piece.own_to or last)])
            for file in files:
                file.unlink(missing_ok=True)
    if progress:
        progress(1.0)
    words = _join(found)
    return {"words": words, "text": " ".join(w["w"] for w in words)}


def _join(per_piece: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One list of words from the pieces' own stretches. Where two stretches meet, a word both
    pieces heard right at the line (same word, nearly the same time) is kept once."""
    words: list[dict[str, Any]] = []
    for part in per_piece:
        if words and part:
            a, b = words[-1], part[0]
            if _plain(a["w"]) == _plain(b["w"]) and abs(a["s"] - b["s"]) < 0.3:
                part = part[1:]
        words.extend(part)
    return words


def _plain(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def loudness_outline(wav: Path) -> tuple[np.ndarray, float]:
    """The level (dBFS) of every 10 ms of a 16 kHz mono WAV, and its length in seconds."""
    with wave.open(str(wav), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise CutzoError("The audio for speech to text came out in the wrong format.",
                             hint="Tell Claude; this shouldn't happen.")
        frames = w.getnframes()
        hop = int(RATE * HOP)
        out = []
        while True:
            data = w.readframes(hop * 6000)
            if not data:
                break
            x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            whole = len(x) // hop * hop
            if whole:
                out.append(10 * np.log10(np.mean(x[:whole].reshape(-1, hop) ** 2, axis=1) + 1e-12))
    return (np.concatenate(out) if out else np.zeros(0, dtype=np.float32)), frames / RATE


def plan(levels: np.ndarray, duration: float) -> list[Piece]:
    """Cut the recording into pieces (see the top of this file). `levels` is the 10 ms outline."""
    if duration <= PIECE:
        return [Piece(0.0, round(duration, 3), 0.0, round(duration, 3))]
    smooth = np.convolve(levels, np.ones(15) / 15, mode="same") if len(levels) >= 15 else np.asarray(levels)
    marks = [0.0]
    while duration - marks[-1] > OWN:
        a = int((marks[-1] + OWN - SEARCH) / HOP)
        b = min(len(smooth), int((marks[-1] + OWN) / HOP))
        mark = (a + int(np.argmin(smooth[a:b])) + 0.5) * HOP if b > a else marks[-1] + OWN
        marks.append(round(mark, 3))
    marks.append(round(duration, 3))
    return [Piece(round(max(0.0, a - MARGIN), 3), round(min(duration, b + MARGIN), 3), a, b)
            for a, b in zip(marks, marks[1:])]


def _write_piece(src: wave.Wave_read, piece: Piece, path: Path) -> None:
    src.setpos(min(src.getnframes(), int(round(piece.start * RATE))))
    data = src.readframes(int(round((piece.end - piece.start) * RATE)))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data)


def run(exe: Path, model: Path, files: list[Path], *, threads: int = 4, on_file: Callable[[int], None] | None = None,
        check: Callable[[], None] | None = None) -> list[list[Token]]:
    """Run parakeet-cli once over several pieces (the model loads once). Returns each piece's tokens."""
    cmd = [str(exe), "-m", str(model), "-t", str(threads), "-ps", *(str(f) for f in files)]
    kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", **kwargs)
    lines: list[str] = []

    def read() -> None:
        assert proc.stderr is not None
        k = -1
        for line in proc.stderr:
            lines.append(line.rstrip("\r\n"))
            if on_file and _FILE.match(line):
                k += 1
                on_file(k)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            if check:
                check()
            reader.join(timeout=0.25)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    reader.join(timeout=10)
    problems = [x.strip() for x in lines if x.strip().lower().startswith("error")]
    if proc.returncode != 0 or problems:
        said = [x for x in lines if x.strip()]
        reason = problems[0] if problems else (said[-1].strip() if said else f"exit code {proc.returncode}")
        raise CutzoError(f"Speech to text couldn't finish: {reason}",
                         hint="If this keeps happening, tell Claude what the message says.")
    return parse(lines, len(files))


def parse(lines: list[str], pieces: int) -> list[list[Token]]:
    """parakeet-cli's -ps report (on its error stream) as tokens, one list per piece in the order given."""
    out: list[list[Token]] = [[] for _ in range(pieces)]
    k = -1
    for line in lines:
        if _FILE.match(line):
            k += 1
            continue
        m = _TOKEN.match(line)
        if m and 0 <= k < pieces:
            out[k].append(Token(text=m.group(5), t0=int(m.group(2)) / 100.0, t1=int(m.group(3)) / 100.0,
                                p=float(m.group(1)), word_start=m.group(4) == "true"))
    if k + 1 != pieces:
        raise CutzoError(f"Speech to text stopped after {max(0, k + 1)} of {pieces} pieces.",
                         hint="If this keeps happening, tell Claude.")
    return out


def words_from_tokens(tokens: list[Token], offset: float = 0.0) -> list[dict[str, Any]]:
    """Words from one piece's tokens: a token marked as a word start begins a new word; word pieces
    and punctuation join the word before ("Par" + "is", "London" + "."). Times are the piece's
    times plus `offset`; a word's doubt (p) is that of its least certain spoken token."""
    words: list[dict[str, Any]] = []
    for tok in tokens:
        if not tok.text or _SPECIAL.match(tok.text):
            continue
        starts = tok.word_start or tok.text.startswith(WORD_MARK)
        piece = tok.text.replace(WORD_MARK, "")
        if not piece:
            continue
        spoken = any(ch.isalnum() for ch in piece)
        if spoken and (starts or not words):
            words.append({"w": piece, "s": tok.t0, "e": tok.t1, "p": tok.p})
        elif words:
            last = words[-1]
            last["w"] += (" " + piece) if starts else piece
            if spoken:
                last["e"] = max(last["e"], tok.t1)
                last["p"] = min(last["p"], tok.p)
    return [{"w": w["w"], "s": round(w["s"] + offset, 3), "e": round(max(w["e"], w["s"] + 0.04) + offset, 3),
             "p": round(w["p"], 3)} for w in words]
