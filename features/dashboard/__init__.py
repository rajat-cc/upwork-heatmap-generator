"""General intelligence dashboard: skills heatmap + client quality + BD shift.

Public API:
    run(days, tz, categories, watch=None) — orchestrates the four analyses
    run_skills_only(days, categories)
    run_shift_only(days, tz)
"""
from features.dashboard.api import run, run_shift_only, run_skills_only

__all__ = ["run", "run_skills_only", "run_shift_only"]
