"""Compile a list of `(label, [keywords])` into word-boundary regexes.

Used by every classifier so substring-style false positives ("ai" inside
"main") are impossible. Compilation happens once at import time of the
calling module.
"""

from __future__ import annotations

import re
from re import Pattern

CompiledTaxonomy = list[tuple[str, Pattern[str]]]


def compile_taxonomy(taxonomy: list[tuple[str, list[str]]]) -> CompiledTaxonomy:
    """One regex per category: \\b(?:k1|k2|…)\\b, case-insensitive."""
    out: CompiledTaxonomy = []
    for label, kws in taxonomy:
        if not kws:
            continue
        pattern = r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b"
        out.append((label, re.compile(pattern, re.IGNORECASE)))
    return out


def match_categories(text: str, compiled: CompiledTaxonomy) -> list[str]:
    """Return all labels whose regex fires anywhere in `text`."""
    return [label for label, rx in compiled if rx.search(text)]
