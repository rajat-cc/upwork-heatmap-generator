"""Domain taxonomies — keyword lists for classifiers and skill normalisation.

Kept as Python modules (not YAML/JSON) on purpose:
  - syntax-highlighted in the editor
  - importable by the LLM enrichment module to constrain its label vocabulary
  - diffable in PRs without escaping rules

If we ever ship taxonomies that non-engineers need to edit, move to YAML.
"""
from taxonomies.compile import compile_taxonomy

__all__ = ["compile_taxonomy"]
