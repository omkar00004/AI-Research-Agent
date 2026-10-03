#!/usr/bin/env python3
"""Resumable Atlas benchmark runner.

  python eval/run_eval.py --stage gen|judge|aggregate|all [--runs N] [--sets dev,test]
         [--conditions B0,B1,B2,B2_r1] [--ids a,b] [--workers N] [--dry-run]

Every (condition, subject, run) artifact is written on completion; reruns skip finished ones.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ap = argparse.ArgumentParser()
ap.add_argument("--stage", default="all")
ap.add_argument("--runs", type=int, default=1)
ap.add_argument("--sets", default="dev,test")
ap.add_argument("--conditions", default="B0,B1,B2,B2_r1")
ap.add_argument("--ids", default="")
ap.add_argument("--gen-workers", type=int, default=3)
ap.add_argument("--judge-workers", type=int, default=4)
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()
if args.dry_run:
    os.environ.setdefault("EVAL_OUT_DIR", str(Path(__file__).resolve().parent / "outputs_dryrun"))
    os.environ.setdefault("EVAL_RESULTS_DIR", str(Path(__file__).resolve().parent / "outputs_dryrun" / "results"))

import common as K  # noqa: E402  (after env is set)

_print_lock = threading.Lock()


def log(msg: str):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with _print_lock:
        print(line, flush=True)


def selected():
    subs = [s for s in K.all_subjects() if s["set"] in args.sets.split(",")]
    if args.ids:
        want = set(args.ids.split(","))
        subs = [s for s in subs if s["id"] in want]
    return subs


def jobs(subs):
    for cond in args.conditions.split(","):
        for s in subs:
            for r in range(args.runs):
                yield cond, s, r


def gen_path(c, s, r):
    return K.OUT / "gen" / c / f"{s['id']}_r{r}.json"


def judge_path(c, s, r):
    return K.OUT / "judge" / c / f"{s['id']}_r{r}.json"


def do_gen(cond, s, r):
    import gen
    last = None
    for attempt in range(3):
        try:
            out = gen.run_condition(s["topic"], cond)
            out.update(subject_id=s["id"], condition=cond, run=r, topic=s["topic"], set=s["set"])
            K.write_json(gen_path(cond, s, r), out)
            return out
        except Exception as e:
            last = e
            log(f"  gen retry {cond} {s['id']} r{r}: {type(e).__name__}: {str(e)[:150]}")
            time.sleep(10 * (attempt + 1))
    K.write_json(K.OUT / "failed" / f"gen_{cond}_{s['id']}_r{r}.json", {"error": repr(last), "trace": traceback.format_exc()})
    raise last


def do_judge(cond, s, r):
    import judge
    g = K.read_json(gen_path(cond, s, r))
    try:
        out = judge.judge_report(s, cond, r, g)
    except Exception as e:
        K.write_json(K.OUT / "failed" / f"judge_{cond}_{s['id']}_r{r}.json", {"error": repr(e), "trace": traceback.format_exc()})
        raise
    K.write_json(judge_path(cond, s, r), out)
    return out


def run_stage(name, fn, pathfn, workers, todo):
    todo = [(c, s, r) for c, s, r in todo if not pathfn(c, s, r).exists()]
    if name == "judge":
        todo = [(c, s, r) for c, s, r in todo if gen_path(c, s, r).exists()]
    log(f"[{name}] {len(todo)} pending")
    done = fail = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fn, c, s, r): (c, s, r) for c, s, r in todo}
        for f in as_completed(futs):
            c, s, r = futs[f]
            try:
                f.result()
                done += 1
                log(f"[{name}] ok {c} {s['id']} r{r}  ({done + fail}/{len(todo)})")
            except Exception as e:
                fail += 1
                log(f"[{name}] FAIL {c} {s['id']} r{r}: {type(e).__name__}: {str(e)[:200]}")
    log(f"[{name}] finished: {done} ok, {fail} failed")


def main():
    subs = selected()
    log(f"subjects={len(subs)} conditions={args.conditions} runs={args.runs} out={K.OUT} judge={K.JUDGE_MODEL}")
    todo = list(jobs(subs))
    if args.stage in ("gen", "all"):
        # nugget checklists first so every condition is judged against the same list
        run_stage("gen", do_gen, gen_path, args.gen_workers, todo)
    if args.stage in ("judge", "all"):
        import judge
        for s in subs:
            judge.get_nuggets(s)
        run_stage("judge", do_judge, judge_path, args.judge_workers, todo)
    if args.stage in ("aggregate", "all"):
        import aggregate
        aggregate.main(subs, args.conditions.split(","), args.runs)


if __name__ == "__main__":
    main()
