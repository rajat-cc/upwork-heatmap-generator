"""Industry tagging for jobs the regexes leave untagged, via the Claude Code CLI.

Why the CLI: the plan's decision is to spend the Claude Max subscription, not
API dollars, so this shells out to `claude -p … --output-format json` on the
machine that runs `sync`. The runner is injectable, so tests never spawn a
process, and a later API-backed runner would go through the official
`anthropic` SDK (model `claude-opus-5`), never a raw HTTP call.

Guard rails: only jobs that still have text and no industry label; batches of
ten; labels validated against the taxonomy; persisted with `source='llm'` and
a confidence below the regex rows'; a per-run cap; silent skip when the CLI is
absent or disabled.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

import config
from core.logging_setup import get_logger
from core.models import Job
from db import get_conn, save_classifications
from taxonomies.n8n import INDUSTRIES

log = get_logger(__name__)

INDUSTRY_LABELS = [name for name, _ in INDUSTRIES]
BATCH = 10
DESCRIPTION_CHARS = 500
LLM_CONFIDENCE = 0.7

Runner = Callable[[str], str]  # prompt → model text


@dataclass(slots=True)
class TagResult:
    considered: int = 0
    tagged_jobs: int = 0
    labels: int = 0
    skipped: str | None = None
    errors: list[str] = field(default_factory=list)


def enabled() -> bool:
    return bool(config.LLM_TAGGING)


def build_prompt(jobs: list[Job]) -> str:
    items = "\n\n".join(
        f"[{j.id}]\nTitle: {j.title}\nDescription: {j.description[:DESCRIPTION_CHARS]}"
        for j in jobs
    )
    labels = "\n".join(f"- {name}" for name in INDUSTRY_LABELS)
    return (
        "You classify Upwork job posts by the client's industry. Use ONLY labels from this list:\n"
        f"{labels}\n\n"
        "Return one JSON object mapping each job id to a list of zero, one or two labels. "
        "Use an empty list when the industry is not stated or implied. No prose, no code fences.\n\n"
        f"{items}\n"
    )


def default_runner(prompt: str) -> str:
    """Run `claude -p` and return the model's text (the `result` of its JSON envelope)."""
    binary = shutil.which("claude")
    if not binary:
        raise FileNotFoundError("claude CLI not found")
    cmd = [binary, "-p", prompt, "--output-format", "json", "--no-session-persistence"]
    if config.LLM_MODEL:
        cmd += ["--model", config.LLM_MODEL]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=config.LLM_TIMEOUT_SECONDS, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {proc.stderr.strip()[:200]}")
    out = proc.stdout.strip()
    try:
        envelope = json.loads(out)
    except ValueError:
        return out
    if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
        return envelope["result"]
    return out


_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def parse_labels(text: str, allowed: list[str] | None = None) -> dict[str, list[str]]:
    """Extract {job_id: [labels]} from the model text; unknown labels are dropped."""
    allowed_set = set(allowed or INDUSTRY_LABELS)
    m = _JSON_OBJECT.search(text or "")
    if not m:
        return {}
    try:
        doc = json.loads(m.group(0))
    except ValueError:
        return {}
    out: dict[str, list[str]] = {}
    if not isinstance(doc, dict):
        return out
    for job_id, labels in doc.items():
        if isinstance(labels, str):
            labels = [labels]
        if not isinstance(labels, list):
            continue
        clean = [lab for lab in labels if isinstance(lab, str) and lab in allowed_set]
        out[str(job_id)] = clean[:2]
    return out


def candidates(limit: int) -> list[Job]:
    """Jobs with text and no industry label from any source, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT j.* FROM jobs j
             WHERE j.purged_at IS NULL AND j.description != ''
               AND NOT EXISTS (
                   SELECT 1 FROM job_classifications c
                    WHERE c.job_id = j.id AND c.axis = 'industry')
             ORDER BY j.published_at DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [Job.from_row(r) for r in rows]


def tag(limit: int | None = None, runner: Runner | None = None) -> TagResult:
    result = TagResult()
    if not enabled():
        result.skipped = "disabled (UPWORK_LLM_TAGGING=0)"
        return result
    if runner is None:
        if shutil.which("claude") is None:
            result.skipped = "claude CLI not found"
            return result
        runner = default_runner

    jobs = candidates(limit or config.LLM_TAG_CAP)
    result.considered = len(jobs)
    for start in range(0, len(jobs), BATCH):
        batch = jobs[start : start + BATCH]
        try:
            text = runner(build_prompt(batch))
        except Exception as exc:  # one failed batch must not sink the run
            log.warning("LLM tagging batch failed: %s", exc)
            result.errors.append(str(exc)[:200])
            continue
        labels = parse_labels(text)
        rows = []
        for job in batch:
            for label in labels.get(job.id, []):
                rows.append(
                    {
                        "job_id": job.id,
                        "axis": "industry",
                        "label": label,
                        "source": "llm",
                        "confidence": LLM_CONFIDENCE,
                    }
                )
        if rows:
            save_classifications(rows)
            result.labels += len(rows)
            result.tagged_jobs += len({r["job_id"] for r in rows})
    return result


def configured_note() -> str:
    if not enabled():
        return "LLM tagging disabled"
    binary = shutil.which("claude")
    return f"LLM tagging via {os.path.basename(binary)}" if binary else "claude CLI not found"
