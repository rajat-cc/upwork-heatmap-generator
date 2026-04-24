"""
Dice.com job data importer.
Usage:
    python fetcher_dice.py <data.json> [--category "AI/ML"]
    echo '<json>' | python fetcher_dice.py - [--category "Python"]

The JSON should be the raw Dice API response: {"data": [...], "meta": {...}}
or just a list of job objects.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from config import TECH_SKILLS
from db import init_db, upsert_jobs
from rich.console import Console

console = Console()

# Extended patterns for skill detection in free text
_SKILL_PATTERNS = {skill: re.compile(r'\b' + re.escape(skill) + r'\b', re.IGNORECASE)
                   for skill in TECH_SKILLS}

# Additional aliases → canonical skill
_ALIASES = {
    "node": "Node.js",
    "nodejs": "Node.js",
    "reactjs": "React",
    "react.js": "React",
    "vuejs": "Vue.js",
    "vue": "Vue.js",
    "angular js": "Angular",
    "angularjs": "Angular",
    "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",
    "langchain": "LangChain",
    "large language model": "LLM",
    "large language models": "LLM",
    "generative ai": "LLM",
    "gen ai": "LLM",
    "openai api": "OpenAI",
    "gpt": "OpenAI",
    "computer vision": "Computer Vision",
    "natural language processing": "NLP",
    "devops": "DevOps",
    "ci/cd": "CI/CD",
    "rest api": "REST API",
    "restful": "REST API",
    "graphql": "GraphQL",
    "postgres": "PostgreSQL",
    "elastic search": "Elasticsearch",
    "power bi": "Power BI",
    "web scraping": "Web Scraping",
    "machine learning": "Machine Learning",
    "deep learning": "Deep Learning",
    "spring boot": "Spring Boot",
    "react native": "React Native",
    "fast api": "FastAPI",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "terraform": "Terraform",
    "blockchain": "Blockchain",
    "solidity": "Solidity",
}
_ALIAS_PATTERNS = {alias: re.compile(r'\b' + re.escape(alias) + r'\b', re.IGNORECASE)
                   for alias in _ALIASES}


def extract_skills(text: str) -> list:
    found = set()
    text_lower = text.lower()

    for skill, pattern in _SKILL_PATTERNS.items():
        if pattern.search(text):
            found.add(skill)

    for alias, pattern in _ALIAS_PATTERNS.items():
        if pattern.search(text_lower):
            found.add(_ALIASES[alias])

    return sorted(found)


def parse_salary(salary_str: str) -> tuple:
    """Returns (budget_type, budget_amount)."""
    if not salary_str or salary_str.lower().strip() in ("depends on experience", "n/a", ""):
        return "UNKNOWN", 0.0

    s = re.sub(r'[$,]', '', salary_str)
    is_annual = bool(re.search(r'\bper\s+year\b|\bannual\b|\byear\b', salary_str, re.IGNORECASE))
    nums = [float(n) for n in re.findall(r'\d+(?:\.\d+)?', s) if float(n) > 0]

    if not nums:
        return "UNKNOWN", 0.0

    avg = sum(nums[:2]) / len(nums[:2])

    if is_annual or avg > 5000:
        return "FIXED", round(avg, 2)
    return "HOURLY", round(avg, 2)


def parse_tier(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    if re.search(r'\b(senior|sr\.|lead|principal|expert|staff|svp|vp|avp|c\d+)\b', text):
        return "EXPERT"
    if re.search(r'\b(junior|jr\.|entry.?level|graduate|intern|0.?3\s*year)\b', text):
        return "ENTRY_LEVEL"
    return "INTERMEDIATE"


def dice_to_jobs(data: list, category: str) -> list:
    jobs = []
    for item in data:
        title = item.get("title") or ""
        summary = item.get("summary") or ""
        text = title + " " + summary

        skills = extract_skills(text)
        budget_type, budget_amount = parse_salary(item.get("salary") or "")
        tier = parse_tier(title, summary)
        published = (item.get("postedDate") or "").replace("Z", "")
        job_id = item.get("id") or item.get("guid") or ""

        if not job_id or not published:
            continue

        jobs.append({
            "id": f"dice_{job_id}",
            "title": title,
            "published_at": published,
            "category": category,
            "contractor_tier": tier,
            "budget_type": budget_type,
            "budget_amount": budget_amount,
            "skills": json.dumps(skills),
        })
    return jobs


def load_file(path: str, category: str) -> int:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text()

    payload = json.loads(raw)

    # Accept either {"data": [...]} or a plain list
    if isinstance(payload, dict):
        data = payload.get("data") or []
    else:
        data = payload

    if not data:
        console.print("[yellow]No jobs found in input.[/yellow]")
        return 0

    jobs = dice_to_jobs(data, category)
    init_db()
    n = upsert_jobs(jobs)
    return n


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="JSON file path or '-' for stdin")
    parser.add_argument("--category", default="Tech", help="Job category label")
    args = parser.parse_args()

    inserted = load_file(args.file, args.category)
    console.print(f"[green]Loaded {inserted} jobs[/green] (category: {args.category})")
