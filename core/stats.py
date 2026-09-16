"""Small, dependency-free statistics helpers.

Budgets on Upwork are heavy-tailed: one $10k fixed post moves an average a
long way. Every money figure in the lenses therefore reports the median and
the p25–p75 band, never the arithmetic mean.
"""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median as _median
from statistics import quantiles as _quantiles


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def median(values: Iterable[float]) -> float:
    vals = list(values)
    return float(_median(vals)) if vals else 0.0


def band(values: Iterable[float]) -> tuple[float, float, float]:
    """(p25, median, p75). Inclusive quartiles; a single value spans no band."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return (0.0, 0.0, 0.0)
    if len(vals) == 1:
        return (vals[0], vals[0], vals[0])
    q = _quantiles(vals, n=4, method="inclusive")
    return (q[0], q[1], q[2])
