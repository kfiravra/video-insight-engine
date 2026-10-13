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
| A | `gpt-4o-mini` | 6 | 0 | 0 | 3.1 | – | 0 | 0 | 167 | 577 | 0 / 0 | 0.00107 | 1 | 1 | – |
| B | `gpt-6-luna` reasoning none | 6 | 0 | 0 | 3.39 | – | 0 | 0 | 226 | 576 | 0 / 0 | 0.00099 | 1 | 1 | 0.632 |

Run-to-run (A, second run vs first): run_fields_agree 0.667

Run-to-run (B, second run vs first): run_fields_agree 0.919

### plan

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | fallback | tabs | distinct_sources | duplicate_sources | required_present | vsA_tabs_jaccard | vsA_evidence_agree | vsA_evidence_flips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A† | `claude-sonnet-4-6` | 4 | 0 | 0 | 23.6 | – | 0 | 0 | 958 | 22562 | 65,160 / 0 | 0.143 | 1 | 0 | 3.75 | 3.5 | 0.25 | 1 | – | – | – |
| B | `claude-sonnet-5-5` effort low | 4 | 0 | 0 | 9.51 | – | 0 | 0 | 1231 | 29844 | 66,210 / 22,070 | 0.206 | 1 | 0 | 4.25 | 2.75 | 1.5 | 1 | 0.692 | 0.904 | has_ranking×1, has_steps×2, is_learnable×2 |
| B-medium† | `claude-sonnet-5-5` effort medium | 4 | 0 | 0 | 11.3 | – | 0 | 0 | 1,340 | 29844 | 88,280 / 0 | 0.127 | 1 | 0 | 4.75 | 3.25 | 1.5 | 1 | 0.817 | 0.942 | has_ranking×1, has_steps×2 |

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
| A | `claude-haiku-4-5-20251001` | 14 | 0 | 0 | 14.6 | 0.587 | 0 | 0 | 1,580 | 10969 | 99,463 / 25,444 | 0.158 | 1 | 0 | 0.071 | 0.857 | 0 | 20.9 | – |
| B | `claude-haiku-5-5` effort low | 14 | 0 | 0 | 5.11 | 0.849 | 0 | 0 | 996 | 14493 | 129,457 / 33,081 | 0.0142 | 1 | 0 | 0 | 0.821 | 0 | 14 | 0.717 |
| B-medium† | `claude-haiku-5-5` effort medium | 14 | 0 | 0 | 7.1 | 0.794 | 0 | 0 | 1,048 | 14493 | 142,688 / 19,850 | 0.0121 | 1 | 0 | 0 | 0.821 | 0 | 16.4 | 0.866 |

† the arm's first call read a cache written before this run (cost is warm)

Run-to-run (A, second run vs first): run_filled_delta 0, run_items_ratio 1.04

Run-to-run (B, second run vs first): run_filled_delta 0, run_items_ratio 0.855

Run-to-run (B-medium, second run vs first): run_filled_delta 0, run_items_ratio 0.904

### quiz

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | questions | dropped | usable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 2 | 0 | 0 | 4.98 | – | 0 | 0 | 336 | 4267 | 0 / 0 | 0.00168 | 1 | 4 | 0 | 1 |
| B | `gpt-6-luna` reasoning none | 2 | 0 | 0 | 6.88 | – | 1 | 0 | 530 | 4266 | 0 / 0 | 0.0016 | 1 | 5 | 0 | 1 |

### vision

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | described_share | kept_count | cards_read | vsA_selection_jaccard | vsA_scene_agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `claude-sonnet-4-6` | 4 | 0 | 0 | 16.8 | – | 0 | 0 | 992 | 10235 | 0 / 0 | 0.171 | 1 | 1 | 7.5 | 1.25 | – | – |
| B | `claude-haiku-5-5` effort low | 4 | 0 | 0 | 4.49 | – | 0 | 0 | 1,024 | 10426 | 0 / 0 | 0.00583 | 1 | 1 | 7 | 1 | 0.928 | 0.768 |
| C | `claude-sonnet-5-5` effort low | 4 | 0 | 0 | 8.6 | – | 0 | 0 | 1,075 | 10426 | 0 / 0 | 0.119 | 1 | 1 | 7.25 | 1.25 | 0.964 | 0.938 |

### translation_output

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | echo | left_share | length_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 7 | 0 | 0 | 10.2 | – | 0 | 0 | 695 | 848 | 0 / 0 | 0.00373 | 1 | 0 | 0.044 | 0.808 |
| B | `gpt-6-luna` reasoning none | 7 | 0 | 0 | 8.95 | – | 0 | 0 | 722 | 847 | 0 / 0 | 0.00307 | 1 | 0 | 0.044 | 0.855 |

### translation_rag

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | echo | left_share | length_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 4 | 0 | 0 | 5.42 | – | 0 | 0 | 586 | 1062 | 0 / 0 | 0.00201 | 0.75 | 0 | 0 | 1.34 |
| B | `gpt-6-luna` reasoning none | 4 | 0 | 0 | 7.11 | – | 0 | 0 | 624 | 1061 | 0 / 0 | 0.00173 | 1 | 0 | 0 | 1.35 |

### faithfulness

| arm | model | calls | err | refusals | p50 s | TTFT p50 s | >timeout | trunc | out tok | max in tok | cache r / w tok | cost $ | parsed | grounded | vsA_agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | `gpt-4o-mini` | 18 | 0 | 0 | 1.01 | – | 0 | 0 | 35.2 | 5614 | 75,520 / 0 | 0.00862 | 1 | 0.889 | – |
| B | `gpt-6-luna` reasoning none | 18 | 0 | 0 | 1.57 | – | 0 | 0 | 43.9 | 5613 | 0 / 0 | 0.012 | 1 | 0.444 | 0.556 |

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
