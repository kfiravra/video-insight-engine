# Brief 3.5 — per-group validation, key filter, merge — coordinator, 2026-10-13

Spec: tasks.md Phase 3 row 3.5; plan table 3.5 ("validate per group against the same Pydantic item models without
materializing defaults (A5); filter to own keys; deep-union merge chunk-major, explicit scalar policy + deterministic
order (C12); `extraction_data` shape unchanged; count/coverage on requested fields only"); brief §Phase 3. Design:
`designs/phase3-design.md` **§3.5**, §1 (merge + validation today), §6 row 3.5, §7 item 14, §8 3.5 tests, §9 (A14:
policy on the group path only — `extraction_merger.py` stays untouched). Agent rules: `agent-rules.md` (Phase 3 first).

## What exists (committed)
- 3.1 `schema_fields.keep_requested(data, fields)` = THE key rule (requested objects kept whole; dotted paths keep
  parents) — the key filter must use it, not a second rule; `slice_skeleton` raises `SchemaParseError` on a broken
  known schema → validate that domain unfiltered (log) rather than dropping it.
- 3.3 `FieldRequest` / `ExtractionGroup` (`field_requests.py`, `field_groups.py`).
- 3.4 (9c6cd70) `extraction_groups.py` (`GroupOutcome`, interim `validate_domain_output_with_drops` +
  `merge_batch_extractions`), `phases/split_extraction.py` (merged `extraction_complete`, `record_validation`,
  `finish_extraction`, quality/coverage).

## Build
- New `services/summarizer/src/services/pipeline/group_merge.py`: `validate_group(raw, group, content_tags,
  modifiers)` (unwrap flat single-domain output; validate each domain block with today's per-item validator so items keep
  item-level defaults; keep ONLY the group's key tree via `keep_requested` → no unrequested field materialized; a
  requested field the model omitted stays absent), `merge_groups(outcomes)` (chunk-major, then group order text1..N,
  visual; lists concat; scalars FIRST non-empty wins; dedup identity keys in a FIXED order (name, label, term, title,
  ingredient, tool, location); renumber only keys that held 1..n in every contributing list (step/order/index), never
  `number` otherwise), `requested_quality(fields, extraction)` (count/coverage on requested fields only).
- Swap the interim calls in `extraction_groups.py` / `phases/split_extraction.py`; `pipeline.extraction._groups =
  [{group, fields, status, outputTokens, wallMs}]` (optional key, absent on the false path).
- False path untouched (`extraction_merger.py`, `extractor.py`, `validate_domain_output*`): replay identity test
  stays green; no byte change.

## Tests (design §8 3.5)
own keys only when the model emits extra domains · unrequested fields not materialized · item-level defaults kept ·
chunk-major then group order · first non-empty scalar · fixed identity-key order regardless of hash seed (run under two
`PYTHONHASHSEED` values or construct the collision) · shirt numbers not renumbered · coverage on requested fields only ·
`extraction_data` domain-keyed · broken schema → unfiltered domain + error log.
