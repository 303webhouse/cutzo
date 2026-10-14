"""DeepFilterNet (deep-filter): AI noise removal for voices, 48 kHz.

The strength is an attenuation limit in dB: the most the background may be turned
down. 0 dB leaves the audio untouched; 100 dB lets the model remove all it can.
A limit keeps voices natural (the plan's guard against robotic-sounding cleanup).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..core.errors import CutzoError
from . import require

CREATE_NO_WINDOW = 0x08000000
# Seconds of audio per second of work, measured on Nick's PC (Ryzen 9 3900X); only used to
# show progress, since deep-filter prints none.
SPEED = 25.0


def denoise(src_wav: Path, dst_wav: Path, *, limit_db: float = 100.0, post_filter: bool = False,
            duration: float | None = None, progress: Callable[[float], None] | None = None,
            check: Callable[[], None] | None = None) -> Path:
    """Clean `src_wav` (48 kHz WAV) into `dst_wav`. The output lines up with the input sample for sample."""
    exe = require("deep-filter")
    src_wav, dst_wav = Path(src_wav), Path(dst_wav)
    with tempfile.TemporaryDirectory(dir=dst_wav.parent, prefix="deepfilter-") as out_dir:
        cmd = [str(exe), "--compensate-delay", "--atten-lim-db", f"{max(0.0, float(limit_db)):g}",
               "--output-dir", out_dir]
        if post_filter:
            cmd.append("--pf")
        cmd.append(str(src_wav))
        kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        log = Path(out_dir) / "deep-filter.log"
        with open(log, "wb") as out:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kwargs)
            started = time.monotonic()
            expected = (duration / SPEED) if duration else None
            try:
                while proc.poll() is None:
                    if check:
                        check()
                    if progress and expected:
                        progress(min(0.95, (time.monotonic() - started) / expected))
                    time.sleep(0.25)
            except BaseException:
                proc.kill()
                proc.wait()
                raise
        result = Path(out_dir) / src_wav.name
        if proc.returncode != 0 or not result.is_file():
            lines = [x for x in log.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
            reason = lines[-1] if lines else f"exit code {proc.returncode}"
            raise CutzoError(f"DeepFilterNet couldn't clean the audio: {reason}")
        shutil.move(str(result), str(dst_wav))
    if progress:
        progress(1.0)
    return dst_wav
