from __future__ import annotations

from core.stats import band, mean, median


def test_median_and_mean():
    assert median([]) == 0.0
    assert median([7]) == 7.0
    assert median([1, 2, 3, 100]) == 2.5  # the outlier does not drag it
    assert mean([1, 2, 3, 100]) == 26.5
    assert mean([]) == 0.0


def test_band_quartiles_inclusive():
    assert band([]) == (0.0, 0.0, 0.0)
    assert band([42]) == (42.0, 42.0, 42.0)
    assert band([10, 20, 30, 40]) == (17.5, 25.0, 32.5)
    p25, p50, p75 = band([25, 30, 35, 40, 45, 50, 200])
    assert p25 < p50 < p75
    assert p50 == 40.0
