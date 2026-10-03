#!/usr/bin/env python3
"""Agreement between your labels (eval/human_check.csv) and the judge (eval/human_check_key.csv).

  python eval/agreement.py [human_check.csv] [human_check_key.csv]
Reports percent agreement and Cohen's kappa on the 3-class labels (supported / partially / not)
and on the binary collapse (supported vs not-fully-supported).
"""
import csv
import sys
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parent
human_p = Path(sys.argv[1]) if len(sys.argv) > 1 else D / "human_check.csv"
key_p = Path(sys.argv[2]) if len(sys.argv) > 2 else D / "human_check_key.csv"


def norm(x: str) -> str | None:
    x = (x or "").strip().lower()
    if x.startswith("supp"):
        return "supported"
    if x.startswith("part"):
        return "partially"
    if x.startswith("not") or x.startswith("unsupp"):
        return "not"
    return None


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n ** 2
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


key = {r["pair_id"]: norm(r["judge_label"]) for r in csv.DictReader(open(key_p))}
h, j, skipped = [], [], 0
for r in csv.DictReader(open(human_p)):
    lab = norm(r[[c for c in r if c.startswith("human_label")][0]])
    if lab is None or key.get(r["pair_id"]) is None:
        skipped += 1
        continue
    h.append(lab)
    j.append(key[r["pair_id"]])
if not h:
    sys.exit("no labelled rows found")
print(f"labelled pairs: {len(h)} (skipped/unlabelled: {skipped})")
print(f"3-class: agreement {sum(x == y for x, y in zip(h, j)) / len(h):.1%}, Cohen's kappa {kappa(h, j):.3f}")
hb = ["supported" if x == "supported" else "other" for x in h]
jb = ["supported" if x == "supported" else "other" for x in j]
print(f"binary (supported vs other): agreement {sum(x == y for x, y in zip(hb, jb)) / len(hb):.1%}, Cohen's kappa {kappa(hb, jb):.3f}")
print("human label counts:", dict(Counter(h)), "| judge label counts:", dict(Counter(j)))
print("N is small: treat kappa as indicative.")
