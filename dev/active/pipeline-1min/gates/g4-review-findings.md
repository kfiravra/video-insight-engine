# Phase-4 review findings (recipe `review-p4.md`)

## R4a — 4.0a (LiteLLM 1.105.0; aioboto3 → aiobotocore) — 0 C / 2 W, both folded before the commit
- W1 test gap `services/summarizer/tests/test_s3_client.py` — every S3 test used a MagicMock session; the
  aiobotocore/botocore-dependent paths (`get_json` body read, `exists()` 404 → False, `get_json` NoSuchKey → None) had
  no test on a real client → AioStubber tests on a real `create_client("s3")`.
- W2 `llm_streaming.py:61` + `tests/test_llm_streaming_order.py:3` named LiteLLM 1.91 for the `_keep_arrival_order`
  seam → "1.91–1.105.0 (re-verified 2026-10-13)" (1.105 still sorts streamed chunks by a `time.time()` stamp).
- g4 questions: `file-uploads.md` skill still says aioboto3 (Kfir's file); `_carry_cache_split` is a no-op on 1.105
  (the streamed rebuild keeps the 1h/5m split itself; streamed cost = non-streamed) — keep as a guard or remove;
  `UsageRecord.litellm_version` always "" (`getattr(litellm, "version")` doesn't exist on 1.91 or 1.105 →
  `importlib.metadata.version`); `LITELLM_MODE=PRODUCTION` for local test runs (LiteLLM loads the repo `.env`).
