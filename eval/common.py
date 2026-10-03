"""Shared paths, config and small helpers for the Atlas benchmark."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Output dir can be redirected (dry run uses a separate dir)
OUT = Path(os.getenv("EVAL_OUT_DIR", EVAL_DIR / "outputs"))
CACHE = EVAL_DIR / "cache"
LOGS = EVAL_DIR / "logs"
RESULTS = Path(os.getenv("EVAL_RESULTS_DIR", ROOT / "results"))

JUDGE_MODEL = os.getenv("JUDGE_MODEL", "google/gemini-2.5-flash")  # OpenRouter slug; "groq/<id>" routes to Groq
SEED = 20260503

CONDITIONS = {
    "B0": "no-retrieval: one LLM call, no search",
    "B1": "single-pass: planner -> researcher -> writer (critic + retries disabled)",
    "B2": "Atlas full: planner -> researcher -> critic (<=2 retries) -> writer",
    "B2_r1": "Atlas with max retries = 1",
}

# USD per 1M tokens. Sources recorded in README.md. Estimates, not invoices.
PRICES = {
    "openai/gpt-oss-120b": {"in": 0.15, "out": 0.60},                 # Groq docs, fetched 2026-10-03
    "meta-llama/llama-3.3-70b-instruct": {"in": 0.10, "out": 0.32},   # OpenRouter listing, fetched 2026-10-03
    "google/gemini-2.5-flash": {"in": 0.30, "out": 2.50},             # OpenRouter listing (reasoning billed as output)
}
TAVILY_USD_PER_ADVANCED_SEARCH = 0.016  # 2 credits x $0.008 pay-as-you-go (assumption)

_lock = threading.Lock()

# Local override for secrets that must not live in the repo (eval/.env.local is gitignored).
# Unset Langfuse so benchmark runs never send traces to the production Langfuse project.
try:
    from dotenv import load_dotenv
    load_dotenv(EVAL_DIR / ".env.local", override=True)
except ImportError:
    pass
for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_k, None)



def load_subjects() -> dict:
    return json.loads((EVAL_DIR / "subjects.json").read_text())


def all_subjects() -> list[dict]:
    s = load_subjects()
    return [dict(x, set="dev") for x in s["dev"]] + [dict(x, set="test") for x in s["test"]]


def sha(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text())


def parse_json_loose(text: str):
    """Parse a JSON object/array from an LLM reply (handles code fences / prose)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", t, flags=re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError("no JSON found")


def est_cost(model: str, tin: int, tout: int) -> float:
    key = model.removeprefix("openrouter/")
    p = PRICES.get(key)
    if not p:
        return 0.0
    return tin * p["in"] / 1e6 + tout * p["out"] / 1e6
