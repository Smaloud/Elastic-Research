# Paperlib local API

Base URL: `http://127.0.0.1:8765/api/v1`

## Discovery

- `GET /health` — service and database health.
- `GET /search?q=...&mode=hybrid&limit=30` — paper/book retrieval. Optional filters: `year_from`, `year_to`, `tag`, `min_importance`, `max_familiarity`.
- `GET /works/{work_id}/analysis` — all structured facts for one work.

## Structured research facts

`GET /research/facts` accepts:

- `q`: text matched against paper title, structured value, and evidence.
- `fact_type`: repeatable category filter.
- `tag`, `year_from`, `year_to`, `min_confidence`, `limit`.

Each result includes `work_id`, `work_title`, `publication_year`, `fact_type`, structured `value`, `confidence`, and verbatim `evidence_text`.

`GET /research/taxonomy` returns the live category map. The current high-value groups are:

- Framing: `research_goal`, `contribution`, `terminology`, `task`.
- Methods: `method`, `algorithm`, `architecture`, `model`.
- Resources: `dataset`, `benchmark`, `software`, `code_repository`, `hardware`, `compute_environment`.
- Experiments: `baseline`, `metric`, `hyperparameter`, `training_setup`, `experimental_setup`, `evaluation_protocol`, `statistical_test`, `experimental_result`, `ablation`.
- Synthesis: `engineering_trick`, `limitation`, `future_work`, `ethical_consideration`.

Legacy records can still use `glossary`, `resource`, `finding`, or `suggested_tag`; label them as legacy instead of silently reclassifying them.

## Evidence rules

Treat `evidence_text` as the grounding source and `value` as the normalized extraction. Use `value.page` when available. Do not compare numeric results until dataset, split, metric direction, unit, baseline, and experimental setting are compatible.
