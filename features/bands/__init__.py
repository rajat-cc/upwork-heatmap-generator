"""Bid bands: what the market pays, by workflow × client segment × experience."""

from features.bands.api import BandsLens, run

__all__ = ["BandsLens", "run"]
