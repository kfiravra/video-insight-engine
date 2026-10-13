# Brief 4.0a — LiteLLM 1.91.0 → 1.105.0 (first and alone)

Kfir 2026-10-13: "LiteLLM upgrade with suites + replay byte-identical before any other change."
Task row: tasks.md 4.0 a. Read `agent-rules.md` (phase-4 section first) before anything else.
Skill: `.claude/skills/backend-python/SKILL.md` + `resources/ai-integration.md` (read before code).
Scratch (coordinator's session): `S=/tmp/claude-1000/-home-kfir-projects-video-insight-engine/af60d8e4-09b5-430c-92c1-3a4b950e7ba0/scratchpad`.

## Facts (coordinator, checked 2026-10-13)
- Latest stable = **litellm 1.105.0** (PyPI 2026-10-11; abi3 wheels now). It is also the only version
  that satisfies Kfir's "≥ 1.105.0".
- New hard deps vs 1.91.0: `boto3>=1.43.1`, `aiohttp>=3.14.2`, `httpx[http2]`, `pyyaml`, `filelock`,
  `packaging`, `huggingface-hub`, `pydantic>=2.11`, `pydantic-settings>=2.14.1`, `jsonschema`.
  Both locks already satisfy all of them EXCEPT `aiohttp` (3.14.1) and `boto3` (summarizer 1.40.61;
  the assistant has none).
- **aioboto3 blocks the bump.** Every LiteLLM ≥ 1.103 requires `boto3>=1.43.1`; aioboto3 is abandoned
  at 15.5.0 (2025-10-30), which pins `aiobotocore[boto3]==2.25.1` → `boto3<1.40.62`. aiobotocore
  itself is current (3.9.2, `botocore>=1.43.101,<1.43.107`). **Decision (coordinator; reported in g4
  as a deviation): replace aioboto3 with aiobotocore in the summarizer in this same commit.** aioboto3
  only wraps aiobotocore; the code uses `aioboto3.Session()` + `session.client("s3", …)` in ONE
  module, `services/summarizer/src/services/media/s3_client.py` (no resource API, no
  `upload_fileobj`/managed transfer — re-verify) → `aiobotocore.session.get_session()` +
  `session.create_client("s3", …)` (async context manager, same client methods). The sync
  `boto3.client` in that file stays (boto3 1.43.x).
- Assistant: a constrained compile changes exactly aiohttp → 3.14.3, + boto3/botocore 1.43.112,
  + jmespath, litellm → 1.105.0, + python-dateutil, + s3transfer 0.19.2, + six (re-run to confirm).
- Lock method (both locks are HAND-EDITED — never regenerate one wholesale; opencv<5 pin etc.):
  constrained `uv pip compile` that keeps every other pin, then apply ONLY the changed lines by hand,
  with correct `# via` comments for changed/added/removed entries and the header unchanged. The
  coordinator's attempt is in `$S/lock/` (`cons-<svc>.txt` = the lock's pins minus the freed set,
  `pin-<svc>.txt` = `litellm==1.105.0`); command shape = the lock header's command +
  `$S/lock/pin-<svc>.txt -c $S/lock/cons-<svc>.txt --extra-index-url https://download.pytorch.org/whl/cpu`.
  For the summarizer, free `aioboto3` (removed), `aiobotocore`, `aiofiles`, `aioitertools`, `boto3`,
  `botocore`, `s3transfer`, `jmespath`, `wrapt`, `aiohttp` — only what the resolver needs; report the
  final diff.
- No local assistant venv exists. Build one like CI (`.github/workflows/ci.yml` assistant job):
  `uv venv -p 3.12 services/assistant/.venv` → `uv pip install -p services/assistant/.venv/bin/python
  --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match -r
  services/assistant/requirements.lock` → `pytest-cov` → `--no-deps -e packages/llm-common`.
  (`.venv` is gitignored — check.) Heavy: do it under the lock.

## Steps (in this order)
1. **Before touching anything**: record the request baseline on HEAD with today's venv (1.91.0):
   `cd /home/kfir/projects/video-insight-engine && services/summarizer/.venv/bin/python $S/tools/capture.py --out $S/p40a/before`
   and the same with `--parallel-false`. (Zero network, zero spend; read its docstring.)
2. Requirements + locks: `services/summarizer/requirements.txt` (`aioboto3` → `aiobotocore>=3.9`;
   keep/align the `botocore` line), `packages/llm-common/pyproject.toml` (`litellm>=1.105.0` — CI's
   llm-common/admin jobs resolve from it), both `requirements.lock` files. Same pin in CI and prod =
   the locks (Dockerfiles + CI install from them).
3. Code: `s3_client.py` swap + its tests (`tests/test_s3_client.py` and any test that mocks
   `aioboto3`); messages/docstrings that say "aioboto3 not installed" (`transcript_store.py`,
   `services/summarizer/scripts/backfill-transcripts.py`, `scripts/backfill-transcripts.py`) and the
   one-off `scripts/migrate-s3-keys.py` (it imports aioboto3 — swap the same way). grep the repo
   (docs included) for `aioboto3` and list what you changed / left.
4. Install into the summarizer venv: `uv pip install -p services/summarizer/.venv/bin/python
   --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match -r
   services/summarizer/requirements.lock`, then uninstall `aioboto3` (and anything only it needed)
   so nothing can import it by accident; `--no-deps -e packages/llm-common` again if needed. Build
   the assistant venv (above). `pip check` / `uv pip check` on both → report.
5. LiteLLM API drift ("removed helpers fixed like c8bbb1f"): pyright 1.1.407 as CI runs it
   (summarizer changed/LLM files, assistant `--project .`, llm-common) + the full suites. Fix only
   what the bump breaks, minimally, using LiteLLM's own predicates/helpers. Read the 1.105.0 source
   for the seams we depend on and report each: `_keep_arrival_order` in
   `services/summarizer/src/services/llm_streaming.py` (LiteLLM sorted streamed chunks by
   `time.time()` — does 1.105 still do that, does our hook still apply?), the usage fields we read
   (`cache_creation_input_tokens`, `cache_read_input_tokens`, `cache_creation_token_details` /
   `ephemeral_1h_input_tokens` / `ephemeral_5m_input_tokens`, `prompt_tokens_details.cached_tokens`),
   `completion_cost`, exception classes we catch, `drop_params`/`UnsupportedParamsError`, the aiohttp
   transport (`DISABLE_AIOHTTP_TRANSPORT`), and how the gpt-5 temperature guard
   (`llm_provider.py`, c8bbb1f) behaves on 1.105.
6. After: capture again (`--out $S/p40a/after`, both modes) → `diff -r` before/after must be EMPTY
   (every request of all 3 cassettes, both modes). Then `bash $S/tools/replay3.sh` (×3) under the
   lock → no divergence / mismatch (rc=1 from the phase-0 tolerance vs v8 walls is expected).
7. Full suites under the lock, one at a time: summarizer (incl. replay), assistant, llm-common
   (`cd packages/llm-common && ../../services/summarizer/.venv/bin/python -m pytest -q` if its tests
   run there — else say how you ran them). api/web untouched — don't run them.
8. Live smoke, allowed spend ≤ $0.02 (dev keys from the repo `.env`, never printed): through
   `LLMProvider` in the summarizer venv — one Anthropic Haiku 4.5 non-streamed call, one streamed
   call, one OpenAI gpt-4o-mini call → each returns text, usage + `costUsd` non-zero. S3 smoke on the
   dev bucket with the new `S3Client`: put → head/get → delete one key under `pipeline-1min-smoke/`
   (delete it at the end; report the bucket name only, no credentials).
9. STOP — report per agent-rules ("When you stop"), uncommitted. Include: the lock diffs, the
   capture diff result (counts), replay ×3 result, suite counts (summarizer / assistant /
   llm-common), pyright results, the seam-by-seam LiteLLM report (step 5), smoke results + spend,
   what the coordinator must do for dev containers (image rebuild list). Commit only after the
   review: `p4.0a chore(deps): litellm 1.105.0; aioboto3 → aiobotocore` (body: what/why,
   output-changing no — prove with the capture diff, tests).

Do NOT touch: models, temperature/thinking/effort guards (that is 4.0b), prompts, config defaults,
compose files, the dev stack (no container rebuilds/restarts — coordinator), `dev/active/**`.
