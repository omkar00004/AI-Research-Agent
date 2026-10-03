"""Judge prompts. Frozen before any evaluation run."""

NUGGET_GEN_SYSTEM = """You design evaluation checklists for research reports.
Given only a research topic, list the 8 to 12 key subtopics, facts or questions that a complete,
well-informed 800-1000 word report on that topic MUST cover. Each item must be specific, independently
checkable against a report's text, and non-overlapping. Do not assume access to any report.
Return JSON only: {"items": [{"id": 1, "item": "..."}, ...]}"""

NUGGET_GEN_USER = "Topic: {topic}"

NUGGET_JUDGE_SYSTEM = """You grade a research report against a checklist.
For each checklist item decide whether the report covers it: "covered" means the report explicitly addresses
the item with at least one substantive sentence (a passing keyword mention or a vague allusion is "not covered").
Judge only the report text. Do not give credit for information that is not in the report.
Return JSON only: {"marks": [{"id": 1, "covered": true, "evidence": "<short quote or empty>"}, ...]}
with exactly one entry per checklist item."""

NUGGET_JUDGE_USER = """Topic: {topic}

Checklist:
{checklist}

Report:
{report}"""

RUBRIC_USER = """Topic: {topic}

Report:
{report}"""

SUPPORT_SYSTEM = """You verify whether a cited web page supports a claim.
You are given a CLAIM taken from a report and an EXCERPT of the web page the claim cites.
Labels:
- "supported": the excerpt directly states or clearly entails the claim (including its specifics).
- "partially_supported": the excerpt supports part of the claim, or supports it only loosely/with different specifics.
- "not_supported": the excerpt does not support the claim, or contradicts it.
Judge only from the excerpt. Return JSON only: {"label": "supported|partially_supported|not_supported", "reason": "<one sentence>"}"""

SUPPORT_USER = """CLAIM: {claim}

CITED URL: {url}

EXCERPT:
{excerpt}"""
