#!/usr/bin/env python3
"""Export 30 claim + cited-excerpt pairs from B2 test reports for manual labelling.

  python eval/human_check.py   ->  eval/human_check.csv (fill the 'human_label' column)
                                   eval/human_check_key.csv (judge labels; do NOT open before labelling)
Only pairs whose page was fetched (verifiable) are eligible. Fixed seed.
"""
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as K

N, COND = 30, "B2"
pool = []
for s in K.load_subjects()["test"]:
    for p in sorted((K.OUT / "judge" / COND).glob(f"{s['id']}_r*.json")):
        j = K.read_json(p)
        for i, it in enumerate(j["support"]["items"]):
            if it["label"] in ("supported", "partially_supported", "not_supported") and it.get("excerpt"):
                pool.append({"pair_id": f"{s['id']}_{p.stem.split('_')[-1]}_{i}", "subject_id": s["id"], "claim": it["claim"],
                             "url": it["url"], "excerpt": it["excerpt"], "judge_label": it["label"]})
random.Random(K.SEED).shuffle(pool)
sel = pool[:N]
out = K.EVAL_DIR
with open(out / "human_check.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["pair_id", "subject_id", "claim", "cited_url", "source_excerpt", "human_label (supported/partially/not)"])
    for r in sel:
        w.writerow([r["pair_id"], r["subject_id"], r["claim"], r["url"], r["excerpt"], ""])
with open(out / "human_check_key.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["pair_id", "judge_label"])
    for r in sel:
        w.writerow([r["pair_id"], r["judge_label"]])
print(f"eligible verifiable B2 test pairs: {len(pool)}; exported {len(sel)} to {out/'human_check.csv'}")
