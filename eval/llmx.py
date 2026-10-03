"""Judge LLM client (OpenRouter or Groq), temperature 0, with raw-call records."""
from __future__ import annotations

import os
import time

import httpx

from common import JUDGE_MODEL, est_cost, parse_json_loose

_OR = "https://openrouter.ai/api/v1/chat/completions"
_GROQ = "https://api.groq.com/openai/v1/chat/completions"


def judge_call(system: str, user: str, *, model: str | None = None, max_tokens: int = 8000, json_out: bool = True) -> dict:
    """One judge call. Returns dict(prompt, raw, parsed, model_requested, model_served, usage, cost, latency_s)."""
    model = model or JUDGE_MODEL
    if model.startswith("groq/"):
        url, key, mname = _GROQ, os.environ["GROQ_API_KEY"].split(",")[0].strip(), model[5:]
    else:
        url, key, mname = _OR, os.environ["OPENROUTER_API_KEY"].strip(), model
    body = {"model": mname, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if url == _OR:
        body["usage"] = {"include": True}
    last_err = None
    for attempt in range(6):
        t0 = time.time()
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:  # auth / bad request: do not retry
                raise PermissionError(f"HTTP {r.status_code}: {r.text[:200]}")
            d = r.json()
            if "error" in d:
                raise RuntimeError(str(d["error"])[:300])
            raw = d["choices"][0]["message"].get("content") or ""
            u = d.get("usage", {})
            tin, tout = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
            cost = u.get("cost")
            if cost is None:
                cost = est_cost(mname, tin, tout)
            rec = {"model_requested": model, "model_served": d.get("model"), "provider": d.get("provider"),
                   "system": system, "user": user, "raw": raw, "usage": {"in": tin, "out": tout},
                   "cost": cost, "latency_s": round(time.time() - t0, 2), "parsed": None}
            if json_out:
                try:
                    rec["parsed"] = parse_json_loose(raw)
                except Exception as e:  # retry once on unparseable output
                    last_err = e
                    if attempt >= 2:
                        rec["parse_error"] = str(e)
                        return rec
                    continue
            return rec
        except PermissionError:
            raise
        except Exception as e:
            last_err = e
            time.sleep(min(60, 2 ** attempt * 3))
    raise RuntimeError(f"judge_call failed: {last_err}")
