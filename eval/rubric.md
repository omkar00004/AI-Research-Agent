# Holistic rubric (frozen before any evaluation run)

The judge scores each report on four dimensions, integer 1-10, temperature 0.
The report's holistic score is the unweighted mean of the four dimension scores.
The judge sees only the topic and the report. It is not told which system produced the report.

Anchors (apply to every dimension; scores between anchors are allowed):

## 1. Coverage
How completely the report addresses the breadth of the topic a well-informed reader would expect.
- 1-2: Addresses one narrow aspect or is mostly off-topic. Most major facets are missing.
- 3-4: Covers some major facets but omits several that are important.
- 5-6: Covers most major facets, but a few important ones are missing or only mentioned in passing.
- 7-8: Covers nearly all major facets, with only minor omissions.
- 9-10: Covers all major facets and relevant secondary ones, including current developments where the topic calls for them.

## 2. Depth
Specificity and analytical substance within each facet.
- 1-2: Generic statements only. No concrete facts, figures, names or mechanisms.
- 3-4: Mostly generic, with a few concrete details.
- 5-6: A mix of concrete details and generalities. Little analysis beyond description.
- 7-8: Mostly specific (figures, named examples, mechanisms, dates), with some analysis of causes, trade-offs or implications.
- 9-10: Consistently specific and analytical. Weighs competing evidence or viewpoints and draws well-supported conclusions.

## 3. Evidence use
How well claims are tied to identifiable, credible sources and how accurately that evidence is used.
- 1-2: No citations or sources, or citations that obviously do not match the claims.
- 3-4: Few citations. Many specific claims are unsourced or only loosely connected to the cited sources.
- 5-6: Some claims are cited and plausibly supported, but significant specific claims are unsourced, or sources are weak.
- 7-8: Most specific claims are cited with relevant sources, and the sources look credible and appropriate.
- 9-10: Nearly all specific claims are cited with credible, relevant sources. Uncertainty and conflicts in the evidence are acknowledged.

Judge only what is visible in the report. Do not reward length for its own sake. Do not use outside knowledge to guess whether a URL is live.

## 4. Organization
Structure, clarity and readability.
- 1-2: Disorganized or hard to follow.
- 3-4: Some structure, but with repetition, poor ordering or unclear sections.
- 5-6: Reasonable structure with minor flow problems or redundancy.
- 7-8: Clear structure and logical flow. Sections do what their headings say.
- 9-10: Excellent structure. Concise, well-sequenced, with an informative summary and conclusion.

## Output format
JSON only:
`{"coverage": {"score": int, "reason": str}, "depth": {...}, "evidence_use": {...}, "organization": {...}}`
