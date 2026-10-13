"""Turning a list of cuts into the finished episode's timing.

All edits are stored in *recording time* (the time in the original recording),
so they never shift when other cuts are added or removed. The finished file's
times (for YouTube chapters, for example) are worked out from them on demand.
"""

from __future__ import annotations

from typing import Iterable

Range = tuple[float, float]


def merge(ranges: Iterable[Range]) -> list[Range]:
    """Sort and join overlapping or touching ranges."""
    out: list[list[float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in ranges if b > a):
        if out and start <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


def clip(ranges: Iterable[Range], duration: float) -> list[Range]:
    return [(max(0.0, a), min(duration, b)) for a, b in ranges if a < duration and b > 0]


def kept(duration: float, cuts: Iterable[Range]) -> list[Range]:
    """The parts of the recording that stay in the episode."""
    out: list[Range] = []
    pos = 0.0
    for a, b in merge(clip(cuts, duration)):
        if a > pos:
            out.append((pos, a))
        pos = max(pos, b)
    if pos < duration:
        out.append((pos, duration))
    return out


def total(ranges: Iterable[Range]) -> float:
    return sum(b - a for a, b in ranges)


def to_output(t: float, cuts: Iterable[Range]) -> float:
    """Recording time -> time in the finished episode. Inside a cut, snaps to the cut point."""
    removed = 0.0
    for a, b in merge(cuts):
        if t >= b:
            removed += b - a
        elif t > a:
            removed += t - a
            break
        else:
            break
    return round(max(0.0, t - removed), 3)


def is_cut(t: float, cuts: Iterable[Range]) -> bool:
    return any(a <= t < b for a, b in merge(cuts))
