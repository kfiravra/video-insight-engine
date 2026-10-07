# Gate 0 — tier-probe A/B, offline (task 0.8)

**Recommendation: `anthropic/claude-haiku-4-5-20251001`, but 1b.1 must force bare JSON output.** Domain and `has_visual_demo` agreement are tied. Format differs by one video, which is noise at n = 18. Haiku is the only model that stayed under the 3 s cap on every call. Kfir decides (plan §owner list: "tier-probe model decision after the A/B").

Raw rows + summaries: [`g0-tier-probe-ab.json`](./g0-tier-probe-ab.json). Script: `scripts/tier_probe_ab.py` (+ `scripts/_tier_probe_inputs.py`, draft prompt `scripts/tier_probe_ab_prompt.txt`).

## Setup

- 18 live golden entries (`dev/golden-dataset/videos.yaml`, `disabled` skipped), one call per (video, model), sequential, model order alternating per video. Total: 36 calls.
- `temperature=0`, `max_tokens=80`, `response_format={"type":"json_object"}` (what the classifier sends today via `json_mode`), `timeout=15 s`, `num_retries=0`. LiteLLM direct, no callbacks → nothing written to Langfuse or Mongo.
- Prompt (Appendix B.1): today's `classify.txt` domain/format lists + `<classification_guidance>`; traits and reasoning removed; `<has_visual_demo>` definition added; `<output_language>` line added (D16). The static part comes first and the per-video block last, so gpt-4o-mini's automatic prefix cache can apply. Haiku's minimum cacheable size is not reached (~1.9k tokens).
- Input: title, channel, duration, YouTube category, ≤ 15 tags, description ≤ 500 chars, and three clean transcript windows of ≈ 700 chars (start/middle/end, cut on word boundaries). `[VISUAL at …]` / `[ON-SCREEN TEXT at …]` spans are stripped. A transcript ≤ 2,100 chars is split into thirds and never duplicated.

## Results

All 18 live rows:

| Model | domain % | format % | has_visual_demo % | parse fail | bare JSON | hit max_tokens | p50 ms | p95 ms | max ms | > 3 s | $/call |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-4o-mini | 72.2 | **77.8** | 83.3 | 1 | **18/18** | 0 | **1113** | 4330 | 4330 | **1** | **0.00027** |
| Haiku 4.5 | 72.2 | 72.2 | **88.9** | **0** | 0/18 | 12 | 1350 | **1635** | **1635** | 0 | 0.00224 |

Without `review-airpods-pro`, whose golden URL is wrong (see below), n = 17:

| Model | domain % | format % | has_visual_demo % | parse fail | p50 ms | p95 ms | $/call |
|---|---|---|---|---|---|---|---|
| gpt-4o-mini | 76.5 | 82.4 | 88.2 | 0 | 1126 | 4330 | 0.00027 |
| Haiku 4.5 | 76.5 | 76.5 | 88.2 | 0 | 1355 | 1635 | 0.00227 |

- **Parse fail.** A failure means the first JSON object is missing or has an invalid enum or type. Parsing uses the same rule as the pipeline's `parse_json_response` (fences and trailing prose are tolerated). gpt-4o-mini's one failure is `domain: "entertainment"` on the white-noise video. The prod classifier would return `None` on it and fall back.
- **Haiku ignores `json_object` through LiteLLM.** All 18 Haiku answers were wrapped in a ```` ```json ```` fence. 12 of 18 then went on to a `**Reasoning:**` tail until they hit `max_tokens=80` (`finish_reason=length`). The JSON object always came first and was always complete, so nothing was lost. The cost was latency and tokens: the 6 Haiku calls without the tail took 891–1032 ms (median ≈ 0.92 s), and the 12 with it took 1302–1635 ms. A Haiku probe that returns bare JSON should therefore beat gpt-4o-mini's p50.
- **Latency tail.** gpt-4o-mini: 1 of 18 calls over the 3 s cap (4.33 s, `learning-photosynthesis`) and 3 of 18 over 2 s (2.39, 2.16, 4.33 s). Haiku: max 1.64 s, even while generating the wasted tail. n = 18 is small; one outlier is weak evidence, but the frames branch caps the probe at 3 s, so the tail matters more than p50.
- **Cost.** gpt-4o-mini is 8× cheaper ($0.00027 vs $0.00224 per call), but either is under 1% of the $0.29 v8 per-video baseline.

## Per-video answers (golden → gpt-4o-mini / Haiku)

| golden id | golden domain/format | visual label | gpt-4o-mini | Haiku 4.5 |
|---|---|---|---|---|
| gaming-op17-unboxing | gaming/unboxing | T | gaming/unboxing/T | gaming/unboxing/T |
| gaming-op13-box-opening | gaming/unboxing | T | gaming/unboxing/T | gaming/unboxing/T |
| gaming-op17-set-verdict | gaming/commentary | F | **review**/commentary/F | **review**/commentary/F |
| tech-agentic-engineering | tech/walkthrough | T | tech/**tutorial**/T | tech/walkthrough/T |
| tech-react-hooks | tech/tutorial | T | tech/tutorial/T | tech/tutorial/T |
| tech-docker-basics | tech/tutorial | T | tech/tutorial/T | tech/**lecture**/T |
| food-knife-skills | food/tutorial | T | food/tutorial/T | food/tutorial/T |
| learning-photosynthesis | learning/lecture | F | **science**/lecture/**T** | **science**/lecture/**T** |
| learning-double-slit | learning/lecture | T* | **science**/lecture/T | **science**/**documentary**/T |
| science-crispr-intro | science/lecture | F | science/**documentary**/**T** | science/lecture/**T** |
| science-black-holes | science/lecture | F | science/**documentary**/F | science/**commentary**/F |
| travel-vietnam-10day | travel/documentary | T | travel/documentary/T | travel/documentary/T |
| fitness-pushup-form | fitness/tutorial | T | fitness/tutorial/T | fitness/tutorial/T |
| review-airpods-pro | review/commentary | F | parse fail (`entertainment`) | **learning**/**entertainment**/F |
| food-travel-montreal-vlog | travel/vlog | T | **food**/vlog/T | **food**/**walkthrough**/T |
| food-recipe-story-intro | food/tutorial | T | food/tutorial/T | food/tutorial/T |
| gaming-op17-static-camera | gaming/unboxing | T | gaming/unboxing/T | gaming/unboxing/T |
| fitness-7min-workout | fitness/tutorial | T | fitness/tutorial/T | fitness/tutorial/T |

\* borderline label.

## Disagreements worth noting

1. **Both models miss the same domains: 5/18 each, on the same videos.** On every row both parsed, the two models agree with each other on domain (17/17) and on `has_visual_demo` (17/17). The misses come from the prompt or the golden set, not the model:
   - `gaming-op17-set-verdict` → `review`. A TCG set/market commentary has no guidance line; only TCG *openings* are mapped to `gaming`. **1b.1:** add "TCG / collectible-card market or set commentary is `gaming`".
   - `learning-photosynthesis`, `learning-double-slit` → `science`. Both models are defensible here, since `science` is listed as "science explanations, experiments, physics, chemistry, biology". **Kfir:** re-label these golden rows as `science`, or add guidance that separates them.
   - `food-travel-montreal-vlog` → `food` (golden `travel`). This is the anchor the brief says must not get recipe components. Under v9 the domain picks the tier/playbook, so the probe will call this a food video. **1b.1/plan:** add "an eating tour of a city is `travel`, `food` is cooking/preparation", or accept `food` as the hint and rely on the plan's evidence (`has_ingredients=false`) to keep recipe tabs out. The `expectedDomain` assertion on this row will catch it either way.
   - `review-airpods-pro` — **golden-set bug:** `nMfPqeZjc2c` is now "White Noise Black Screen | Sleep, Study, Focus | 10 Hours" (36,000 s, transcript = `[steady white noise]`). Both models were right to reject it as a review. **Kfir:** fix the URL or disable the row. A 10-hour video would also blow the eval's < 35 min rule.
2. **Animated explainers** (TED-Ed photosynthesis, McGovern CRISPR) were tagged `has_visual_demo=true` by both models; my proposed label is false. Kurzgesagt black holes was correctly tagged false. The definition's "narration over generic footage" is not enough to separate them. **1b.1:** add "animations or diagrams illustrating narration = false". It matters because `true` buys the high-res vision tier.
3. **Format scatter is in lecture/documentary/commentary/walkthrough**, where the golden labels are judgement calls. Both models scored 13–14/18. Format only feeds the plan `Hint:` line and playbook choice, not the tier.

## Inputs and sources

- **Dev MongoDB holds only 3 of the 18** (`IODxDxX7oi4` push-up, `Jru5B044HOs` 7-min workout, `5VOUleaZ63E` OP17 unboxing). Even those have no description/category/tags. Title/channel/duration came from Mongo for those 3 (`mongo+summarizer`) and from the summarizer fetch for the other 15. Stored frame captions exist for 1 video only (push-up: 1 caption), so labels came from domain/format, title and transcript.
- **Metadata** for all 18 came from `extract_video_data` inside `vie-summarizer` (dev proxy, `docker exec`). Mongo was read via `docker exec vie-mongodb mongosh` because port 27017 refuses connections from the host. Inputs are cached in the session scratchpad, not the repo.
- **Transcript layer per video**, in pipeline order:
  - `s3` (2): push-up, 7-min workout.
  - yt-dlp captions as resolved (10).
  - yt-dlp captions re-picked for the golden language `en` (6): photosynthesis, double-slit, black holes, Montreal (resolver said `ar`); Vietnam (resolver said `nl`); react-hooks (first try hit http_429, second try OK).
- **Resolver bug (outside 0.8).** On this branch, `resolve_video_language` labels 4 English golden videos `ar` and 1 `nl`, then feeds the probe the Arabic/Dutch track (seen on the first fetch). This matches the memory note "TED talks labelled `ar`". The output-language line (D16) keeps the answer English, but the windows the probe reads would be foreign. Worth a tracked item before 1b.1.
- **`tech-react-hooks` has no usable transcript.** Fireship's only caption track is ASR misdetected as Polish, so the `en` track is a garbled machine translation (2,090 chars for 13 min: "from the EU Crocs and Spears abroad for PSP…"). youtube-transcript-api returns the same track. Both models still got tech/tutorial from the title, tags and description.
- Other golden rule breaks: `tech-docker-basics` is 7,818 s and `tech-agentic-engineering` 6,002 s, both against the "< 35 min" curation rule.

## Proposed `has_visual_demo` labels

These are proposed labels, not ground truth. Reasons are in `VISUAL_DEMO_LABELS` in the script and in the JSON.

- **true:** the 3 TCG openings, the 3 tech videos (code/commands on screen), knife skills, double-slit (borderline: the experiment is performed on camera), Vietnam, push-up, Montreal, the recipe, the 7-min workout.
- **false:** set-verdict (talking head over price charts), photosynthesis / CRISPR / black holes (animated explainers), white-noise.

## What 1b.1 should take from this

- Model: Haiku 4.5 (or gpt-4o-mini if Kfir prefers 8× cheaper and native JSON mode, accepting a tail that occasionally falls past the 3 s cap to the metadata rule).
- If Haiku: force bare JSON (assistant prefill `{`, a stop sequence after the object, or structured output; check which of these LiteLLM 1.81 passes to Anthropic) and add "no prose, no code fences" to the prompt. Keep `max_tokens=80`, which is enough for the 27–49-token answer.
- Prompt edits from the disagreements above: TCG commentary → `gaming`, eating tour → `travel`, animated explainer → `has_visual_demo=false`.
- Golden-set fixes for Kfir: `review-airpods-pro` URL; learning-vs-science labels on photosynthesis/double-slit.

## Spend

36 calls, **$0.0451** total (gpt-4o-mini $0.0048, Haiku $0.0403, by `litellm.completion_cost`). No pipeline runs, no prod access.

## Reproduce

```bash
services/summarizer/.venv/bin/python scripts/tier_probe_ab.py --cache-dir <scratch>/probe-inputs --dry-run      # inputs only
services/summarizer/.venv/bin/python scripts/tier_probe_ab.py --cache-dir <scratch>/probe-inputs \
    --out-json dev/active/pipeline-1min/gates/g0-tier-probe-ab.json                                            # 36 calls
services/summarizer/.venv/bin/python scripts/tier_probe_ab.py --cache-dir <scratch>/probe-inputs \
    --out-json dev/active/pipeline-1min/gates/g0-tier-probe-ab.json --rescore                                  # no calls
cd services/summarizer && .venv/bin/python -m pytest -q tests/test_tier_probe_ab.py                            # 32 passed
```
