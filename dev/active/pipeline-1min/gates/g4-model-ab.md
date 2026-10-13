# g4 model A/B (4.0d) — per call type

Run 2026-10-13T11:29:03+00:00 + follow-ups 2026-10-13T11:45:55+00:00 (only=['plan', 'extraction'] arms=['B-medium'] medium=['plan', 'extraction'], ceiling $0.29); 2026-10-13T11:46:28+00:00 (only=['tier_probe'] source=['golden'] arms=['B'] repeats=2, ceiling $0.16); 2026-10-13T12:11:18+00:00 (only=['description', 'quiz'] arms=['A', 'B'] repeats=2, ceiling $0.03) · 206 calls · spend $1.087 (estimate $1.302, ceiling $1.50). Metrics: bool = rate, number = mean; `vsA_*` = against arm A's answer to the same request; run-to-run = the same arm twice on one video (the noise floor).

### tier_probe

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | golden_domain | golden_format | golden_has_visual_demo | golden_transcript_usable | vsA_domain |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `claude-haiku-4-5-20251001` | 21 | 0 | 0 | 0.91 | – | 0 | 0 | 45.5 | 2245 | 0 / 0 | 0.0472 | 1 | 0.944 | 0.833 | 1 | 1 | – |
| B | `claude-haiku-5-5` effort low | 39 | 0 | 0 | 1.15 | – | 1 | 0 | 50.5 | 3121 | 0 / 0 | 0.0119 | 1 | 0.972 | 0.833 | 0.889 | 1 | 0.897 |

Run-to-run (B, second run vs first): run_domain 0.944, run_format 0.889, run_has_visual_demo 1, run_transcript_usable 1

### description

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | has_content | vsA_fields_agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 6 | 0 | 0 | 3.18 | – | 0 | 0 | 167 | 577 | 0 / 0 | 0.00107 | 1 | 1 | – |
| B | `gpt-6-luna` reasoning none | 6 | 0 | 0 | 3.41 | – | 0 | 0 | 226 | 576 | 0 / 0 | 0.00099 | 1 | 1 | 0.632 |

Run-to-run (A, second run vs first): run_fields_agree 0.667

Run-to-run (B, second run vs first): run_fields_agree 0.919

### plan

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | fallback | tabs | distinct_sources | duplicate_sources | required_present | vsA_tabs_jaccard | vsA_evidence_agree | vsA_evidence_flips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A† | `claude-sonnet-4-6` | 4 | 0 | 0 | 24.5 | – | 0 | 0 | 958 | 22562 | 65,160 / 0 | 0.143 | 1 | 0 | 3.75 | 3.5 | 0.25 | 1 | – | – | – |
| B | `claude-sonnet-5-5` effort low | 4 | 0 | 0 | 9.92 | – | 0 | 0 | 1231 | 29844 | 66,210 / 22,070 | 0.206 | 1 | 0 | 4.25 | 2.75 | 1.5 | 1 | 0.692 | 0.904 | has_ranking×1, has_steps×2, is_learnable×2 |
| B-medium† | `claude-sonnet-5-5` effort medium | 4 | 0 | 0 | 11.5 | – | 0 | 0 | 1,340 | 29844 | 88,280 / 0 | 0.127 | 1 | 0 | 4.75 | 3.25 | 1.5 | 1 | 0.817 | 0.942 | has_ranking×1, has_steps×2 |

† the arm's first call read a cache written before this run (cost is warm)

Run-to-run (A, second run vs first): run_evidence_agree 1, run_primary_tag 1, run_tabs_jaccard 0.6

Run-to-run (B, second run vs first): run_evidence_agree 1, run_primary_tag 1, run_tabs_jaccard 0.5

Run-to-run (B-medium, second run vs first): run_evidence_agree 1, run_primary_tag 1, run_tabs_jaccard 0.6

### memory

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | outline_valid | outline_sections | evidence_keys | vsA_evidence_agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `claude-haiku-4-5-20251001` | 3 | 0 | 0 | 9.05 | – | 0 | 0 | 903 | 7207 | 0 / 0 | 0.0327 | 1 | 1 | 9.33 | 13 | – |
| B | `claude-haiku-5-5` effort low | 3 | 0 | 0 | 4.05 | – | 0 | 0 | 764 | 8973 | 0 / 0 | 0.00358 | 1 | 1 | 9 | 13 | 0.82 |

### extraction

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | dropped | defaulted | filled_share | answer_lost | items | vsA_items_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `claude-haiku-4-5-20251001` | 14 | 0 | 0 | 14.8 | 0.638 | 0 | 0 | 1,580 | 10969 | 99,463 / 25,444 | 0.158 | 1 | 0 | 0.071 | 0.857 | 0 | 20.9 | – |
| B | `claude-haiku-5-5` effort low | 14 | 0 | 0 | 5.19 | 0.864 | 0 | 0 | 996 | 14493 | 129,457 / 33,081 | 0.0142 | 1 | 0 | 0 | 0.821 | 0 | 14 | 0.717 |
| B-medium† | `claude-haiku-5-5` effort medium | 14 | 0 | 0 | 7.17 | 0.872 | 0 | 0 | 1,048 | 14493 | 142,688 / 19,850 | 0.0121 | 1 | 0 | 0 | 0.821 | 0 | 16.4 | 0.866 |

† the arm's first call read a cache written before this run (cost is warm)

Run-to-run (A, second run vs first): run_filled_delta 0, run_items_ratio 1.04

Run-to-run (B, second run vs first): run_filled_delta 0, run_items_ratio 0.855

Run-to-run (B-medium, second run vs first): run_filled_delta 0, run_items_ratio 0.904

### quiz

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | questions | dropped | usable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 2 | 0 | 0 | 5.2 | – | 0 | 0 | 336 | 4267 | 0 / 0 | 0.00168 | 1 | 4 | 0 | 1 |
| B | `gpt-6-luna` reasoning none | 2 | 0 | 0 | 10.5 | – | 1 | 0 | 530 | 4266 | 0 / 0 | 0.0016 | 1 | 5 | 0 | 1 |

### vision

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | described_share | kept_count | cards_read | vsA_selection_jaccard | vsA_scene_agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `claude-sonnet-4-6` | 4 | 0 | 0 | 22.2 | – | 0 | 0 | 992 | 10235 | 0 / 0 | 0.171 | 1 | 1 | 7.5 | 1.25 | – | – |
| B | `claude-haiku-5-5` effort low | 4 | 0 | 0 | 5.19 | – | 0 | 0 | 1,024 | 10426 | 0 / 0 | 0.00583 | 1 | 1 | 7 | 1 | 0.928 | 0.768 |
| C | `claude-sonnet-5-5` effort low | 4 | 0 | 0 | 8.88 | – | 0 | 0 | 1,075 | 10426 | 0 / 0 | 0.119 | 1 | 1 | 7.25 | 1.25 | 0.964 | 0.938 |

### translation_output

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | echo | left_share | length_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 7 | 0 | 0 | 10.2 | – | 0 | 0 | 695 | 848 | 0 / 0 | 0.00373 | 1 | 0 | 0.044 | 0.808 |
| B | `gpt-6-luna` reasoning none | 7 | 0 | 0 | 8.95 | – | 0 | 0 | 722 | 847 | 0 / 0 | 0.00307 | 1 | 0 | 0.044 | 0.855 |

### translation_rag

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | echo | left_share | length_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 4 | 0 | 0 | 5.71 | – | 0 | 0 | 586 | 1062 | 0 / 0 | 0.00201 | 0.75 | 0 | 0 | 1.34 |
| B | `gpt-6-luna` reasoning none | 4 | 0 | 0 | 7.18 | – | 0 | 0 | 624 | 1061 | 0 / 0 | 0.00173 | 1 | 0 | 0 | 1.35 |

### faithfulness

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | grounded | vsA_agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 18 | 0 | 0 | 1.05 | – | 0 | 0 | 35.2 | 5614 | 75,520 / 0 | 0.00862 | 1 | 0.889 | – |
| B | `gpt-6-luna` reasoning none | 18 | 0 | 0 | 1.6 | – | 0 | 0 | 43.9 | 5613 | 0 / 0 | 0.012 | 1 | 0.444 | 0.556 |

## Max prompt tokens (provider-counted)

| call type | model | tokens |
|---|---|---|
| description | `openai/gpt-4o-mini` | 577 |
| description | `openai/gpt-6-luna` | 576 |
| tier_probe | `anthropic/claude-haiku-4-5-20251001` | 2,245 |
| tier_probe | `anthropic/claude-haiku-5-5` | 3,121 |
| plan | `anthropic/claude-sonnet-4-6` | 22,562 |
| plan | `anthropic/claude-sonnet-5-5` | 29,844 |
| memory | `anthropic/claude-haiku-4-5-20251001` | 7,207 |
| memory | `anthropic/claude-haiku-5-5` | 8,973 |
| extraction | `anthropic/claude-haiku-4-5-20251001` | 10,969 |
| extraction | `anthropic/claude-haiku-5-5` | 14,493 |
| quiz | `openai/gpt-4o-mini` | 4,267 |
| quiz | `openai/gpt-6-luna` | 4,266 |
| faithfulness | `openai/gpt-4o-mini` | 5,614 |
| faithfulness | `openai/gpt-6-luna` | 5,613 |
| translation_output | `openai/gpt-4o-mini` | 848 |
| translation_output | `openai/gpt-6-luna` | 847 |
| translation_rag | `openai/gpt-4o-mini` | 1,062 |
| translation_rag | `openai/gpt-6-luna` | 1,061 |
| vision | `anthropic/claude-haiku-5-5` | 10,426 |
| vision | `anthropic/claude-sonnet-4-6` | 10,235 |
| vision | `anthropic/claude-sonnet-5-5` | 10,426 |

Longest chunk (3.7, 50,000 cl100k): extraction prompt ≈ 54,608 cl100k ≈ 70,990 Haiku 5.5 tokens vs the 100,000-token price tier.

## Picks

Rule: quality within noise or better first, then latency, then cost.

| call type | pick | evidence | decided by |
|---|---|---|---|
| plan | `anthropic/claude-sonnet-5-5 @ effort medium` | at low it flipped is_learnable on T1dQ in both runs (quiz tab dropped); medium keeps it — evidence 0.94 / tabs 0.82 vs A (A's own run-to-run tabs 0.6); p50 11.3 s vs 23.6 s; warm ≈ $0.032 vs $0.036 per call | quality (medium over low), then latency (vs Sonnet 4.6) |
| vision | `anthropic/claude-sonnet-5-5` | reads all 5 uC45 cards like Sonnet 4.6; scene types 0.94 vs A (Haiku 5.5 0.77: chef-at-work frames → talking_head, dropped as filler); p50 8.6 s vs 16.8 s; −30 % cost | quality (C over B), then latency (C over A) |
| extraction | `anthropic/claude-haiku-4-5-20251001 (unchanged)` | Haiku 5.5 kept 0.72 of A's items vs its own run-to-run 0.86 and lost real recipe steps (jMq8: preheat, 375 °F covered bake); at medium 0.87, same steps lost | quality |
| memory | `anthropic/claude-haiku-4-5-20251001 (unchanged)` | reads LLM_EXTRACTION_MODEL (D5) — follows the extraction pick (Haiku 5.5 parsed 3/3, evidence agreement 0.82) | tied to extraction |
| tier_probe | `anthropic/claude-haiku-4-5-20251001 (unchanged)` | Haiku 5.5 matched 16/18 visual-demo labels vs 18/18, the same two misses in both passes; p50 1.14 s vs 0.91 s, 1/36 calls over the 4 s timeout | quality |
| description | `openai/gpt-6-luna` | same URLs (differences are link/resource/social buckets); gpt-4o-mini invented a placeholder URL in 1/6 runs, luna 0/6; p50 3.4 s vs 3.2 s; −7 % cost | quality |
| quiz | `openai/gpt-4o-mini (unchanged)` | both usable 2/2 (4 vs 5 valid questions, cap 8) — within noise; p50 5.2 s vs 10.5 s, one luna call past the 14 s attempt timeout | latency |
| translation_output | `openai/gpt-6-luna` | no echo, the same share left in English (0.044); p50 9.0 s vs 10.2 s; −18 % cost | latency (quality within noise) |
| translation_rag | `openai/gpt-6-luna` | parsed 4/4 batches vs gpt-4o-mini 3/4, none left in Hebrew; p50 7.1 s vs 5.4 s | quality |
| faithfulness | `openai/gpt-4o-mini (unchanged, fast tier)` | luna halves the grounded rate (0.44 vs 0.89; 8 of 18 claims flipped, never the reverse) and fails headings; the score is informational and the golden baseline is gpt-4o-mini | quality (comparability) |

p50s in this section are round 1's nearest-rank values; true medians are in the tables (plan 24.5 vs 11.5 s, vision 22.2 vs 8.9 s).

## Round 2 — 2026-10-13 (UTC)

Kfir's order D30 (`brief-4.0d-r2.md`). **Spend $1.2248** over 100 calls (ceiling $1.60, raised by Kfir from $1.00; dry-run estimate $1.377); REQUIRED scope only (no OPTIONAL call sent); 0 errors, 0 refusals, 0 truncated answers.

Requests: round 1's, re-captured from the cassettes with round 1's TODAY models pinned — 16/16 identical in cl100k tokens; 12/12 (request, model) pairs both rounds answered got the same provider-counted prompt tokens. EqaMrzd9nZU's plan request was rebuilt by `run_plan` from its latest v9 dev run (Mongo + S3 + that run's Langfuse trace for the probe answer and the description); its video half is byte-identical to the request the run sent (cache read 16290 = round 1's static block). The completeness rule — `Completeness: emit every distinct step, item or command the transcript supports — never merge two into one, never skip one.` — sits at the end of `<quality_rules>` in the 1-hour block of every extraction request (all four arms, identical bytes). Repeats are numbered after round 1's, so both rounds' answers are scored together (round 1's arms shown as `r1`). Latency = true median; round 1's tables above were re-scored with it — summary fields changed: `latency_p50_ms`, `ttft_p50_ms`.

**Blind review.** 9 reviewer agents, one per field group, classified all 1,906 extraction items of the 10 outputs per field (Haiku 4.5 without the rule ×2, the four `+rule` arms ×2) with the outputs shuffled and relabelled X1..X10 per field, then unblinded by key. Each item sits in one cluster (the same step / item across outputs) with a verdict — supported (quoted with its [m:ss]), padding, unclear. A cluster counts as present in an output when one of its items is there or an item of the output merges it (`also_covers`, flagged "merged"); items of one output beyond the first in one cluster — a duplicate, or one thing split into two items — count as "dup / split". Verdicts below use supported clusters only; unclear is reported apart.

**Round-1 correction (jMq8).** Round 1's extraction pick said Haiku 5.5 "lost real recipe steps (jMq8: preheat, 375 °F covered bake)". It did not: the transcript has no preheat, no 375 °F and no foil / covered bake (0 hits); its oven steps are the sauce into a 225-degree oven, lid ajar [8:21], and the lasagna "in the oven at 425, just to crisp the top up" [14:40]. Round 1 compared Haiku 5.5 with A.0 only and counted A.0's preheat-oven-375 (padding), bake-covered-foil-375 (padding) — Haiku 4.5's own inventions without the rule; round 2's extra no-rule run also invented: A.1's bake-covered-foil-425 (padding), preheat-oven-425 (unclear). The real 425 °F crisp step is in every `+rule` run; outputs without it as an item of its own: A.1 (merged). Kfir's named check is evaluated that way below; the preheat / covered bake are not required.

### 1. Extraction — four arms, completeness rule

PASS = in both runs on every cassette the arm lacks no supported cluster A+rule's two runs have, filled_share ≥ A+rule's, defaulted ≤ A+rule's. A single run of A+rule itself lacks 4–17 clusters of its own two-run union, so no single run of any arm can meet the first condition; the counts are the comparison. "Lacks vs all outputs" = supported clusters of the union of all 10 outputs a run misses. A = Haiku 4.5 without the rule (T1dQ: round 1 ×2; jMq8, uC45: round 1 + round 2).

| arm | model | calls | median / p95 s | TTFT median s | in / out tok | $ / call | filled_share | defaulted | lacks vs A+rule (T1dQ · jMq8 · uC45, run 0/1) | lacks vs all outputs | padding | unclear | dup / split | PASS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `claude-haiku-4-5-20251001` | 20 | 14.7 / 32.4 | 0.574 | 9,010 / 1,593 | 0.0116 | 0.9 | 0.1 | T1dQ 22/20 · jMq8 9/4 · uC45 13/16 | T1dQ 42/37 · jMq8 15/11 · uC45 26/27 (of 120/74/87) | 4 | 28 | 19 | no |
| A+rule | `claude-haiku-4-5-20251001` | 20 | 15.0 / 32.7 | 0.527 | 9,037 / 1,814 | 0.0118 | 0.95 | 0.1 | T1dQ 14/17 · jMq8 4/4 · uC45 10/10 | T1dQ 42/45 · jMq8 13/13 · uC45 26/26 (of 120/74/87) | 13 | 34 | 30 | reference |
| B+rule | `claude-haiku-5-5` low | 20 | 4.8 / 9.9 | 0.798 | 11,784 / 1,044 | 0.00086 | 0.875 | 0 | T1dQ 33/34 · jMq8 7/7 · uC45 32/28 | T1dQ 43/44 · jMq8 15/13 · uC45 48/37 (of 120/74/87) | 2 | 7 | 3 | no |
| B-medium+rule | `claude-haiku-5-5` medium | 20 | 5.5 / 11.1 | 0.987 | 11,784 / 1,160 | 0.00082 | 0.875 | 0 | T1dQ 32/32 · jMq8 8/9 · uC45 26/34 | T1dQ 36/41 · jMq8 16/12 · uC45 37/49 (of 120/74/87) | 0 | 12 | 3 | no |
| C+rule | `claude-sonnet-5-5` low | 20 | 11.3 / 17.8 | 1.15 | 11,784 / 1,647 | 0.0246 | 0.95 | 0 | T1dQ 19/19 · jMq8 3/1 · uC45 15/16 | T1dQ 21/22 · jMq8 9/2 · uC45 24/28 (of 120/74/87) | 2 | 10 | 4 | no |

**A+rule's reference holds one outlier.** A+rule.0's T1dQ visual answer (tech.snippets, tech.patterns) is the only output with snippets / patterns — `tech.snippets` 10 padding, 2 unclear; `tech.patterns` 8 supported — and 4,841 output tokens vs 24–433 for the other outputs of that request. A+rule's filled_share is 0.95 with it, 0.947 with that answer left out, 0.9 with it counted as empty. Of each run's T1dQ lacks vs A+rule, these many are that answer's clusters: A.0 8, A.1 8, B+rule.0 8, B+rule.1 8, B-medium+rule.0 8, B-medium+rule.1 8, C+rule.0 8, C+rule.1 8. T1dQ lacks vs A+rule without it (run 0/1): A 14/12, A+rule 14/9, B+rule 25/26, B-medium+rule 24/24, C+rule 11/11.

**Kfir's named check, corrected.** Which outputs INVENT the preheat / covered bake (padding — the transcript has neither) and which carry the real 425 °F crisp step (`bake-uncovered-425-crisp-top`; "merged" = only inside another item):

| output | round | invented oven steps (verdict) | real 425 °F crisp bake |
|---|---|---|---|
| A+rule.0 | 2 | – | direct |
| A+rule.1 | 2 | – | direct |
| A.0 | 1 | preheat-oven-375 (padding), bake-covered-foil-375 (padding) | direct |
| A.1 | 2 | bake-covered-foil-425 (padding), preheat-oven-425 (unclear) | merged |
| B+rule.0 | 2 | – | direct |
| B+rule.1 | 2 | – | direct |
| B-medium+rule.0 | 2 | – | direct |
| B-medium+rule.1 | 2 | – | direct |
| C+rule.0 | 2 | – | direct |
| C+rule.1 | 2 | – | direct |

**Keep-the-rule check** (Haiku 4.5 with vs without the rule; Jaccard distance over the supported + padding clusters an output covers; within noise = the with/without distance ≤ the larger run-to-run distance on every cassette): **no**.

| cassette | A vs A+rule (mean of 4 pairs) | A run-to-run | A+rule run-to-run | within noise | supported only: rule vs A / A+rule noise | supported lost / gained | padding items A → A+rule |
|---|---|---|---|---|---|---|---|
| T1dQhQAm8Tc | 0.343 | 0.277 | 0.408 | yes | 0.307 vs 0.269 / 0.337 | 14 / 13 | [0, 1] → [11, 1] |
| jMq8lEu-of0 | 0.17 | 0.162 | 0.123 | NO | 0.151 vs 0.123 / 0.123 | 3 / 3 | [2, 1] → [0, 0] |
| uC45_4nnEAI | 0.256 | 0.246 | 0.292 | yes | 0.25 vs 0.246 / 0.282 | 8 / 10 | [0, 0] → [0, 1] |

The "no" rests on jMq8 alone (0.17 vs 0.162), one noise pair per arm — A's pair: runs from rounds 1 and 2; A+rule's: both runs in round 2, back to back. Of the 46 clusters that differ across the four A / A+rule pairs, 6 are padding (the invented oven steps); on supported clusters only the distance is 0.151 vs 0.123. On T1dQ the check passes (0.343 ≤ 0.408) because A+rule's own run-to-run is inflated by the A+rule.0 visual outlier: without it A+rule's run-to-run is 0.291 and the with/without distance 0.292 — within noise: no.

Supported clusters each run lacks against A+rule's two runs (field:cluster):

- T1dQ B+rule.0: learning.concepts:cloudflare-vs-vercel-deployment, learning.concepts:codex-computer-use, learning.concepts:design-tools-paper-mobbin, learning.concepts:mcp-protocol, learning.concepts:opus-5-5-primary-model, learning.concepts:planetscale-postgres, learning.concepts:svelte-sveltekit-frontend, learning.concepts:switch-subscriptions-best-value, learning.timestamps:anthropic-now-best-value, learning.timestamps:best-models-as-of-sept-2026, learning.timestamps:claude-opus-excellent-in-cli, learning.timestamps:convex-components-agent-setup-stripe, learning.timestamps:gosha-email-on-cloudflare, learning.timestamps:grokbot-all-work-without-leaving, learning.timestamps:grokbot-own-computer, learning.timestamps:models-change-in-2-3-months, learning.timestamps:opus-5-5-strengths, learning.timestamps:orgo-investor-disclosure, learning.timestamps:paper-portfolio-pluto-redesign, learning.timestamps:tech-stack-intro-decisions-matter, learning.timestamps:why-svelte-ai-friendly-fast, tech.patterns:cloud-agent-production-error-response, tech.patterns:cloudflare-all-in-else-vercel, tech.patterns:convex-components-agent-setup, tech.patterns:effect-for-all-backend-services, tech.patterns:mcp-connected-tools, tech.patterns:opus-primary-switch-providers, tech.patterns:parallel-agents-git-worktrees, tech.patterns:svelte-over-react-for-ai, tech.topics:agent-pr-automation, tech.topics:gosha-email-for-agents, tech.topics:grokbot-sponsor-multi-agent-workflow, tech.topics:mcp-integration
- T1dQ B+rule.1: learning.concepts:codex-computer-use, learning.concepts:design-tools-paper-mobbin, learning.concepts:grokbot-multi-agent-robots, learning.concepts:mcp-protocol, learning.concepts:per-agent-persistent-computer, learning.concepts:switch-subscriptions-best-value, learning.timestamps:anthropic-now-best-value, learning.timestamps:best-models-as-of-sept-2026, learning.timestamps:claude-opus-excellent-in-cli, learning.timestamps:convex-components-agent-setup-stripe, learning.timestamps:grokbot-all-work-without-leaving, learning.timestamps:grokbot-designer-engineer-group-chat-site, learning.timestamps:grokbot-own-computer, learning.timestamps:models-change-in-2-3-months, learning.timestamps:opus-5-5-strengths, learning.timestamps:orgo-investor-disclosure, learning.timestamps:paper-portfolio-pluto-redesign, learning.timestamps:platform-choice-best-value-no-loyalty, learning.timestamps:skills-step-code-structuring, learning.timestamps:skills-step-proof-video-tests-screenshots, learning.timestamps:why-svelte-ai-friendly-fast, tech.patterns:cloud-agent-production-error-response, tech.patterns:cloudflare-all-in-else-vercel, tech.patterns:convex-components-agent-setup, tech.patterns:effect-for-all-backend-services, tech.patterns:mcp-connected-tools, tech.patterns:opus-primary-switch-providers, tech.patterns:parallel-agents-git-worktrees, tech.patterns:svelte-over-react-for-ai, tech.topics:agent-pr-automation, tech.topics:gosha-email-for-agents, tech.topics:grokbot-sponsor-multi-agent-workflow, tech.topics:mcp-integration, tech.topics:workos-auth
- T1dQ B-medium+rule.0: learning.concepts:codex-computer-use, learning.concepts:design-tools-paper-mobbin, learning.concepts:skills-software-factory-workflow, learning.concepts:switch-subscriptions-best-value, learning.timestamps:anthropic-now-best-value, learning.timestamps:best-models-as-of-sept-2026, learning.timestamps:bezalo-effect-mcp-server, learning.timestamps:convex-components-agent-setup-stripe, learning.timestamps:gosha-email-on-cloudflare, learning.timestamps:grokbot-all-work-without-leaving, learning.timestamps:grokbot-designer-engineer-group-chat-site, learning.timestamps:grokbot-own-computer, learning.timestamps:grokbot-screenshot-to-paper-design, learning.timestamps:models-change-in-2-3-months, learning.timestamps:opus-5-5-strengths, learning.timestamps:orgo-investor-disclosure, learning.timestamps:paper-portfolio-pluto-redesign, learning.timestamps:skills-step-proof-video-tests-screenshots, learning.timestamps:tech-stack-intro-decisions-matter, learning.timestamps:why-svelte-ai-friendly-fast, learning.timestamps:wrap-up-outro, tech.patterns:cloud-agent-production-error-response, tech.patterns:cloudflare-all-in-else-vercel, tech.patterns:convex-components-agent-setup, tech.patterns:effect-for-all-backend-services, tech.patterns:mcp-connected-tools, tech.patterns:opus-primary-switch-providers, tech.patterns:parallel-agents-git-worktrees, tech.patterns:svelte-over-react-for-ai, tech.topics:agent-pr-automation, tech.topics:gosha-email-for-agents, tech.topics:mcp-integration
- T1dQ B-medium+rule.1: learning.concepts:convex-backend, learning.concepts:convex-components, learning.concepts:design-tools-paper-mobbin, learning.concepts:mcp-protocol, learning.concepts:opus-5-5-primary-model, learning.concepts:planetscale-postgres, learning.concepts:svelte-sveltekit-frontend, learning.concepts:switch-subscriptions-best-value, learning.timestamps:anthropic-now-best-value, learning.timestamps:best-models-as-of-sept-2026, learning.timestamps:claude-opus-excellent-in-cli, learning.timestamps:grokbot-all-work-without-leaving, learning.timestamps:grokbot-own-computer, learning.timestamps:models-change-in-2-3-months, learning.timestamps:opus-5-5-strengths, learning.timestamps:orgo-investor-disclosure, learning.timestamps:paper-portfolio-pluto-redesign, learning.timestamps:tech-stack-intro-decisions-matter, learning.timestamps:why-svelte-ai-friendly-fast, tech.patterns:cloud-agent-production-error-response, tech.patterns:cloudflare-all-in-else-vercel, tech.patterns:convex-components-agent-setup, tech.patterns:effect-for-all-backend-services, tech.patterns:mcp-connected-tools, tech.patterns:opus-primary-switch-providers, tech.patterns:parallel-agents-git-worktrees, tech.patterns:svelte-over-react-for-ai, tech.topics:agent-pr-automation, tech.topics:gosha-email-for-agents, tech.topics:grokbot-sponsor-multi-agent-workflow, tech.topics:multi-device-agent-distribution, tech.topics:workos-auth
- T1dQ C+rule.0: learning.concepts:codex-computer-use, learning.concepts:design-tools-paper-mobbin, learning.concepts:grokbot-multi-agent-robots, learning.timestamps:anthropic-now-best-value, learning.timestamps:claude-opus-excellent-in-cli, learning.timestamps:grokbot-all-work-without-leaving, learning.timestamps:grokbot-own-computer, learning.timestamps:models-change-in-2-3-months, learning.timestamps:orgo-investor-disclosure, tech.patterns:cloud-agent-production-error-response, tech.patterns:cloudflare-all-in-else-vercel, tech.patterns:convex-components-agent-setup, tech.patterns:effect-for-all-backend-services, tech.patterns:mcp-connected-tools, tech.patterns:opus-primary-switch-providers, tech.patterns:parallel-agents-git-worktrees, tech.patterns:svelte-over-react-for-ai, tech.topics:coding-harnesses-overview, tech.topics:mcp-integration
- T1dQ C+rule.1: learning.concepts:codex-computer-use, learning.concepts:design-tools-paper-mobbin, learning.concepts:grokbot-multi-agent-robots, learning.concepts:planetscale-postgres, learning.concepts:skills-software-factory-workflow, learning.timestamps:anthropic-now-best-value, learning.timestamps:grokbot-all-work-without-leaving, learning.timestamps:grokbot-own-computer, learning.timestamps:models-change-in-2-3-months, learning.timestamps:opus-5-5-strengths, tech.patterns:cloud-agent-production-error-response, tech.patterns:cloudflare-all-in-else-vercel, tech.patterns:convex-components-agent-setup, tech.patterns:effect-for-all-backend-services, tech.patterns:mcp-connected-tools, tech.patterns:opus-primary-switch-providers, tech.patterns:parallel-agents-git-worktrees, tech.patterns:svelte-over-react-for-ai, tech.topics:mcp-integration
- jMq8 B+rule.0: food.steps:bechamel-simmer-10min, food.steps:break-up-braised-ragu, food.steps:oil-baking-dish, food.steps:slice-and-serve, food.tips:carrot-belongs-in-bolognese, food.tips:couple-sheets-sticking-is-fine, food.tips:listen-for-sizzle
- jMq8 B+rule.1: food.steps:bechamel-simmer-10min, food.steps:break-up-braised-ragu, food.steps:oil-baking-dish, food.steps:slice-and-serve, food.tips:carrot-belongs-in-bolognese, food.tips:couple-sheets-sticking-is-fine, food.tips:listen-for-sizzle
- jMq8 B-medium+rule.0: food.steps:break-up-braised-ragu, food.steps:oil-baking-dish, food.steps:slice-and-serve, food.tips:bechamel-creaminess-cheese-cant-replicate, food.tips:carrot-belongs-in-bolognese, food.tips:listen-for-sizzle, food.tips:make-sauce-ahead-freeze, food.tips:render-pancetta-moderate-heat
- jMq8 B-medium+rule.1: food.steps:bechamel-simmer-10min, food.steps:break-up-braised-ragu, food.steps:oil-baking-dish, food.steps:slice-and-serve, food.tips:carrot-belongs-in-bolognese, food.tips:listen-for-sizzle, food.tips:pasta-doneness-flexible-snaps-3min, food.tips:render-pancetta-moderate-heat, food.tips:store-bought-de-cecco-sheets
- jMq8 C+rule.0: food.steps:slice-and-serve, food.tips:braise-sauce-225-oven-lid-ajar, food.tips:finish-425-crisp-top
- jMq8 C+rule.1: food.steps:slice-and-serve
- uC45 B+rule.0: gaming.highlights:back-to-back-new-gate, gaming.highlights:gloriosa-bogey-tradable, gaming.highlights:kaido-rocks-playset-pull, gaming.highlights:nico-robin-elbaf-pull, gaming.highlights:second-luffy-regular-playset, gaming.highlights:usopp-needed-elbaf, gaming.loadout:aramaki, gaming.loadout:ashiki-board-clear, gaming.loadout:ben-beckman, gaming.loadout:charlotte-linlin, gaming.loadout:foxy-leader, gaming.loadout:gan-bay-alternative-deck, gaming.loadout:kaido-leader, gaming.loadout:katakuri, gaming.loadout:kyo, gaming.loadout:lead-performers, gaming.loadout:nami, gaming.loadout:new-gate-rare-and-sr, gaming.loadout:nico-robin, gaming.loadout:pirate-docking-six, gaming.loadout:rocks-event-card-opening, gaming.loadout:schwan, gaming.loadout:shanks-mihawk-fish-hawk-combo, gaming.loadout:soul, gaming.loadout:sp-yamato-expensive-to-alt, gaming.loadout:three-generals, gaming.loadout:zoro, gaming.rankings:ashiki-board-clear, gaming.rankings:king-doo-pandaman, gaming.rankings:luffy-elbaf-regular-sr, gaming.rankings:sol-4cost-blocker, gaming.rankings:three-generals
- uC45 B+rule.1: gaming.highlights:back-to-back-new-gate, gaming.highlights:gloriosa-bogey-tradable, gaming.highlights:kaido-ashiki-board-clear, gaming.highlights:kaido-rocks-playset-pull, gaming.highlights:luffy-loki-don-card, gaming.highlights:nico-robin-elbaf-pull, gaming.highlights:second-luffy-regular-playset, gaming.highlights:usopp-needed-elbaf, gaming.loadout:aramaki, gaming.loadout:ben-beckman, gaming.loadout:foxy-leader, gaming.loadout:gan-bay-alternative-deck, gaming.loadout:lead-performers, gaming.loadout:nami, gaming.loadout:nico-robin, gaming.loadout:pirate-docking-six, gaming.loadout:schwan, gaming.loadout:shanks-mihawk-fish-hawk-combo, gaming.loadout:soul, gaming.loadout:sp-yamato-expensive-to-alt, gaming.loadout:zoro, gaming.rankings:ashiki-board-clear, gaming.rankings:charlotte-linlin, gaming.rankings:king-doo-pandaman, gaming.rankings:luffy-elbaf-regular-sr, gaming.rankings:nico-robin, gaming.rankings:sol-4cost-blocker, gaming.rankings:three-generals
- uC45 B-medium+rule.0: gaming.highlights:back-to-back-new-gate, gaming.highlights:gloriosa-bogey-tradable, gaming.highlights:kaido-rocks-playset-pull, gaming.highlights:nico-robin-elbaf-pull, gaming.highlights:pandaman-king-doo, gaming.highlights:second-luffy-regular-playset, gaming.highlights:usopp-needed-elbaf, gaming.loadout:aramaki, gaming.loadout:ben-beckman, gaming.loadout:foxy-leader, gaming.loadout:gan-bay-alternative-deck, gaming.loadout:lead-performers, gaming.loadout:nami, gaming.loadout:new-gate-rare-and-sr, gaming.loadout:pirate-docking-six, gaming.loadout:schwan, gaming.loadout:shanks-mihawk-fish-hawk-combo, gaming.loadout:soul, gaming.loadout:sp-yamato-expensive-to-alt, gaming.loadout:zoro, gaming.rankings:ashiki-board-clear, gaming.rankings:king-doo-pandaman, gaming.rankings:luffy-elbaf-regular-sr, gaming.rankings:nico-robin, gaming.rankings:sol-4cost-blocker, gaming.rankings:three-generals
- uC45 B-medium+rule.1: gaming.highlights:back-to-back-new-gate, gaming.highlights:kaido-rocks-playset-pull, gaming.highlights:nico-robin-elbaf-pull, gaming.highlights:pandaman-king-doo, gaming.highlights:second-luffy-regular-playset, gaming.highlights:usopp-needed-elbaf, gaming.loadout:aramaki, gaming.loadout:ben-beckman, gaming.loadout:foxy-leader, gaming.loadout:gan-bay-alternative-deck, gaming.loadout:kaido-leader, gaming.loadout:katakuri, gaming.loadout:kyo, gaming.loadout:lead-performers, gaming.loadout:luffy-loki-don, gaming.loadout:nami, gaming.loadout:new-gate-rare-and-sr, gaming.loadout:nico-robin, gaming.loadout:pandaman-king-doo, gaming.loadout:pirate-docking-six, gaming.loadout:rocks-don, gaming.loadout:rocks-event-card-opening, gaming.loadout:schwan, gaming.loadout:shanks-mihawk-fish-hawk-combo, gaming.loadout:soul, gaming.loadout:sp-yamato-expensive-to-alt, gaming.loadout:three-generals, gaming.loadout:zoro, gaming.rankings:charlotte-linlin, gaming.rankings:king-doo-pandaman, gaming.rankings:luffy-loki-don, gaming.rankings:nico-robin, gaming.rankings:sol-4cost-blocker, gaming.rankings:three-generals
- uC45 C+rule.0: gaming.highlights:charlotte-linlin-pull, gaming.highlights:nico-robin-elbaf-pull, gaming.loadout:aramaki, gaming.loadout:ben-beckman, gaming.loadout:foxy-leader, gaming.loadout:gan-bay-alternative-deck, gaming.loadout:kaido-leader, gaming.loadout:katakuri, gaming.loadout:lead-performers, gaming.loadout:nami, gaming.loadout:pirate-docking-six, gaming.loadout:schwan, gaming.loadout:sp-yamato-expensive-to-alt, gaming.loadout:zoro, gaming.rankings:luffy-elbaf-regular-sr
- uC45 C+rule.1: gaming.highlights:charlotte-linlin-pull, gaming.highlights:kaido-rocks-playset-pull, gaming.highlights:nico-robin-elbaf-pull, gaming.loadout:aramaki, gaming.loadout:ben-beckman, gaming.loadout:charlotte-linlin, gaming.loadout:foxy-leader, gaming.loadout:gan-bay-alternative-deck, gaming.loadout:kaido-leader, gaming.loadout:katakuri, gaming.loadout:lead-performers, gaming.loadout:nami, gaming.loadout:pirate-docking-six, gaming.loadout:schwan, gaming.loadout:zoro, gaming.rankings:luffy-elbaf-regular-sr

**Item-level errors** (reviewer notes, unblinded — what the cluster verdicts do not capture):

- **jMq8 `food.ingredients`** (23 clusters: 21 supported / 1 padding / 1 unclear): every Haiku 4.5 output (A.0, A.1, A+rule.0, A+rule.1) invents quantities (1 lb / ½ lb meats, 28 oz tomatoes, 1 cup red wine, 4 cups milk, 3 tbsp paste, 1 cup Parmesan; A.0 "24 sheets"); B+rule.0 "1 tube". The wine colour is invented in 8 of 10 outputs — white in all four Haiku 5.5 outputs (B+rule.0/.1, B-medium+rule.0/.1), red in all four Haiku 4.5 outputs; only C+rule.0 and C+rule.1 stay with the transcript. Padding: B+rule.0's Parmesan topping; unclear: oil for browning (B+rule.0, B-medium+rule.0, B-medium+rule.1).
- **jMq8 `food.steps`** (26 clusters: 21 / 3 / 2): the transcript has no preheat, no 375 °F and no foil or covered bake. Its oven steps are the sauce going into a 225-degree oven with the lid ajar [8:21] and the lasagna going "in the oven at 425, just to crisp the top up" [14:40]. Padding clusters: `preheat-oven-375` and `bake-covered-foil-375` (A.0), `bake-covered-foil-425` (A.1). Unclear: `preheat-oven-425` (A.1) and `top-layer-bechamel` (A.0, A+rule.0). Invented details inside supported clusters: wine colour; quantities (28 oz, 4 cups milk, 2 cups broth); a 3–4 h braise; hot milk into the roux (A.0); Parmesan on every layer (A.0); oiled sheets (B-medium+rule.0). A.0 also places the rest (1230 s) and the bake (1080 s) after the end of the video.
- **jMq8 `food.tips`** (32 clusters, all supported): small invented details — salting the béchamel (A.0), a "broiler" (A+rule.0), the pasta brand misnamed "d'Azzo" (A+rule.1). Some outputs split one tip into two items: A.0, A+rule.0, B-medium+rule.0, B+rule.1.
- **uC45 `gaming.rankings`** (32 clusters: 21 / 1 / 10): the 10 unclear clusters are cards only named in a pull list, never rated, so their tier and reason are invented. Padding: "Mihawk Pandamen" (B+rule.1). Supported / unclear / padding items per output: C+rule.0 20/0/0, C+rule.1 19/1/0, A.0 18/2/0, A.1 17/3/0, A+rule.1 17/3/0, A+rule.0 17/3/0, B+rule.1 12/3/1, B-medium+rule.1 12/4/0, B+rule.0 12/1/0, B-medium+rule.0 12/5/0.
- **uC45 `gaming.loadout`** (47 clusters: 42 / 1 / 4): padding = box-recap items (C+rule.0, C+rule.1); unclear = box and pack meta (C+rule.1 ×5, C+rule.0 ×2). B+rule.0 and B-medium+rule.1 (11 items each) are fully grounded but cover the fewest clusters. Detail errors: A.1, B-medium+rule.0, B+rule.1, B+rule.0 (two).
- **uC45 `gaming.highlights`** (27 clusters: 24 / 1 / 2): padding "Rocks leader SR" — never pulled (A+rule.1). Errors: B+rule.1 says Pandaman was "not yet found" though it was pulled; one detail error each in A+rule.1, B-medium+rule.1, A.1. B+rule.1 (12 items) and B-medium+rule.0 (13) are the thinnest; both miss Usopp and New Gate.
- **T1dQ `tech.topics`** (42 clusters: 34 / 1 / 7): padding "CI/CD" (A+rule.1). The unclear clusters are mostly abstract category labels: A.0 ×6, A.1 ×4, A+rule.1 ×4, A+rule.0 ×2, B+rule.0 and B-medium+rule.1 ×1. Duplicates: A.0, A+rule.1 (umbrella ×2, Convex ×2), B+rule.0 (Convex ×2, Effect ×2), A.1. C+rule.0, C+rule.1, B-medium+rule.0 and B+rule.1 are specific per-tool lists.
- **T1dQ `learning.timestamps`** (67 clusters: 53 / 2 / 12): wrong facts got their own unclear clusters, all in Haiku 4.5 outputs:
  - "VS Code" for T3 Code: A+rule.1, A+rule.0, A.0;
  - "computer vision" for computer use: A+rule.1, A+rule.0, A.1, A.0;
  - Codex credited to Claude, and "Opus 3.5": A+rule.0;
  - wrong years: A+rule.0, A.0;
  - a proof step reversed: A.1; roles swapped: A.0.

  Per output (items / padding / unclear / duplicates): A+rule.0 62 / 1 / 9 / 14; A.1 52 / 1 / 3 / 7; A+rule.1 51 / 0 / 5 / 6; A.0 46 / 0 / 4 / 4. The Haiku 5.5 and Sonnet 5.5 outputs have 22–42 items each, with 0 / 0 / 0.
- **T1dQ `learning.concepts`, `tech.snippets`, `tech.patterns`**:
  - `learning.concepts` (28 clusters: 24 / 0 / 4) has detail errors: "VS Code" (A.1), a wrong year (A.1), "Astra (a design tool)" (A+rule.0), and Greptile placed over MCP (A+rule.1, A+rule.0, A.1).
  - `tech.snippets` (12 clusters: 1 / 9 / 2): the only code on screen is `npm install effect` [11:28], and A+rule.0 invented 10 of its 12 snippets.
  - `tech.patterns` (8 clusters, all supported): all of them come from A+rule.0.

### 2. Plan — Sonnet 5.5 at effort high (thinking off) vs medium

Four inputs ×2 (the cassettes + EqaMrzd9nZU). A = Sonnet 4.6 (round 1); B = Sonnet 5.5 at low and B-medium at medium (round 1, cassettes only). "vs medium" = against B-medium's run 0 on the same input (Eqa has none: the OPTIONAL medium runs were not sent).

| arm | model | calls | median / p95 s | out tok | $ / call | tabs | distinct | duplicate | required present | evidence vs A | evidence vs medium | tabs J vs A | run-to-run evidence / tabs J |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A r1 | `claude-sonnet-4-6` | 4 | 24.5 / 27.4 | 958 | 0.0356 | 3.75 | 3.5 | 0.25 | 1 | – | 0.942 | – | 1 / 0.6 |
| B r1 | `claude-sonnet-5-5` low | 4 | 9.9 / 10.7 | 1231 | 0.0515 | 4.25 | 2.75 | 1.5 | 1 | 0.904 | 0.962 | 0.692 | 1 / 0.5 |
| B-medium r1 | `claude-sonnet-5-5` medium | 4 | 11.5 / 11.9 | 1,340 | 0.0316 | 4.75 | 3.25 | 1.5 | 1 | 0.942 | 1 | 0.817 | 1 / 0.6 |
| B-high | `claude-sonnet-5-5` high | 8 | 9.4 / 12.0 | 1,226 | 0.0407 | 4 | 3.38 | 0.625 | 1 | 0.949 | 0.974 | 0.87 | 0.923 / 0.717 |

Evidence flips (keys answered differently):

- A r1: vs A none; vs medium has_ranking×1, has_steps×2
- B r1: vs A has_ranking×1, has_steps×2, is_learnable×2; vs medium is_learnable×2
- B-medium r1: vs A has_ranking×1, has_steps×2; vs medium none
- B-high: vs A has_ranking×1, has_steps×2, is_learnable×1; vs medium has_ranking×1, is_learnable×1

**Downstream — what a duplicate dataSource costs.** Every plan answer (A, B, B-medium, B-high) ran offline through the code that turns a plan into tabs and extraction groups: plan.py:300 _plan_from_response → plan.py:276 reconcile_plan_tabs (plan_reconcile.py:126) → reconcile.py:322 reconcile_plan → reconcile_pass.py:286 dedupe_step (one tab per dataSource + component) → field_requests.py:395 requested_fields (_put :340 merges a field several tabs read) → field_groups.py:156 build_groups (split_extraction.py:71, :104). Group calls of the final plan vs the same plan with each source's later tabs removed: **0 extra group calls over all 20 answers**. A second tab on a source with the same component is dropped at plan-done; one with another component stays as a second view of the same data, whose field is requested once. The plan-done reconcile reads the cassette's Haiku 4.5 memory answer (round 2's run 1); EqaMrzd9nZU: no memory input (the memory second opinion is skipped).

| video | answer | duplicate source → after plan-done | group calls | deduped | same fields |
|---|---|---|---|---|---|
| T1dQ | A.0 | no duplicate | 2 | 2 | yes |
| T1dQ | A.1 | no duplicate | 2 | 2 | yes |
| T1dQ | B.0 | tech.topics: 2 kept (envs(spot_explorer) + stack(info_grid)) | 2 | 2 | yes |
| T1dQ | B.1 | tech.topics: 3 kept (tech_stack(spot_explorer) + tools(info_grid) + workflow(step_player)), 1 dropped at plan-done | 1 | 1 | yes |
| T1dQ | B-high.0 | no duplicate | 2 | 2 | yes |
| T1dQ | B-high.1 | tech.topics: 2 kept (stack(info_grid) + harnesses(spot_explorer)) | 2 | 2 | yes |
| T1dQ | B-medium.0 | tech.topics: 1 kept (tech_stack(info_grid)), 2 dropped at plan-done | 3 | 3 | yes |
| T1dQ | B-medium.1 | tech.topics: 2 kept (environments(spot_explorer) + stack(info_grid)), 1 dropped at plan-done | 2 | 2 | yes |
| jMq8 | A.0 | food.steps: 2 kept (cook_steps(step_player) + moments(moment_track)) | 3 | 3 | yes |
| jMq8 | B.0 | food.steps: 2 kept (cook_steps(step_flow_canvas) + moments(moment_track)) | 3 | 3 | yes |
| jMq8 | B-high.0 | food.steps: 2 kept (cook_steps(step_player) + moments(moment_track)) | 3 | 3 | yes |
| jMq8 | B-high.1 | food.steps: 2 kept (cook_steps(step_flow_canvas) + moments(moment_track)) | 3 | 3 | yes |
| jMq8 | B-medium.0 | food.steps: 2 kept (cook_steps(step_flow_canvas) + moments(moment_track)) | 3 | 3 | yes |
| uC45 | A.0 | no duplicate | 3 | 3 | yes |
| uC45 | B.0 | gaming.highlights: 2 kept (pull_moments(moment_track) + hit_cards(info_grid)) | 2 | 2 | yes |
| uC45 | B-high.0 | gaming.highlights: 2 kept (pull_moments(moment_track) + hits_reference(info_grid)) | 2 | 2 | yes |
| uC45 | B-high.1 | gaming.loadout: 2 kept (deck_needs(checklist) + box_facts(info_grid)) | 3 | 3 | yes |
| uC45 | B-medium.0 | gaming.highlights: 2 kept (pull_moments(moment_track) + meta_talk(info_grid)) | 2 | 2 | yes |
| EqaM | B-high.0 | no duplicate | 3 | 3 | yes |
| EqaM | B-high.1 | no duplicate | 3 | 3 | yes |

### 3. Memory — Haiku 4.5's run-to-run noise

Round 2's two Haiku 4.5 runs per cassette (temperature 0.0, sent back to back) came back byte-identical on every cassette (output tokens 876/876, 738/738, 853/853), so the noise floor is round 1's run against round 2's: evidence agreement mean 0.974, min 0.923; outline delta up to 2 section(s) on T1dQ, jMq8. Round 1's Haiku 5.5 vs Haiku 4.5 agreement was 0.82.

| cassette | runs | evidence agree | outline delta | outline sections |
|---|---|---|---|---|
| T1dQhQAm8Tc | 0 (r1) vs 1 (r2) | 0.923 | 2 | [10, 8] |
| T1dQhQAm8Tc | 0 (r1) vs 2 (r2) | 0.923 | 2 | [10, 8] |
| T1dQhQAm8Tc | 1 (r2) vs 2 (r2) | 1 | 0 | [8, 8] |
| jMq8lEu-of0 | 0 (r1) vs 1 (r2) | 1 | 2 | [10, 8] |
| jMq8lEu-of0 | 0 (r1) vs 2 (r2) | 1 | 2 | [10, 8] |
| jMq8lEu-of0 | 1 (r2) vs 2 (r2) | 1 | 0 | [8, 8] |
| uC45_4nnEAI | 0 (r1) vs 1 (r2) | 1 | 0 | [8, 8] |
| uC45_4nnEAI | 0 (r1) vs 2 (r2) | 1 | 0 | [8, 8] |
| uC45_4nnEAI | 1 (r2) vs 2 (r2) | 1 | 0 | [8, 8] |

### 4. Cost per video — today's v9 vs stack 1 vs stack 2

From the prod benchmark ledger (`g3-prod-benchmark.json`, per call), re-priced with LiteLLM's map. Stack 1 = Sonnet 5.5 plan + vision, Haiku 5.5 extraction at effort low (B+rule) + memory ("stack 1 (medium)": the extraction at medium, B-medium+rule); stack 2 = Sonnet 5.5 plan + vision + extraction, Haiku 5.5 memory; both with the committed picks (description + translations gpt-6-luna); tier probe, quiz, synthesis and faithfulness as on prod. A moved call's prompt + cache tokens × the measured provider-counted prompt ratio of its call type, its output × the measured output ratio of the arm (plan: B-medium vs Sonnet 4.6; vision: Sonnet 5.5 vs 4.6; memory: Haiku 5.5 vs 4.5; extraction: the `+rule` arm vs Haiku 4.5 without the rule, same requests). The cache split is kept as recorded: on the 5.5 models the 512-token minimum would also cache the extraction's 1-hour block, which today's Haiku 4.5 (4,096 minimum) folds into the 5-minute write. Today re-priced vs the ledger's `usd`: max difference $0.000000 per video.

| video | today $ | stack 1 $ | stack 1 (medium) $ | stack 2 $ |
|---|---|---|---|---|
| EqaMrzd9nZU r1 | 0.157 | 0.0803 | 0.0805 | 0.191 |
| T1dQhQAm8Tc r2 | 0.0981 | 0.0628 | 0.0628 | 0.0925 |
| jMq8lEu-of0 r2 | 0.18 | 0.103 | 0.103 | 0.187 |
| uC45_4nnEAI r2 | 0.141 | 0.085 | 0.0852 | 0.163 |

EqaMrzd9nZU r1 per call type:

| call type | today $ | stack 1 $ | stack 1 (medium) $ | stack 2 $ |
|---|---|---|---|---|
| description | 0.00047 | 0.00046 | 0.00046 | 0.00046 |
| memory | 0.0116 | 0.00132 | 0.00132 | 0.00132 |
| plan | 0.0376 | 0.0333 | 0.0333 | 0.0333 |
| vision | 0.0483 | 0.0336 | 0.0336 | 0.0336 |
| extraction | 0.0512 | 0.00442 | 0.00467 | 0.115 |
| quiz | 0.00081 | 0.00081 | 0.00081 | 0.00081 |
| faithfulness | 0.00082 | 0.00082 | 0.00082 | 0.00082 |
| synthesis | 0.00044 | 0.00044 | 0.00044 | 0.00044 |
| translation_output | 0.00421 | 0.00346 | 0.00346 | 0.00346 |
| translation_rag | 0.00201 | 0.00165 | 0.00165 | 0.00165 |

T1dQhQAm8Tc r2 per call type:

| call type | today $ | stack 1 $ | stack 1 (medium) $ | stack 2 $ |
|---|---|---|---|---|
| description | 0.00013 | 0.00012 | 0.00012 | 0.00012 |
| tier_probe | 0.00243 | 0.00243 | 0.00243 | 0.00243 |
| memory | 0.0103 | 0.00115 | 0.00115 | 0.00115 |
| plan | 0.0341 | 0.0303 | 0.0303 | 0.0303 |
| extraction | 0.0122 | 0.0015 | 0.00151 | 0.0311 |
| quiz | 0.0004 | 0.0004 | 0.0004 | 0.0004 |
| vision | 0.0382 | 0.0267 | 0.0267 | 0.0267 |
| synthesis | 0.00028 | 0.00028 | 0.00028 | 0.00028 |

jMq8lEu-of0 r2 per call type:

| call type | today $ | stack 1 $ | stack 1 (medium) $ | stack 2 $ |
|---|---|---|---|---|
| description | 0.0002 | 0.00018 | 0.00018 | 0.00018 |
| memory | 0.0103 | 0.00111 | 0.00111 | 0.00111 |
| plan | 0.0317 | 0.0281 | 0.0281 | 0.0281 |
| extraction | 0.0394 | 0.00335 | 0.00355 | 0.0879 |
| vision | 0.0972 | 0.0683 | 0.0683 | 0.0683 |
| faithfulness | 0.00112 | 0.00112 | 0.00112 | 0.00112 |
| synthesis | 0.00041 | 0.00041 | 0.00041 | 0.00041 |

uC45_4nnEAI r2 per call type:

| call type | today $ | stack 1 $ | stack 1 (medium) $ | stack 2 $ |
|---|---|---|---|---|
| description | 0.00017 | 0.00015 | 0.00015 | 0.00015 |
| plan | 0.0349 | 0.0309 | 0.0309 | 0.0309 |
| extraction | 0.0352 | 0.00328 | 0.00343 | 0.081 |
| vision | 0.0688 | 0.0486 | 0.0486 | 0.0486 |
| faithfulness | 0.00162 | 0.00162 | 0.00162 | 0.00162 |
| synthesis | 0.00051 | 0.00051 | 0.00051 | 0.00051 |

Measured ratios (prompt / output, new vs old):

| call type | stack 1 | stack 1 (medium) | stack 2 |
|---|---|---|---|
| description | 0.9981 / 1.356 | 0.9981 / 1.356 | 0.9981 / 1.356 |
| translation_output | 0.9987 / 1.0382 | 0.9987 / 1.0382 | 0.9987 / 1.0382 |
| translation_rag | 0.999 / 1.0652 | 0.999 / 1.0652 | 0.999 / 1.0652 |
| plan | 1.3322 / 1.3254 | 1.3322 / 1.3254 | 1.3322 / 1.3254 |
| vision | 1.0206 / 1.0839 | 1.0206 / 1.0839 | 1.0206 / 1.0839 |
| memory | 1.2688 / 0.8998 | 1.2688 / 0.8998 | 1.2688 / 0.8998 |
| extraction | 1.3079 / 0.6556 | 1.3079 / 0.7286 | 1.3079 / 1.0339 |

Decisions recorded (D30): the tier probe stays Haiku 4.5 (final); `PIPELINE_VERSION` bumps to v10 in the picks commit, not before. No picks applied in round 2.
