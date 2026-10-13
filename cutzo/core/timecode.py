"""Reading and writing times like 12:03, 1:02:45.5, 90s or 2m10s."""

from __future__ import annotations

import math
import re

from .errors import CutzoError

_UNITS = re.compile(r"^(?:(?P<h>\d+(?:\.\d+)?)h)?\s*(?:(?P<m>\d+(?:\.\d+)?)m(?!s))?\s*(?:(?P<s>\d+(?:\.\d+)?)s)?$")


def parse_time(value: object, *, name: str = "time") -> float:
    """Return seconds from a number or text such as "12:03", "1:02:45.5", "75", "90s" or "1h2m3s"."""
    if isinstance(value, bool):
        raise CutzoError(f"{name.capitalize()} must be a time like 12:03, not {value!r}.")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value or "").strip().lower().replace(",", ".")
        if not text:
            raise CutzoError(f"{name.capitalize()} is missing. Use a time like 12:03 or 1:02:45.")
        seconds = _parse_text(text, name)
    if math.isnan(seconds) or math.isinf(seconds) or seconds < 0:
        raise CutzoError(f"{name.capitalize()} can't be negative or empty: {value!r}.")
    return round(seconds, 3)


def _parse_text(text: str, name: str) -> float:
    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3 or any(p.strip() == "" for p in parts):
            raise CutzoError(f"Couldn't read {name} {text!r}. Use mm:ss or h:mm:ss, like 12:03 or 1:02:45.")
        try:
            numbers = [float(p) for p in parts]
        except ValueError:
            raise CutzoError(f"Couldn't read {name} {text!r}. Use mm:ss or h:mm:ss, like 12:03 or 1:02:45.") from None
        seconds = 0.0
        for n in numbers:
            seconds = seconds * 60 + n
        return seconds
    try:
        return float(text)
    except ValueError:
        pass
    match = _UNITS.match(text.replace(" ", ""))
    if match and any(match.groupdict().values()):
        h = float(match.group("h") or 0)
        m = float(match.group("m") or 0)
        s = float(match.group("s") or 0)
        return h * 3600 + m * 60 + s
    raise CutzoError(f"Couldn't read {name} {text!r}. Use mm:ss or h:mm:ss, like 12:03 or 1:02:45.")


def fmt_time(seconds: float, *, precise: bool = False) -> str:
    """12:03 or 1:02:03; precise=True adds tenths (12:03.4)."""
    seconds = max(0.0, float(seconds))
    if precise:
        tenths = int(round(seconds * 10))
        whole, frac = divmod(tenths, 10)
    else:
        whole, frac = int(seconds), None
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    out = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    if frac is not None:
        out += f".{frac}"
    return out


def fmt_duration(seconds: float) -> str:
    """Friendly length: 45 s, 3 min 20 s, 1 h 12 min."""
    seconds = max(0, int(round(float(seconds))))
    if seconds < 60:
        return f"{seconds} s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m} min {s} s" if s else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if m else f"{h} h"
