"""whisper.cpp (whisper-cli): speech to text with a time for every word.

Runs on the graphics card when whisper.cpp's CUDA build finds one (Nick's RTX 2070 SUPER:
about 21x real time with the settings below, so a 90-minute episode takes about 4 minutes).

Settings, tested on Season Debrief Part 2 (Oct 15, 2026):
- Beam search (whisper.cpp's default) keeps more of the conversation than greedy decoding.
- A prompt full of "um", "uh" and "you know" makes Whisper write those down; without it, it
  quietly drops them (0 "um"s in 10 minutes against 24 with it), and editing by transcript
  needs them, since they are often what you want to cut.
- Word times come from whisper.cpp's token times. They can be a couple of tenths of a second
  off, so Cutzo moves each cut to the quietest moment nearby (see cutzo.core.cutpoints).
  whisper.cpp's DTW timing (--dtw) placed words later than the sound did, so it isn't used.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from ..core.errors import CutzoError
from . import require, require_file

CREATE_NO_WINDOW = 0x08000000
PROMPT = "Um, so, uh, I mean, like, you know, I- I think, uh, yeah. Hmm."
_PROGRESS = re.compile(r"progress\s*=\s*(\d+)\s*%")


def transcribe(wav16k: Path, *, language: str = "en", prompt: str = PROMPT, threads: int | None = None,
               progress: Callable[[float], None] | None = None, check: Callable[[], None] | None = None,
               work_dir: Path | None = None) -> dict[str, Any]:
    """Transcribe a 16 kHz mono WAV. Returns {"words": [{"w", "s", "e", "p"}...], "text": ...}
    with times in seconds from the start of the file."""
    exe = require("whisper-cli")
    model = require_file("whisper-model")
    threads = threads or max(1, min(8, (os.cpu_count() or 2) // 2))
    with tempfile.TemporaryDirectory(dir=work_dir, prefix="whisper-") as tmp:
        base = Path(tmp) / "transcript"
        cmd = [str(exe), "-m", str(model), "-f", str(wav16k), "-l", language, "-t", str(threads),
               "-ojf", "-of", str(base), "-pp", "--prompt", prompt, "--carry-initial-prompt"]
        kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", **kwargs)
        tail: list[str] = []

        def read() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                tail.append(line.rstrip())
                del tail[:-40]
                found = _PROGRESS.search(line)
                if found and progress:
                    progress(min(0.99, int(found.group(1)) / 100.0))

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
        result = base.with_suffix(".json")
        if proc.returncode != 0 or not result.is_file():
            lines = [x for x in tail if x.strip()]
            reason = lines[-1] if lines else f"exit code {proc.returncode}"
            raise CutzoError(f"Speech to text couldn't finish: {reason}",
                             hint="If this keeps happening, tell Claude what the message says.")
        data = json.loads(result.read_text(encoding="utf-8", errors="replace"))
    if progress:
        progress(1.0)
    words = words_from_json(data)
    return {"words": words, "text": " ".join(w["w"] for w in words)}


def words_from_json(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn whisper.cpp's full JSON (-ojf) into words. Tokens that start with a space start a
    new word; the rest (word pieces, punctuation, a dash) belong to the word before."""
    words: list[dict[str, Any]] = []
    for seg in data.get("transcription") or []:
        for tok in seg.get("tokens") or []:
            text = tok.get("text") or ""
            if not text.strip() or text.startswith("[_") or text.startswith("<|"):
                continue  # timing and control tokens
            offsets = tok.get("offsets") or {}
            a = float(offsets.get("from", 0)) / 1000.0
            b = max(a, float(offsets.get("to", offsets.get("from", 0))) / 1000.0)
            p = float(tok.get("p", 1.0))
            starts = text.startswith(" ") and any(ch.isalnum() for ch in text)
            if starts or not words:
                words.append({"w": text.strip(), "s": a, "e": b, "p": p})
            else:
                last = words[-1]
                last["w"] += (" " + text.strip()) if text.startswith(" ") else text  # "like –", "York-", "ramble."
                last["e"] = max(last["e"], b)
                last["p"] = min(last["p"], p)
    out = []
    for w in words:
        if not w["w"]:
            continue
        out.append({"w": w["w"], "s": round(w["s"], 3), "e": round(max(w["e"], w["s"] + 0.02), 3), "p": round(w["p"], 3)})
    return out
