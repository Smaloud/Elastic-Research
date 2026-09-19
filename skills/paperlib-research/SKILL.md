---
name: paperlib-research
description: Search and synthesize the user's local Paperlib research library, including papers, books, methods, datasets, experimental results, limitations, and engineering tricks. Use for evidence-backed literature lookup, experiment comparison, related-work synthesis, or research-gap analysis against the local library. Do not use for general web literature search or figure extraction.
---

# Paperlib Research

Use Paperlib's localhost API as the source of truth. The service normally runs at `http://127.0.0.1:8765`.

## Workflow

1. Check health with `scripts/query_paperlib.py health`.
2. Use `search` for papers or books. Prefer hybrid retrieval unless the request specifically needs exact keywords.
3. Use `facts` for structured comparisons. Select precise `--fact-type` values rather than treating every extracted item as a generic resource.
4. Base synthesis on returned evidence. Identify the paper, year, category, page when present, and quote only the short evidence needed to support the conclusion.
5. State coverage limits: an absent fact means “not extracted or not found,” not proof that the paper omitted it.

For available categories and endpoint parameters, read [references/api.md](references/api.md). Run `taxonomy` when the live server may be newer than the reference.

## Analysis patterns

- Experiment comparison: query `experimental_result`, `metric`, `baseline`, `evaluation_protocol`, `statistical_test`, and `ablation`; align dataset, metric, setting, value, unit, baseline, and delta before comparing numbers.
- Engineering synthesis: query `engineering_trick`, `software`, `hardware`, `compute_environment`, `hyperparameter`, and `training_setup`; separate reported facts from your inference.
- Research gaps: combine `limitation`, `future_work`, `research_goal`, and `contribution`; distinguish recurring author-stated gaps from gaps inferred across papers.
- Method/resource inventory: keep `method`, `algorithm`, `architecture`, `model`, `dataset`, `benchmark`, `software`, and `code_repository` separate.

## Boundaries

- Default to read-only operations. Do not mutate PostgreSQL directly or bypass the Paperlib API.
- Ask for explicit authorization before any library change, external upload, or cloud API call not already requested.
- Figure extraction and visual-similarity analysis are intentionally out of scope until the user resumes that phase.
- Never expose saved API keys or files from `data/private`.
