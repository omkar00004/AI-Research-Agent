"""Metrics A-D for one generated report (judge calls + URL checks). Returns a JSON-serialisable artifact."""
from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor

import citations as C
import prompts as P
from common import CACHE, EVAL_DIR, SEED, JUDGE_MODEL, read_json, sha, write_json
from llmx import judge_call

RUBRIC_TEXT = (EVAL_DIR / "rubric.md").read_text()
RUBRIC_SYSTEM = ("You are a strict, calibrated evaluator of research reports. Score the report using the rubric below. "
                 "Do not reward length for its own sake.\n\n" + RUBRIC_TEXT)
DIMS = ["coverage", "depth", "evidence_use", "organization"]


def get_nuggets(subject: dict) -> dict:
    """Checklist of 8-12 items generated once per subject (blind to every report) and cached."""
    path = CACHE / "nuggets" / f"{subject['id']}.json"
    if path.exists():
        return read_json(path)
    rec = judge_call(P.NUGGET_GEN_SYSTEM, P.NUGGET_GEN_USER.format(topic=subject["topic"]))
    items = [{"id": int(x["id"]), "item": x["item"]} for x in (rec["parsed"] or {}).get("items", [])]
    if not 6 <= len(items) <= 14:
        raise RuntimeError(f"bad checklist for {subject['id']}: {len(items)} items")
    out = {"subject_id": subject["id"], "topic": subject["topic"], "items": items, "call": rec}
    write_json(path, out)
    return out


def nugget_score(subject: dict, report: str) -> dict:
    ck = get_nuggets(subject)
    checklist = "\n".join(f"{x['id']}. {x['item']}" for x in ck["items"])
    rec = judge_call(P.NUGGET_JUDGE_SYSTEM, P.NUGGET_JUDGE_USER.format(topic=subject["topic"], checklist=checklist, report=report))
    marks = {int(m["id"]): bool(m.get("covered")) for m in (rec["parsed"] or {}).get("marks", [])}
    covered = sum(1 for x in ck["items"] if marks.get(x["id"], False))
    return {"covered": covered, "total": len(ck["items"]), "score": covered / len(ck["items"]),
            "missing_marks": sum(1 for x in ck["items"] if x["id"] not in marks), "call": rec}


def rubric_score(subject: dict, report: str) -> dict:
    rec = judge_call(RUBRIC_SYSTEM, P.RUBRIC_USER.format(topic=subject["topic"], report=report))
    p = rec["parsed"] or {}
    scores = {}
    for d in DIMS:
        try:
            scores[d] = float(p[d]["score"])
        except Exception:
            scores[d] = None
    ok = all(v is not None for v in scores.values())
    return {"scores": scores, "overall": sum(scores.values()) / 4 if ok else None, "call": rec}


def citation_checks(report: str, retrieved_urls: list[str], workers: int = 8) -> dict:
    urls = C.extract_urls(report)
    with ThreadPoolExecutor(workers) as ex:
        fetched = list(ex.map(C.fetch_url, urls))
    per = [{"url": u, "status_code": f["status_code"], "status_class": f["status_class"],
            "error": f.get("error"), "env_blocked": f.get("env_blocked", False)} for u, f in zip(urls, fetched)]
    counts = {k: sum(1 for x in per if x["status_class"] == k) for k in ("reachable", "dead", "blocked")}
    prov = C.provenance(urls, retrieved_urls)
    return {"urls": per, "counts": counts, "total": len(urls), "provenance": prov}


def support_checks(subject_id: str, cond: str, run: int, report: str, k: int = 10) -> dict:
    pairs = C.claim_pairs(report)
    seed = int(sha(f"{SEED}|{subject_id}|{cond}|{run}")[:8], 16)
    sample = C.sample_pairs(pairs, k, seed)
    items = []

    def one(pr):
        f = C.fetch_url(pr["url"])
        text = f.get("text") or ""
        if f["status_class"] != "reachable" or len(text) < 200:
            return {**pr, "label": "unverifiable",
                    "why": f"fetch:{f['status_class']}:{f['status_code']}" if f["status_class"] != "reachable" else "no_text",
                    "excerpt": None}
        ex = C.best_excerpt(text, pr["claim"])
        rec = judge_call(P.SUPPORT_SYSTEM, P.SUPPORT_USER.format(claim=pr["claim"], url=pr["url"], excerpt=ex), max_tokens=2000)
        lab = (rec["parsed"] or {}).get("label")
        if lab not in ("supported", "partially_supported", "not_supported"):
            lab = "judge_error"
        return {**pr, "label": lab, "reason": (rec["parsed"] or {}).get("reason"), "excerpt": ex, "call": rec}

    with ThreadPoolExecutor(5) as ex_:
        items = list(ex_.map(one, sample))
    labs = [i["label"] for i in items]
    return {"n_claim_pairs_total": len(pairs), "sampled": len(items),
            "supported": labs.count("supported"), "partial": labs.count("partially_supported"),
            "not_supported": labs.count("not_supported"), "unverifiable": labs.count("unverifiable"),
            "judge_error": labs.count("judge_error"), "items": items}


def judge_report(subject: dict, cond: str, run: int, gen: dict) -> dict:
    report = gen["report"]
    retrieved = [r["url"] for r in gen.get("retrieved", [])]
    out = {"subject_id": subject["id"], "condition": cond, "run": run, "judge_model": JUDGE_MODEL,
           "nuggets": nugget_score(subject, report), "rubric": rubric_score(subject, report),
           "citations": citation_checks(report, retrieved),
           "support": support_checks(subject["id"], cond, run, report)}
    return out
