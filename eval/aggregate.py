"""Aggregate artifacts into results/summary.json, results.md, per_run.csv, per_subject.csv.

Stats: per-condition mean with 95% percentile bootstrap CI over subjects (10,000 resamples, fixed seed);
paired bootstrap CI of the difference (B2-B1, B1-B0, and B2_r1-B1 if present) over subjects with both values.
Runs of the same subject are averaged first, so the unit of resampling is the subject.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import common as K

N_BOOT = 10_000
METRICS = ["nugget", "rubric", "rub_coverage", "rub_depth", "rub_evidence", "rub_organization",
           "cite_reach_rate", "cite_prov_rate", "supp_rate_strict", "supp_rate_lenient", "supp_unverif_share",
           "cost_usd", "latency_s", "latency_uncached_s", "n_search", "retry_loops", "n_urls"]


def boot_ci(vals, seed=K.SEED):
    v = np.array([x for x in vals if x is not None and not np.isnan(x)], float)
    if len(v) == 0:
        return None
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, len(v), (N_BOOT, len(v)))].mean(1)
    return {"mean": float(v.mean()), "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5)), "n": int(len(v))}


def paired_ci(a: dict, b: dict, seed=K.SEED):
    ids = sorted(set(a) & set(b))
    d = np.array([a[i] - b[i] for i in ids if a[i] is not None and b[i] is not None], float)
    if len(d) == 0:
        return None
    rng = np.random.default_rng(seed)
    means = d[rng.integers(0, len(d), (N_BOOT, len(d)))].mean(1)
    return {"mean_diff": float(d.mean()), "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5)), "n": int(len(d))}


def run_row(g: dict, j: dict) -> dict:
    cc = j["citations"]["counts"]
    den = cc["reachable"] + cc["dead"]
    prov = j["citations"]["provenance"]
    sp = j["support"]
    ver = sp["supported"] + sp["partial"] + sp["not_supported"]
    return {
        "subject_id": g["subject_id"], "condition": g["condition"], "run": g["run"], "set": g["set"],
        "nugget": j["nuggets"]["score"], "nugget_covered": j["nuggets"]["covered"], "nugget_total": j["nuggets"]["total"],
        "rubric": j["rubric"]["overall"],
        "rub_coverage": j["rubric"]["scores"]["coverage"], "rub_depth": j["rubric"]["scores"]["depth"],
        "rub_evidence": j["rubric"]["scores"]["evidence_use"], "rub_organization": j["rubric"]["scores"]["organization"],
        "n_urls": j["citations"]["total"], "reachable": cc["reachable"], "dead": cc["dead"], "blocked": cc["blocked"],
        "cite_reach_rate": cc["reachable"] / den if den else None,
        "not_in_retrieved": len(prov["not_in_retrieved"]),
        "cite_prov_rate": prov["in_retrieved"] / prov["cited"] if prov["cited"] else None,
        "claim_pairs": sp["n_claim_pairs_total"], "sampled": sp["sampled"], "supported": sp["supported"],
        "partial": sp["partial"], "not_supported": sp["not_supported"], "unverifiable": sp["unverifiable"],
        "judge_error": sp["judge_error"],
        "supp_rate_strict": sp["supported"] / ver if ver else None,
        "supp_rate_lenient": (sp["supported"] + sp["partial"]) / ver if ver else None,
        "supp_unverif_share": sp["unverifiable"] / sp["sampled"] if sp["sampled"] else None,
        "cost_usd": g["total_cost_usd"], "llm_cost_usd": g["llm_cost_usd"], "search_cost_usd": g["search_cost_usd"],
        "latency_s": g["latency_s"], "latency_uncached_s": g.get("latency_uncached_est_s", g["latency_s"]),
        "tokens_in": g["tokens"]["in"], "tokens_out": g["tokens"]["out"],
        "n_search": g["n_search_calls"], "retry_loops": g["retry_loops"],
        "words": len(g["report"].split()),
        "judge_cost_usd": j["nuggets"]["call"]["cost"] + j["rubric"]["call"]["cost"]
        + sum(i["call"]["cost"] for i in j["support"]["items"] if "call" in i),
    }


def load_rows(subs, conds, runs):
    rows, missing = [], []
    for c in conds:
        for s in subs:
            for r in range(runs):
                gp, jp = K.OUT / "gen" / c / f"{s['id']}_r{r}.json", K.OUT / "judge" / c / f"{s['id']}_r{r}.json"
                if gp.exists() and jp.exists():
                    rows.append(run_row(K.read_json(gp), K.read_json(jp)))
                else:
                    missing.append({"condition": c, "subject_id": s["id"], "run": r,
                                    "stage_missing": "gen" if not gp.exists() else "judge"})
    return rows, missing


def per_subject(rows, cond, sset):
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["condition"] == cond and r["set"] == sset:
            for m in METRICS:
                if r.get(m) is not None:
                    by[r["subject_id"]][m].append(r[m])
    return {sid: {m: float(np.mean(v)) for m, v in d.items()} for sid, d in by.items()}


def pooled(rows, cond, sset):
    rs = [r for r in rows if r["condition"] == cond and r["set"] == sset]
    s = lambda k: int(sum(r[k] for r in rs))
    out = {"reports": len(rs), "urls": s("n_urls"), "reachable": s("reachable"), "dead": s("dead"), "blocked": s("blocked"),
           "not_in_retrieved": s("not_in_retrieved"), "claim_pairs_sampled": s("sampled"), "supported": s("supported"),
           "partial": s("partial"), "not_supported": s("not_supported"), "unverifiable": s("unverifiable"),
           "judge_error": s("judge_error"), "reports_without_urls": sum(1 for r in rs if r["n_urls"] == 0)}
    den = out["reachable"] + out["dead"]
    out["reach_rate_pooled"] = out["reachable"] / den if den else None
    ver = out["supported"] + out["partial"] + out["not_supported"]
    out["verifiable_pairs"] = ver
    out["support_strict_pooled"] = out["supported"] / ver if ver else None
    out["support_lenient_pooled"] = (out["supported"] + out["partial"]) / ver if ver else None
    out["unverifiable_share_pooled"] = out["unverifiable"] / out["claim_pairs_sampled"] if out["claim_pairs_sampled"] else None
    out["prov_rate_pooled"] = (out["urls"] - out["not_in_retrieved"]) / out["urls"] if out["urls"] else None
    out["total_cost_usd"] = sum(r["cost_usd"] for r in rs)
    out["judge_cost_usd"] = sum(r["judge_cost_usd"] for r in rs)
    return out


def main(subs, conds, runs):
    rows, missing = load_rows(subs, conds, runs)
    K.RESULTS.mkdir(parents=True, exist_ok=True)
    summary = {"conditions": {c: K.CONDITIONS[c] for c in conds}, "judge_model": K.JUDGE_MODEL, "runs_per_condition": runs,
               "n_runs_scored": len(rows), "missing": missing, "bootstrap": {"resamples": N_BOOT, "seed": K.SEED},
               "sets": {}}
    for sset in ("test", "dev"):
        ids = [s["id"] for s in subs if s["set"] == sset]
        if not ids:
            continue
        block = {"n_subjects": len(ids), "conditions": {}, "paired": {}}
        ps = {c: per_subject(rows, c, sset) for c in conds}
        for c in conds:
            block["conditions"][c] = {m: boot_ci([ps[c][i].get(m) for i in ps[c]]) for m in METRICS}
            block["conditions"][c]["pooled"] = pooled(rows, c, sset)
        for a, b in (("B2", "B1"), ("B1", "B0"), ("B2_r1", "B1"), ("B2", "B2_r1")):
            if a in conds and b in conds:
                block["paired"][f"{a}-{b}"] = {m: paired_ci({i: ps[a][i].get(m) for i in ps[a] if ps[a][i].get(m) is not None},
                                                             {i: ps[b][i].get(m) for i in ps[b] if ps[b][i].get(m) is not None})
                                               for m in ("nugget", "rubric", "cite_reach_rate", "supp_rate_strict", "cost_usd", "latency_s")}
        summary["sets"][sset] = block
    K.write_json(K.RESULTS / "summary.json", summary)
    if rows:
        with open(K.RESULTS / "per_run.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        # per-subject CSV: one row per subject x condition (runs averaged)
        with open(K.RESULTS / "per_subject.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["set", "subject_id", "condition"] + METRICS)
            for sset in ("test", "dev"):
                for c in conds:
                    for sid, d in sorted(per_subject(rows, c, sset).items()):
                        w.writerow([sset, sid, c] + [round(d[m], 4) if m in d else "" for m in METRICS])
    (K.RESULTS / "results.md").write_text(render_md(summary, conds))
    print(f"wrote {K.RESULTS}/summary.json, results.md, per_run.csv, per_subject.csv  (scored runs: {len(rows)}, missing: {len(missing)})")


def fmt(x, pct=False, nd=2):
    if x is None:
        return "n/a"
    v = x["mean"] * (100 if pct else 1)
    lo, hi = x["lo"] * (100 if pct else 1), x["hi"] * (100 if pct else 1)
    nd = 1 if pct else nd
    return f"{v:.{nd}f}{'%' if pct else ''} [{lo:.{nd}f}, {hi:.{nd}f}]"


def render_md(summary, conds):
    L = ["# Atlas benchmark results", "",
         f"Judge: `{summary['judge_model']}`. Runs per condition per subject: {summary['runs_per_condition']}. "
         "Means with 95% bootstrap CIs over subjects (N is small; treat CIs as indicative).", ""]
    rows = [("Nugget coverage (A)", "nugget", True), ("Holistic rubric /10 (B)", "rubric", False),
            ("  coverage", "rub_coverage", False), ("  depth", "rub_depth", False), ("  evidence use", "rub_evidence", False),
            ("  organization", "rub_organization", False),
            ("Citation reachability (C), per-subject", "cite_reach_rate", True),
            ("Citation provenance: cited URL was retrieved (C)", "cite_prov_rate", True),
            ("Support rate, strict (D)", "supp_rate_strict", True), ("Support rate, lenient (D)", "supp_rate_lenient", True),
            ("Unverifiable share of sampled claims (D)", "supp_unverif_share", True),
            ("URLs cited / report", "n_urls", False), ("Search calls / report", "n_search", False),
            ("Retry loops / report", "retry_loops", False), ("Est. cost USD / report (E)", "cost_usd", False),
            ("Latency s / report, measured (E)", "latency_s", False), ("Latency s / report, searches uncached (E)", "latency_uncached_s", False)]
    for sset, title in (("test", "Test set (headline)"), ("dev", "Dev set (subjects used during development)")):
        b = summary["sets"].get(sset)
        if not b:
            continue
        L += [f"## {title}: {b['n_subjects']} subjects", "", "| Metric | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
        for name, key, pct in rows:
            nd = 1 if key in ("n_urls", "n_search", "retry_loops", "latency_s", "latency_uncached_s") else (4 if key == "cost_usd" else 2)
            L.append(f"| {name} | " + " | ".join(fmt(b["conditions"][c][key], pct, nd) for c in conds) + " |")
        L += ["", "Paired differences (mean diff [95% CI], subjects with both values):", "",
              "| Comparison | Nugget (pts) | Rubric | Reachability (pts) | Support strict (pts) | Cost USD | Latency s |", "|---|---|---|---|---|---|---|"]
        for k, d in b["paired"].items():
            def pf(m, scale=1.0, nd=2):
                x = d.get(m)
                return "n/a" if not x else f"{x['mean_diff']*scale:+.{nd}f} [{x['lo']*scale:+.{nd}f}, {x['hi']*scale:+.{nd}f}] (n={x['n']})"
            L.append(f"| {k} | {pf('nugget',100,1)} | {pf('rubric')} | {pf('cite_reach_rate',100,1)} | {pf('supp_rate_strict',100,1)} | {pf('cost_usd',1,4)} | {pf('latency_s',1,1)} |")
        L += ["", "Pooled citation counts (all reports in the set):", "",
              "| Condition | Reports | URLs | Reachable | Dead | Blocked | Not in retrieved | Verifiable pairs | Supported | Partial | Not supported | Unverifiable |", "|---|" + "---|" * 11]
        for c in conds:
            p = b["conditions"][c]["pooled"]
            L.append(f"| {c} | {p['reports']} | {p['urls']} | {p['reachable']} | {p['dead']} | {p['blocked']} | {p['not_in_retrieved']} | "
                     f"{p['verifiable_pairs']} | {p['supported']} | {p['partial']} | {p['not_supported']} | {p['unverifiable']} |")
        L.append("")
    if summary["missing"]:
        L += [f"**Missing / failed runs: {len(summary['missing'])}** (excluded from all statistics; see summary.json).", ""]
    return "\n".join(L)
