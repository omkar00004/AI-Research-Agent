"""Citation extraction, URL validity (reachable/dead/blocked), provenance, claim sampling, excerpts."""
from __future__ import annotations

import io
import random
import re
import threading
import time
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

from common import CACHE, sha, read_json, write_json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
BLOCKED_CODES = {403, 429}      # frozen: only these are 'blocked'; every other 4xx/5xx/timeout/DNS is 'dead'
MAX_BYTES = 3_000_000
TIMEOUT = 15.0

_URL_RE = re.compile(r"https?://[^\s<>\"'`\]\)【】]+")


# ---------------------------------------------------------------- extraction
def _md_links(text: str) -> list[tuple[str, str, int, int]]:
    """[label](url) with balanced parentheses inside the URL. Returns (label, url, start, end)."""
    out = []
    for m in re.finditer(r"\[([^\]]*)\]\((https?://)", text):
        i = m.end() - len(m.group(2))
        depth, j = 1, m.end()
        while j < len(text) and depth:
            c = text[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c.isspace():
                break
            j += 1
        if depth == 0:
            out.append((m.group(1), text[i:j - 1], m.start(), j))
    return out


def _clean(u: str) -> str:
    return u.rstrip(".,;:!?*_")


def extract_urls(text: str) -> list[str]:
    """All distinct cited URLs in order of first appearance (markdown links + bare URLs)."""
    spans = _md_links(text)
    found = [(s, _clean(u)) for _, u, s, _ in spans]
    masked = text
    for _, _, s, e in reversed(spans):
        masked = masked[:s] + " " * (e - s) + masked[e:]
    found += [(m.start(), _clean(m.group(0))) for m in _URL_RE.finditer(masked)]
    seen, urls = set(), []
    for _, u in sorted(found):
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def norm_url(u: str) -> str:
    try:
        p = urlsplit(u.strip())
        host = p.netloc.lower().removeprefix("www.")
        q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
        path = p.path.rstrip("/") or ""
        return urlunsplit((p.scheme.lower(), host, path, urlencode(q), ""))
    except Exception:
        return u


def provenance(cited: list[str], retrieved: list[str]) -> dict:
    ret = {norm_url(u) for u in retrieved}
    in_ret = [u for u in cited if norm_url(u) in ret]
    return {"cited": len(cited), "in_retrieved": len(in_ret),
            "not_in_retrieved": [u for u in cited if norm_url(u) not in ret]}


# ---------------------------------------------------------------- fetching
_domain_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_domain_last: dict[str, float] = {}
_cache_lock = threading.Lock()


def _classify(code: int | None, err: str | None) -> str:
    if err or code is None:
        return "dead"
    if 200 <= code < 400:
        return "reachable"
    if code in BLOCKED_CODES:
        return "blocked"
    return "dead"


def _extract_text(content: bytes, ctype: str) -> str:
    ctype = (ctype or "").lower()
    try:
        if "pdf" in ctype or content[:5] == b"%PDF-":
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(content))
            return "\n".join((pg.extract_text() or "") for pg in rd.pages[:15])[:150_000]
        if "html" in ctype or content.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            for t in soup(["script", "style", "noscript", "nav", "footer", "header", "svg", "form"]):
                t.decompose()
            return re.sub(r"\s+", " ", soup.get_text(" ")).strip()[:150_000]
        if ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
            return re.sub(r"\s+", " ", content.decode("utf-8", "ignore")).strip()[:150_000]
    except Exception:
        return ""
    return ""


def _curl_get(url: str) -> dict:
    """One GET via curl (Wikimedia and others reject the python-httpx TLS fingerprint but accept curl)."""
    import subprocess
    import tempfile
    rec = {"status_code": None, "final_url": None, "error": None, "content_type": None, "text": ""}
    with tempfile.NamedTemporaryFile() as body:
        cmd = ["curl", "-sS", "-L", "--max-redirs", "8", "-m", str(int(TIMEOUT)), "--connect-timeout", "10",
               "--max-filesize", str(MAX_BYTES), "--compressed", "-A", UA,
               "-H", "Accept: text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
               "-H", "Accept-Language: en-US,en;q=0.9", "-o", body.name,
               "-w", "%{http_code}\t%{url_effective}\t%{content_type}", url]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 10)
        out = (p.stdout or "").split("\t")
        code = int(out[0]) if out and out[0].isdigit() and int(out[0]) > 0 else None
        if p.returncode not in (0, 63) and code is None:      # 63 = max-filesize reached (keep what we got)
            err = (p.stderr or "").strip().splitlines()[-1:] or [f"curl exit {p.returncode}"]
            rec["error"] = err[0][:160]
            return rec
        rec.update(status_code=code, final_url=out[1] if len(out) > 1 else None,
                   content_type=out[2] if len(out) > 2 else None)
        if code and code < 400:
            rec["text"] = _extract_text(open(body.name, "rb").read(), rec["content_type"])
    return rec


def fetch_url(url: str) -> dict:
    """GET with browser-like UA, redirects, timeout, one retry. Cached on disk by URL."""
    path = CACHE / "fetch" / f"{sha(url)}.json"
    if path.exists():
        return read_json(path)
    host = urlsplit(url).netloc.lower()
    rec = {"url": url}
    for attempt in range(2):
        with _domain_locks[host]:                       # at most one in-flight request per domain
            wait = 0.4 - (time.time() - _domain_last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            _domain_last[host] = time.time()
            try:
                rec = {"url": url, **_curl_get(url)}
            except Exception as e:
                rec = {"url": url, "status_code": None, "final_url": None, "content_type": None, "text": "",
                       "error": f"{type(e).__name__}: {str(e)[:120]}"}
        code = rec["status_code"]
        retryable = rec["error"] is not None or (code is not None and (code >= 500 or code == 429))
        if "CONNECT tunnel failed" in (rec["error"] or ""):
            retryable = False
        if not retryable:
            break
        time.sleep(1.5)
    rec["status_class"] = _classify(rec["status_code"], rec["error"])
    if "CONNECT tunnel failed, response 403" in (rec["error"] or ""):
        rec["status_class"] = "blocked"                 # sandbox egress policy, not the site: excluded like 403
        rec["env_blocked"] = True
    with _cache_lock:
        write_json(path, rec)
    return rec


# ---------------------------------------------------------------- claims
def claim_pairs(report: str) -> list[dict]:
    """(claim sentence, cited URL) pairs: every sentence that carries at least one URL."""
    text = re.sub(r"```.*?```", " ", report, flags=re.S)       # drop mermaid / code
    pairs, seen = [], set()
    for para in re.split(r"\n+", text):
        if para.lstrip().startswith("#"):
            continue
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*\[(\"'])", para.strip()):
            urls = extract_urls(sent)
            if not urls:
                continue
            clean = sent
            for lab, u, s, e in reversed(_md_links(sent)):
                clean = clean[:s] + (lab if lab.strip() and not lab.startswith("http") else "") + clean[e:]
            clean = _URL_RE.sub("", clean)
            clean = re.sub(r"[【】]|\(\s*\)|\s+", " ", clean).strip(" -*•\t")
            if len(clean.split()) < 6:
                continue
            for u in urls:
                k = (clean, u)
                if k not in seen:
                    seen.add(k)
                    pairs.append({"claim": clean, "url": u})
    return pairs


def sample_pairs(pairs: list[dict], k: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(pairs)), min(k, len(pairs))))
    return [pairs[i] for i in idx]


_STOP = set("the a an of and or to in on for with by is are was were be been that this it as at from has have had their its into than not but which also more most".split())


def best_excerpt(text: str, claim: str, max_chars: int = 3000, win: int = 900) -> str:
    """Top-overlap windows of the page text (kept in page order), up to max_chars."""
    if len(text) <= max_chars:
        return text
    toks = {w for w in re.findall(r"[a-z0-9]+", claim.lower()) if w not in _STOP and len(w) > 2}
    step = win // 2
    wins = []
    for i in range(0, max(1, len(text) - step), step):
        seg = text[i:i + win]
        sc = len(toks & set(re.findall(r"[a-z0-9]+", seg.lower())))
        wins.append((sc, i))
    top = sorted(sorted(wins, key=lambda x: (-x[0], x[1]))[: max(1, max_chars // win)], key=lambda x: x[1])
    return " … ".join(text[i:i + win] for _, i in top)[:max_chars + 200]
