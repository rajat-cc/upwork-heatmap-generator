"""Contract for `exports/market_intel_latest.json`.

Hand-rolled validation (no dependency): the document must carry these keys
with these types, and must never carry job ids or free text. The JSON Schema
in `docs/market_intel.schema.json` mirrors this for other consumers.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1

FORBIDDEN_KEYS = {"job_id", "job_ids", "title", "description", "url", "ids"}

_BAND_KEYS = {
    "workflow": str,
    "client_segment": str,
    "experience": str,
    "budget_type": str,
    "n": int,
    "p25": (int, float),
    "p50": (int, float),
    "p75": (int, float),
}
_DEMAND_KEYS = {"axis": str, "label": str, "jobs": int, "share": (int, float)}
_WATCH_KEYS = {"expression": str, "jobs_7d": int, "median_applicants": (int, float)}


def _check(errors: list[str], where: str, obj: Any, spec: dict) -> None:
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected object")
        return
    for key, typ in spec.items():
        if key not in obj:
            errors.append(f"{where}: missing {key}")
        elif not isinstance(obj[key], typ) or isinstance(obj[key], bool) and typ is int:
            errors.append(f"{where}: {key} has wrong type {type(obj[key]).__name__}")


def _forbidden(errors: list[str], where: str, obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                errors.append(f"{where}: forbidden key {k}")
            _forbidden(errors, f"{where}.{k}", v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _forbidden(errors, f"{where}[{i}]", v)


def validate(doc: Any) -> list[str]:
    """Return a list of problems; empty means the document is valid."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["document must be an object"]
    for key, typ in {
        "schema_version": int,
        "generated_at": str,
        "data_as_of": str,
        "window_days": int,
        "scoring_version": str,
        "bands": list,
        "demand": list,
        "watch_suggestions": list,
        "funnel_summary": dict,
    }.items():
        if key not in doc:
            errors.append(f"missing {key}")
        elif not isinstance(doc[key], typ):
            errors.append(f"{key} has wrong type {type(doc[key]).__name__}")
    if errors:
        return errors
    if doc["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version {doc['schema_version']} != {SCHEMA_VERSION}")
    for i, b in enumerate(doc["bands"]):
        _check(errors, f"bands[{i}]", b, _BAND_KEYS)
    for i, d in enumerate(doc["demand"]):
        _check(errors, f"demand[{i}]", d, _DEMAND_KEYS)
    for i, w in enumerate(doc["watch_suggestions"]):
        _check(errors, f"watch_suggestions[{i}]", w, _WATCH_KEYS)
    fs = doc["funnel_summary"]
    for key in ("days", "stages", "win_rate_shrunk", "insufficient"):
        if key not in fs:
            errors.append(f"funnel_summary: missing {key}")
    _forbidden(errors, "$", doc)
    return errors
