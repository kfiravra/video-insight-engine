# Brief 4.0b — model-keyed guards in the LLM plumbing (after 4.0a is committed)

Kfir 2026-10-13 (tasks.md 4.0 b, verbatim): "Guards in the LLM plumbing, keyed by model, not by call
site: never send temperature / top_p / top_k to a model that rejects them (Haiku 5.5 returns 400 —
LiteLLM's own predicates, as in the gpt-5 guard); thinking off + effort low on every pipeline call
(plan at medium only if the A/B shows a quality loss); no assistant prefill anywhere — a prompt that
forces JSON with a prefill moves to structured outputs; stop_reason "refusal" → same-call fallback to
the previous model, tagged in the ledger and `pipeline.timing`, never retried on the same model;
content blocks selected by type, never by position."
Read first: `agent-rules.md` (phase-4 section), tasks.md 4.0 (b + "Checked against the code"),
context.md D29 + **"4.0c docs facts"** (the model behaviour you code against — verified docs; do not
re-derive from memory), `.claude/skills/backend-python/SKILL.md` + `resources/ai-integration.md`.
Scratch: `S=/tmp/claude-1000/-home-kfir-projects-video-insight-engine/af60d8e4-09b5-430c-92c1-3a4b950e7ba0/scratchpad`.

## Binding rule: today's models are untouched
Dev and prod run Haiku 4.5 / Sonnet 4.6 / gpt-4o-mini / gpt-4o until 4.0d changes the defaults. Every
guard must be a no-op for them: the FULL request kwargs of every cassette request stay byte-identical
(`$S/tools/capture.py`, both modes, vs `$S/p40a/after` — the post-4.0a capture). Only a 5.x / gpt-6
model gets new params.

## What to build (design it; these are the required behaviours)
1. **Haiku 5.5 in LiteLLM's model map.** LiteLLM 1.105.0's bundled map lacks `claude-haiku-5-5`, and
   LiteLLM keys the Anthropic thinking / effort / `output_config` / structured-output mapping and
   `completion_cost` on map flags. Register an entry (docs prices: $0.10 / $0.50 per MTok ≤ 100K,
   $0.50 / $2.50 above via `input_cost_per_token_above_100k_tokens` / `output_…`; cache read 0.1×,
   5m write 1.25×, 1h write 2× via `cache_creation_input_token_cost_above_1hr`; 1M / 128K;
   `prompt_cache_min_tokens` 512; capability flags modelled on the map's `claude-sonnet-5-5` entry,
   corrected for Haiku 5.5 per the docs facts — e.g. `disabled` thinking IS accepted ≤ high,
   forced tool use IS accepted) ONLY when the loaded map lacks it (a later LiteLLM / the remote map
   wins). Report which map source is live in the venv (`litellm` remote fetch vs bundled backup; the
   project does not set `LITELLM_LOCAL_MODEL_COST_MAP`) — do not change env (question for g4).
2. **Sampling guard** — 4.0a found the c8bbb1f guard DRIFTED on 1.105: LiteLLM now allows a non-1
   temperature on gpt-5.x / gpt-6 only when the effort it will actually send resolves to "none"
   (`OpenAIGPT5Config.effort_resolves_to_none(model, effort)`), while our `_rejects_temperature`
   only asks whether the model supports "none" → on gpt-6-luna we would send temperature and get
   `UnsupportedParamsError`. Key the guard on the effort the request really carries (after item 3).
   Keyed by model via LiteLLM's own predicates/map flags
   (`supports_sampling_params`, the gpt-5/gpt-6 reasoning-effort predicate already used by
   `_rejects_temperature` in `services/llm_provider.py`, c8bbb1f): drop temperature / top_p / top_k for
   a model that rejects them. Today's models: unchanged.
3. **Thinking off + effort low**, keyed by model: a model that thinks by default gets its own
   "off" shape + `output_config={"effort": "low"}` — Haiku 5.5 `thinking={"type": "disabled"}`;
   Sonnet 5.5 `thinking={"type": "between_tools"}` (the only off shape it accepts; no other field
   inside `thinking`; never sent to another model); OpenAI reasoning models (`gpt-6-luna`)
   `reasoning_effort="none"` where the map says `supports_none_reasoning_effort` (thinking off). Prove
   the WIRE body offline (zero spend): run LiteLLM's own request transformation for each model
   (Anthropic transform / `get_optional_params`) and assert `thinking`, `output_config.effort`, no
   sampling params; check what `AnthropicModelInfo.translate_legacy_thinking_for_adaptive_model` and
   `_validate_effort_for_model` do to `disabled` / `between_tools` on 1.105 (LiteLLM may not know
   `between_tools` — if it rewrites or rejects it, find the supported way through, report it).
   The plumbing takes an optional per-call effort (default low) so 4.0d can try plan at medium without
   a new setting; no call site passes it in this task.
4. **No assistant prefill:** a guard that refuses a request whose last message is `assistant` (clear
   error, before LiteLLM) + a test over every cassette request (replay) that none ends with an
   assistant turn. grep (2026-10-13) found no prefill in the summarizer / llm-common builders —
   re-verify (incl. `description_analyzer.py`, vision, faithfulness, translation, quiz). If one exists
   and forces JSON → structured outputs (`response_format` json_schema → LiteLLM `output_config.format`
   where `supports_native_structured_output`); report it first.
5. **Refusal → previous model, same call, once.** Detect a refusal however LiteLLM 1.105 surfaces it
   (Anthropic `stop_reason: "refusal"` + `stop_details` — check `finish_reason` mapping and
   `provider_specific_fields`; OpenAI `message.refusal`), streamed (mid-stream: discard the partial
   output) and non-streamed. Then re-send the SAME call on the previous model, with that model's params
   (no `between_tools` / `output_config` / `reasoning_effort` it rejects; sampling as the caller
   asked). Previous-model table = a constant in `config.py` (Kfir: "Haiku 4.5 and Sonnet 4.6 stay in
   config as fallbacks"): `claude-haiku-5-5 → claude-haiku-4-5-20251001`, `claude-sonnet-5-5 →
   claude-sonnet-4-6`, `gpt-6-luna → gpt-4o-mini` (provider prefixes as config uses them). Never
   retried on the same model; a refusal on the previous model → a typed refusal error into the
   caller's existing failure path (no cross-provider hop). Tag it: the ledger row (`llm_usage`) and
   `pipeline.timing` (llmCalls `fallbackUsed` + the refused model + category; a failure row
   `error: "Refusal"`; a `refusals` count in `counts`). Decide and document how this composes with
   D4's cross-provider error fallback in `utils/llm_retry.py` (errors keep D4; refusals take this
   path only).
6. **Content blocks by type:** our readers take text only — `message.content` (LiteLLM joins text
   blocks) and streamed `delta.content`; thinking (`reasoning_content`, `thinking_blocks`) is never
   read as the answer. grep for positional access to provider content; tests with a LiteLLM-shaped
   response / stream whose thinking block comes FIRST (non-streamed + streamed, incl. vision).
7. **Identity test keys on the prompt bytes** (tasks.md 4.0 e, needed now because 1–3 add kwargs):
   `tests/replay/test_replay_extraction_identity.py` `canonical_request` → messages (all content
   blocks incl. `cache_control`) + system only — not `model`, sampling, thinking, effort, max_tokens.
   Keep its baseline semantics (the `EXTRACTION_PARALLEL=false` single call stays byte-identical).
8. Tests: table-driven per model (Haiku 4.5, Sonnet 4.6, gpt-4o-mini, gpt-4o, Haiku 5.5, Sonnet 5.5,
   gpt-6-luna) — sampling kept/dropped, thinking/effort shape, wire body via LiteLLM's transform,
   refusal fallback (non-streamed + streamed, Anthropic + OpenAI shapes, refusal on the fallback
   too), prefill guard, block-type reading. Behaviour, not implementation; "should … when …".
9. Live probe, allowed spend ≤ $0.05 (dev keys, never printed): one small call each on
   `anthropic/claude-haiku-5-5`, `anthropic/claude-sonnet-5-5`, `openai/gpt-6-luna` through the
   plumbing with the guards → 200, text answer, usage + `costUsd` non-zero and priced (Haiku 5.5 via
   the registered entry), no thinking output; one Haiku 5.5 call with a 1h + 5m `cache_control` prefix
   ≥ 600 tokens sent twice → second call reads the cache (512 minimum). A 400 or an access error =
   report it verbatim, don't work around it.

Files (expected; split a file that would pass 500 lines): `services/llm_provider.py`,
`utils/llm_retry.py`, `services/llm_streaming.py`, `services/llm_telemetry.py`,
`services/pipeline/pipeline_timing.py`, a new small module for the model rules if it keeps files
< 500 lines, `config.py` (the previous-model constant only — no new env setting), their tests,
`tests/replay/test_replay_extraction_identity.py`. Not: model defaults / settings / compose / `.env`
(that is 4.0d), prompts, call sites' prompts.

Flow: implement + tests + targeted runs → offline wire-body proof → capture (both modes) vs
`$S/p40a/after` = identical full kwargs for today's models → live probe → STOP uncommitted and report
(agent-rules "When you stop" + the map source, the LiteLLM `between_tools` finding, the refusal
surface on 1.105, probe results + spend). After the review: full summarizer suite + llm-common under
the lock → commit `p4.0b feat(summarizer): model-keyed LLM guards …`.
