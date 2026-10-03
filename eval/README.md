# Atlas benchmark (`eval/`)

Automated, reproducible evaluation of Atlas against two ablations. Everything new lives under `eval/`. Atlas's default behavior is unchanged.

## One-command reproduction

```bash
pip install -r requirements.txt numpy beautifulsoup4 pypdf
# keys: GROQ_API_KEY, TAVILY_API_KEY, OPENROUTER_API_KEY (env vars, or eval/.env.local which is gitignored)
python eval/run_eval.py --stage all --runs 1          # resumable; add --dry-run --ids dev07,dev05 for a 2-subject trial
python eval/human_check.py                            # exports the 30-pair manual-label sheet
python eval/agreement.py                              # after you fill eval/human_check.csv
```

Outputs: `results/summary.json`, `results/results.md`, `results/per_run.csv`, `results/per_subject.csv`.
Raw per-run outputs (reports, retrieved sources, every judge prompt and raw reply) are written to `eval/outputs/` (gitignored; a compressed copy is in `results/raw_outputs.tar.gz` if it fit in the repo).
The runner skips completed (condition, subject, run) artifacts, so an interrupted run just continues when relaunched.

## Atlas edits (all default-off)

| Switch | Effect | Default |
|---|---|---|
| `ATLAS_ENABLE_CRITIC=0` (`config.ENABLE_CRITIC`) | graph becomes planner -> researcher -> writer (no gap evaluation, no retries) | on |
| `ATLAS_MAX_RETRIES=n` (`config.MAX_RETRIES`) | cap on Critic -> Researcher retries | 2 |
| `ATLAS_CITE_MODE=1` (`config.CITE_MODE`) | researcher and writer prompts get an inline-citation instruction, and the writer is given the URL list | off |

Files touched: `config.py`, `agents/graph.py`, `agents/researcher.py`, `agents/writer.py`.

## Conditions

All conditions use the same models (planner/researcher/critic: Groq `openai/gpt-oss-120b`; writer: OpenRouter `meta-llama/llama-3.3-70b-instruct`, i.e. `config.MODEL_CONFIG`) and the same inline-citation instruction.

- **B0 no-retrieval**: one writer-model call with the writer's system prompt, no search, asked to write the cited report from its own knowledge.
- **B1 single-pass**: planner -> researcher (Tavily search + synthesis) -> writer. Critic and retry loop removed.
- **B2 Atlas full**: planner -> researcher -> critic -> (retry researcher up to 2x) -> writer.
- **B2_r1**: as B2 with at most 1 retry.

Search results are cached by (query, params, occurrence index within the run). Replaying a run gives identical search results. A retry that re-issues the same query inside one run gets its own live call, as in real Atlas.

## Metrics

- **A. Nugget coverage (headline).** The judge writes an 8-12 item checklist per subject from the topic alone, once, cached in `eval/cache/nuggets/`. It then marks each item covered or not covered per report. Score = covered / total.
- **B. Holistic rubric.** 1-10 on coverage, depth, evidence use and organization. Anchors are in `eval/rubric.md`. Score is the mean of the four dimensions.
- **C. Citation validity.** Every cited URL (markdown links and bare URLs) is fetched with `curl` (browser-like UA, redirects, 15 s timeout, one retry). Reachable = 2xx/3xx after redirects. **Blocked = 403 or 429** (reported separately and excluded from the denominator). Dead = everything else (404, 410, other 4xx, 5xx, timeout, DNS or connection error). Provenance = fraction of cited URLs that appear in the run's retrieved search results (URLs compared after dropping `www.`, fragments, trailing slashes and tracking params). For B0, provenance is 0 by construction because nothing is retrieved.
- **D. Citation support.** Sentences that carry a URL are paired with each cited URL. Up to 10 pairs per report are sampled with a fixed seed. The page text is fetched, the 3,000 characters most lexically similar to the claim are given to the judge, and the judge labels the pair supported / partially supported / not supported. A pair is unverifiable if the page could not be fetched (blocked, dead, no text). Strict support rate = supported / verifiable. Lenient = (supported + partial) / verifiable.
- **E. Cost and latency.** LLM cost = tokens x list price (`eval/common.py::PRICES`). Search cost = $0.016 per advanced Tavily search (assumption: 2 credits at $0.008). Both are estimates, not invoices. Latency is measured wall time. Because search results are cached, `latency_uncached_s` adds back the original latency of cached searches.

Judge: `JUDGE_MODEL` (default `google/gemini-2.5-flash` via OpenRouter, temperature 0). The exact served model string is stored in every judge record. The generators are from the OpenAI (gpt-oss) and Meta (Llama) families, so the judge is from a different family.

## Statistics

Mean with 95% percentile bootstrap CI over subjects (10,000 resamples, fixed seed). Runs of the same subject are averaged first. Paired bootstrap CIs of differences for B2-B1 and B1-B0 (and B2_r1-B1, B2-B2_r1). No p-values. N is small (24 test subjects, 1 run each by default), so CIs are wide and indicative.

## Assumptions and limitations

Filled in at the end of the run; see the PASTE-BACK SUMMARY in the run log and `results/results.md`.
