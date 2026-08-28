"""Lightweight post-generation evaluation for Atlas reports.

Two automated checks run after the Writer produces a report:

1. **Citation coverage** — Extracts URLs from the report markdown, checks
   reachability via HTTP HEAD (5s timeout).
2. **Completeness score** — LLM-judge comparing the final report against
   the Planner's subtask list.

Results are saved as ``report_eval.json`` alongside each report.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from langchain_core.messages import SystemMessage, HumanMessage
from utils.llm import get_llm


# ---------------------------------------------------------------------------
# 1. Citation Coverage
# ---------------------------------------------------------------------------

def _extract_urls(text: str) -> list[str]:
    """Pull all http(s) URLs from markdown text."""
    pattern = r'https?://[^\s\)\]\>\"\'`]+'
    urls = re.findall(pattern, text)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        # Strip trailing punctuation that isn't part of the URL
        u = u.rstrip(".,;:!?")
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def check_citation_reachability(report_text: str) -> dict:
    """Check which cited URLs in the report are reachable.

    Uses synchronous ``httpx`` HEAD requests with a 5-second timeout.
    Does NOT fetch full content — just checks HTTP status.
    """
    urls = _extract_urls(report_text)
    reachable: list[str] = []
    unreachable: list[str] = []

    with httpx.Client(timeout=5.0, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = client.head(url)
                if resp.status_code < 400:
                    reachable.append(url)
                else:
                    # Try GET as fallback — some servers reject HEAD
                    try:
                        resp2 = client.get(url, follow_redirects=True)
                        if resp2.status_code < 400:
                            reachable.append(url)
                        else:
                            unreachable.append(url)
                    except Exception:
                        unreachable.append(url)
            except Exception:
                unreachable.append(url)

    total = len(urls)
    return {
        "total_urls": total,
        "reachable": len(reachable),
        "unreachable": unreachable,
        "coverage_pct": round((len(reachable) / total) * 100, 1) if total > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# 2. Completeness Score (LLM-judge)
# ---------------------------------------------------------------------------

def score_completeness(
    report_text: str,
    subtasks: list[str],
    topic: str,
) -> dict:
    """Use an LLM judge to score how well the report covers the planned subtasks.

    Returns a dict with ``score`` (1-10), ``justification``,
    ``subtasks_covered``, and ``subtasks_missing``.
    """
    llm = get_llm(role="critic", temperature=0.2)

    subtask_list = "\n".join([f"  {i+1}. {s}" for i, s in enumerate(subtasks)])

    system = SystemMessage(content="""You are a strict research report evaluator.
Given a research topic, a list of planned subtasks, and the final report,
evaluate how completely the report addresses each subtask.

Respond with ONLY a JSON object:
{
  "score": <1-10>,
  "justification": "<2-3 sentences explaining the score>",
  "subtasks_covered": ["<subtask text>", ...],
  "subtasks_missing": ["<subtask text>", ...]
}

Scoring guide:
- 10: Every subtask is thoroughly addressed with evidence and citations
- 7-9: Most subtasks covered well, minor gaps
- 4-6: Significant gaps, some subtasks poorly covered
- 1-3: Most subtasks missing or barely mentioned""")

    human = HumanMessage(content=f"""Topic: {topic}

Planned Subtasks:
{subtask_list}

Final Report:
{report_text[:6000]}

Evaluate completeness:""")

    response = llm.invoke([system, human])

    try:
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = json.loads(content.strip())
        return {
            "score": result.get("score", 0),
            "justification": result.get("justification", ""),
            "subtasks_covered": result.get("subtasks_covered", []),
            "subtasks_missing": result.get("subtasks_missing", []),
        }
    except Exception as e:
        return {
            "score": 0,
            "justification": f"Failed to parse LLM judge response: {e}",
            "subtasks_covered": [],
            "subtasks_missing": subtasks,
        }


# ---------------------------------------------------------------------------
# Combined Evaluation
# ---------------------------------------------------------------------------

def run_evaluation(
    report_id: str,
    report_text: str,
    subtasks: list[str],
    topic: str,
    output_dir: str | Path | None = None,
) -> dict:
    """Run all evaluation checks and save results.

    Args:
        report_id: Unique identifier for this report run.
        report_text: The final markdown report from the Writer.
        subtasks: The subtask list from the Planner.
        topic: The original research topic.
        output_dir: Directory to save ``report_eval.json``. Defaults to cwd.

    Returns:
        The complete evaluation results dict.
    """
    citation = check_citation_reachability(report_text)
    completeness = score_completeness(report_text, subtasks, topic)

    eval_result = {
        "report_id": report_id,
        "topic": topic,
        "citation_coverage": citation,
        "completeness": completeness,
    }

    # Save to file
    out = Path(output_dir) if output_dir else Path(".")
    out.mkdir(parents=True, exist_ok=True)
    eval_path = out / f"report_eval_{report_id}.json"
    eval_path.write_text(json.dumps(eval_result, indent=2, ensure_ascii=False))

    return eval_result
