"""Configuration settings for vie-summarizer service."""

import logging
from functools import cache
from typing import ClassVar, Literal

from pydantic import Field
from pydantic_settings import BaseSettings

from src.shared_config.pipeline_version import get_pipeline_version

_DEFAULT_INTERNAL_SECRET = "dev-internal-secret-change-me"


# Model mapping for each provider
MODEL_MAP = {
    "anthropic": {
        "default": "anthropic/claude-sonnet-4-6",
        "fast": "anthropic/claude-haiku-4-5-20251001",
    },
    "openai": {
        "default": "openai/gpt-4o",
        "fast": "openai/gpt-4o-mini",
    },
    "gemini": {
        "default": "gemini/gemini-2.5-flash",
        "fast": "gemini/gemini-2.5-flash-lite",
    },
}


def get_model(provider: str = "anthropic", tier: str = "default") -> str:
    """Get model name for provider and tier."""
    provider_models = MODEL_MAP.get(provider, MODEL_MAP["anthropic"])
    return provider_models.get(tier, provider_models["default"])


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Environment
    ENVIRONMENT: str = ""  # development, test, staging, production

    # MongoDB
    MONGODB_URI: str = "mongodb://vie-mongodb:27017/video-insight-engine"

    # LLM Provider Configuration
    LLM_PROVIDER: str = "anthropic"  # anthropic, openai, gemini
    LLM_FAST_PROVIDER: str | None = None  # Optional separate provider for fast model
    LLM_FALLBACK_PROVIDER: str | None = None  # Optional fallback provider
    LLM_MODEL: str | None = None  # Override default model (e.g., "anthropic/claude-sonnet-4-6")
    LLM_FAST_MODEL: str | None = None  # Override fast model

    # Per-stage model overrides (None → fall back to LLM_FAST_MODEL).
    # Defaults reflect the 2026-05-19 fast-tier benchmark winners
    # (reports/fast-model-bench-20260519-074647.md). Override via env to test
    # alternatives without rebuilding.
    # Only the two stages with material wins (enrichment quality +35%,
    # vision -67% cost) are pinned. Classifier/translation stay on the
    # default fast tier (gpt-4o-mini); the Gemini Flash-Lite savings were
    # fractions of a cent — not worth adding a third provider dependency.
    LLM_CLASSIFIER_MODEL: str | None = None
    LLM_CHAPTER_DETECT_MODEL: str | None = None
    LLM_DESCRIPTION_MODEL: str | None = None
    LLM_SYNTHESIS_MODEL: str | None = None
    LLM_ENRICHMENT_MODEL: str | None = "anthropic/claude-haiku-4-5-20251001"
    LLM_TRANSLATION_MODEL: str | None = None
    LLM_VISION_MODEL: str | None = "anthropic/claude-haiku-4-5-20251001"
    # Extraction model override. Default `None` falls through to the primary
    # model (claude-sonnet-4-6 in production), preserving the current cost
    # profile. Set to "anthropic/claude-haiku-4-5-20251001" or another model
    # to A/B against Sonnet without rebuilding. Unlike EXTRACTION_USE_FAST_FIRST
    # (which routes the FIRST pass to the fast tier and escalates retries to
    # primary), this override pins ALL extraction passes — including retries —
    # to the chosen model. Use when you want a clean A/B with no escalation
    # noise; use EXTRACTION_USE_FAST_FIRST when you want a cost-saving first
    # pass with Sonnet as a safety net on failure.
    LLM_EXTRACTION_MODEL: str | None = None

    # Provider API Keys (set for providers you use)
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None  # For Gemini (LiteLLM expects this name)

    # API callback
    API_URL: str = "http://vie-api:3000"
    INTERNAL_SECRET: str = Field(default=_DEFAULT_INTERNAL_SECRET, repr=False)

    # Limits
    # WARNING: 600 min (10 hours) is the hard cap.  Audio transcription of very
    # long videos is expensive ($3.60 Whisper + up to 30 min processing for 10h).
    # Deploy rate-limiting on the API gateway before exposing to untrusted users.
    MAX_VIDEO_DURATION_MINUTES: int = 600
    MIN_VIDEO_DURATION_SECONDS: int = 60
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_NUM_RETRIES: int = 2
    LLM_MAX_TOKENS: int = 4096
    LLM_FAST_MAX_TOKENS: int = 4096

    # Token limits for LLM prompts (large safety nets - modern LLMs handle full transcripts)
    MAX_TRANSCRIPT_CHARS: int = 500000  # ~500K chars = well within all LLM limits
    MAX_CHAPTER_CHARS: int = 100000  # ~100K chars per chapter

    # Chapter-based chunked extraction
    CHAPTER_BATCH_SIZE: int = 3
    # Videos longer than this run chapter detection + chunked-batch extraction
    # (parallel Sonnet calls). Lowered 1800→900 on 2026-05-24 to halve the
    # wallclock for 15–30 min videos at the cost of a small chapter_detect
    # call (~$0.01) per newly-eligible video. Cost is roughly neutral once
    # prompt caching kicks in across the parallel batches.
    CHUNKED_EXTRACTION_THRESHOLD: int = 900  # seconds (15 min)
    MAX_TOKENS_PER_BATCH: int = 50000  # conservative token limit per extraction batch
    # Hard cap on the wall-clock span a single extraction batch may cover.
    # A batch can fit MAX_TOKENS_PER_BATCH yet still span hours (a talky 3h
    # segment is ~49K tokens) — and the fast model then front-loads and drops
    # the tail (a 4.5h video produced output only up to 1:34). Bounding span to
    # 40 min keeps each batch densely coverable within the 16K output budget.
    MAX_MINUTES_PER_BATCH: int = 40
    CHUNKED_EXTRACTION_TIMEOUT: float = 300.0  # 5 min — per-batch timeout for chunked extraction
    # Parallel concurrency for the chunked extraction batches. Defaults to 2
    # because production has hit Anthropic 529 (overloaded) at 3 concurrent
    # Sonnet calls; 2 keeps tail latency stable while still ~halving wall time.
    EXTRACTION_PARALLEL_BATCHES: int = 2
    # Force-split target — kept aligned with EXTRACTION_PARALLEL_BATCHES * 2
    # so a single round of the parallel limit drains half the chunks.
    EXTRACTION_FORCE_SPLIT_CHUNKS: int = 4
    # Phase 4 / P2: route the *first* extraction pass through the fast model.
    # Default OFF — flip via env after the corpus eval (scripts/eval_extraction_models.py)
    # confirms quality delta < 0.05 across all primary domains. Retries always
    # escalate to the primary model regardless of this flag.
    EXTRACTION_USE_FAST_FIRST: bool = False

    # Timeout constants for pipeline stages
    TRANSCRIPT_FETCH_TIMEOUT: float = 30.0
    GEMINI_UPLOAD_TIMEOUT: float = 300.0

    # SponsorBlock API timeout
    SPONSORBLOCK_TIMEOUT: float = 5.0

    # The only proxy setting. One exit for every YouTube-facing request: yt-dlp
    # downloads (scene detection, local 720p fallback, stream-URL lookup,
    # whisper/gemini audio), metadata, playlists and caption fetches (timedtext
    # + caption API). Full URL with credentials, e.g. http://user:pass@host:port.
    # Blank = direct. Needed on the EC2 box, whose datacenter IP is bot-checked
    # (2026-09); the exit must be a residential/ISP IP — datacenter proxies are
    # blocked the same way. ffmpeg seeks on a looked-up stream URL would leave
    # from the host IP unproxied and 403, so with a proxy set the frame pipeline
    # never looks one up: hi-res frames come from a proxied local 720p download
    # started alongside scene detection, and moment fills (>=3 frameless moments)
    # download it again during assembly — two downloads through the proxy.
    YOUTUBE_PROXY_URL: str | None = None
    # Sticky exits behind the proxy gateway, addressed by the username suffix
    # (Webshare: USERNAME-1 … USERNAME-N). A caption 429 is IP-scoped, so both
    # caption fetches (timedtext + caption API) retry on the NEXT exit before
    # giving up on captions.
    # 1 (or a username without a -N suffix) = no rotation, retry on the same exit.
    YOUTUBE_PROXY_EXIT_COUNT: int = 1

    # Whisper fallback (Phase 4 - for videos without captions)
    # Max duration set to 600 min (10 hours) to support ultra-long content.
    # Cost implication: 10-hour video ≈ $3.60 Whisper API + up to 30 min processing.
    WHISPER_ENABLED: bool = True
    WHISPER_MAX_DURATION_MINUTES: int = 600
    # Per-request OpenAI HTTP timeout for one chunk (upload + transcription of a
    # ≤24MB chunk). Bounds chunk 0 (the per-chunk *deadline* only guards index>0),
    # so a stalled call fails in minutes instead of hanging to the outer backstop.
    WHISPER_CLIENT_TIMEOUT_SECONDS: float = 300.0
    # SDK default is 2; with a 300s timeout that doubles worst-case latency past
    # the backstop margin. One retry covers a transient blip without unbounded wait.
    WHISPER_MAX_RETRIES: int = 1
    # Chunks of a long video transcribe concurrently (bounded). Mirrors the
    # extraction Semaphore(3) pattern; respects OpenAI per-key rate limits.
    WHISPER_CHUNK_CONCURRENCY: int = 3

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"  # "console" or "json"

    # ─── Langfuse observability ─────────────────────────────────────────
    # Leave keys blank to disable — every Langfuse helper is no-op when
    # init returns None, so tests and offline dev never hit the network.
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"
    # Faithfulness judge: fraction of extracted items to spot-check (0 disables).
    LANGFUSE_FAITHFULNESS_SAMPLE_RATE: float = 0.2
    # User-id propagation policy for Langfuse traces. Options:
    #   - "identity" (default): forward the internal user id as-is.
    #   - "hash":     SHA-256 of "<salt>::<id>" truncated to 16 chars.
    #   - "omit":     never include user_id in traces.
    # When compliance forbids per-user identifiers leaving the host, use
    # "omit"; for support-driven debugging keep "identity".
    LANGFUSE_USER_ID_MODE: str = "identity"
    LANGFUSE_USER_ID_HASH_SALT: str = ""
    # Where prompt templates come from. "registry": the Langfuse `production`
    # label wins over the local .txt whenever keys are set. "disk": always the
    # local .txt, so dev prompt edits take effect without re-registering.
    # Dev-only — ignored in production/staging (see prompts_from_disk()).
    PROMPT_SOURCE: Literal["registry", "disk"] = "registry"

    # ─── Sentry error tracking ──────────────────────────────────────────
    # Empty DSN -> SDK no-ops. Lets dev/CI run without a live project.
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str | None = None  # Falls back to ENVIRONMENT when unset.
    SENTRY_RELEASE: str | None = None  # Usually the deploy SHA.
    # 0 disables performance tracing (errors still capture). 0.1 in prod, 1.0 in dev.
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

    # S3 — uses existing vie-transcripts bucket for all media (frames, transcripts, audio)
    S3_BUCKET: str = "vie-transcripts"
    # Must cover the api gateway's re-sign window (FRAME_URL_TTL_SECONDS=21600);
    # a shorter expiry here means SSE-delivered URLs 403 mid-session.
    S3_PRESIGNED_URL_EXPIRY: int = 21600  # Presigned URL validity in seconds (6 hours)
    AWS_REGION: str = "us-east-1"
    AWS_ENDPOINT_URL: str | None = None  # Optional: custom endpoint for CI/CD testing (LocalStack)
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None

    # Qdrant vector DB (for transcript chunk storage / RAG)
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_ENABLED: bool = True

    # Embedding model — drop-in alternatives must keep VECTOR_SIZE=384 (e.g. BAAI/bge-small-en-v1.5).
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"

    # Redis response cache
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_ENABLED: bool = True
    REDIS_CACHE_TTL: int = 60 * 60 * 24 * 30  # 30 days

    # YouTube caption-endpoint 429 negative cache: after a rate-limit, later
    # pipeline runs skip the caption API for this long and go straight to
    # audio transcription (the 429 is IP-scoped, so retries within the window
    # are doomed and only amplify the throttle). <= 0 disables the marker.
    CAPTION_429_NEG_TTL_SECONDS: int = 900  # 15 min

    # Pipeline event broker (Redis Streams).
    # Lock TTL must outlive the longest realistic pipeline run; stream TTL
    # gives late joiners a chance to drain after the producer finishes.
    PIPELINE_LOCK_TTL_SECONDS: int = 600  # 10 min — auto-expire if producer crashes

    # Stall sweeper (src/services/stall_sweeper.py) — runs in the HTTP process
    # only. A ``processing`` row whose ``updatedAt`` is older than the threshold
    # AND whose producer lock has expired is flipped to ``failed`` (so attachers
    # stop waiting and the user can retry) and a ``pipeline_stalled`` alert is
    # written + delivered. Threshold mirrors the API's lazy re-dispatch guard
    # (api/src/services/video.service.ts PIPELINE_STALL_THRESHOLD_MS).
    STALL_SWEEP_ENABLED: bool = True
    STALL_SWEEP_INTERVAL_SECONDS: int = 300
    STALL_THRESHOLD_MINUTES: int = 30
    PIPELINE_STREAM_TTL_SECONDS: int = 120  # 2 min retention after DONE
    PIPELINE_STREAM_MAXLEN: int = 2000  # MAXLEN ~ for XADD ring-buffer
    # Keepalive cadence while a phase runs silently (e.g. multi-minute Whisper).
    # Must stay well under the API gateway's undici bodyTimeout (300s) and any
    # 60s proxy read timeout, or the SSE proxy hop aborts an idle-but-live stream.
    SSE_HEARTBEAT_SECONDS: float = 12.0

    # Advanced transcript cleaning (spaCy + TF-IDF)
    TRANSCRIPT_CLEANING_ENABLED: bool = True
    # Budget for the advanced-cleaning pass. The first call per worker process
    # pays a spaCy cold-start inside this window; raise if cold-start timeouts
    # show up in logs ("Advanced transcript cleaning timed out").
    TRANSCRIPT_CLEANING_TIMEOUT: float = 30.0

    # Sound-only force-English: when a music-category video has words-per-second
    # below this threshold, override the detected language to English. Whisper
    # hallucinates languages on instrumental audio; vocal songs stay well above
    # this floor (a sparse vocal song is ~0.33 wps, an instrumental is ~0.03).
    MUSIC_LANGUAGE_FORCE_EN_WPS: float = 0.15

    # Scene-based frame extraction (additive to timestamp extraction)
    SCENE_EXTRACTION_ENABLED: bool = True
    SCENE_THRESHOLD: float = 0.3
    SCENE_MAX_FRAMES: int = 100
    # Detection pass renders from the worst-quality download; these only shape
    # the low-res JPEGs used for scoring/OCR (and the fallback if hi-res fails).
    SCENE_DETECT_SCALE_WIDTH: int = 1024
    SCENE_JPEG_QUALITY: int = 4  # ffmpeg -q:v (2 = near-lossless, 31 = worst)

    # Hi-res refinement: re-extract the ~25 SELECTED frames at 720p before S3
    # upload + vision analysis. Proxyless: stream-URL seeks (no full download).
    # Proxied: one local 720p download, prefetched during scene detection.
    # Disable to fall back to single-pass low-res frames.
    SCENE_HIRES_ENABLED: bool = True
    SCENE_HIRES_CONCURRENCY: int = 4  # parallel ffmpeg seeks (CDN or local file)
    SCENE_HIRES_TIMEOUT: float = 90.0  # stream-URL budget; on expiry keep low-res
    # Budget for the local-file seeks (after the one yt-dlp 720p download):
    # the proxyless fallback when the CDN 403s every direct stream-URL
    # extraction, and the only path when a proxy is set.
    SCENE_HIRES_FALLBACK_TIMEOUT: float = 180.0
    # yt-dlp player clients for VIDEO/AUDIO downloads (comma-separated).
    # 2026-08-19: YouTube 403s the web client's download URLs from this
    # environment while the android client works. android ONLY —
    #   * NEVER add "default": merging the web client's format list makes
    #     `bestvideo` selectors pick a web DASH format whose URL 403s.
    #   * NEVER add "android_vr": its formats need a GVS PO Token — actual
    #     downloads 403 (verified 2026-09-03; yt-dlp>=2026.8 skips them with
    #     a PO-token warning, older versions select them and then fail).
    # 2026-09-03: YouTube's SABR experiment can strip URLs from android DASH
    # formats (incl. ALL audio-only ones), so `bestaudio` may match nothing —
    # audio downloaders therefore use `bestaudio/best` and demux the muxed
    # progressive stream. When YouTube shifts again: first knob is this env
    # var, second is a yt-dlp lock bump + rebuild. Empty string = yt-dlp
    # defaults. Metadata/subtitle extraction deliberately does NOT use this
    # (android clients can lack subtitle/chapter data).
    YTDLP_PLAYER_CLIENTS: str = "android"
    # Player clients for the hi-res 720p download ONLY (hires prefetch, the
    # refiner's local fallback, moment fill). android tops out at format 18
    # (640x360), so with it the "720p" file was the same 360p file as pass 1.
    # Measured 2026-10-06 with real downloads, proxied and direct: web_embedded
    # and tv_embedded get 720p; ios/tv/web/web_safari/android_vr/web_creator
    # fail; android/tv_simply/mweb cap at 360p. "web_embedded,android" =
    # 720p, with android's formats in the list for non-embeddable videos.
    # If the download still fails, it retries once with YTDLP_PLAYER_CLIENTS.
    YTDLP_HIRES_PLAYER_CLIENTS: str = "web_embedded,android"
    # Versioned S3 prefix — bumping it defeats the frames-already-exist cache
    # so quality changes take effect for reprocessed videos ("scenes" = pre-hires).
    # v3: subject-aware scoring (skin/center-detail) + adaptive vision tiers.
    SCENE_S3_PREFIX: str = "scenes-v3"

    # Vision LLM analysis on top-scored frames.
    # Sending 8 base64 frames to Sonnet legitimately takes 30-50s under load;
    # HIGH-tier batches (~40 low-res frames) need the larger 90s budget.
    FRAME_VISION_ENABLED: bool = True
    FRAME_VISION_MAX_FRAMES: int = 8
    FRAME_VISION_TIMEOUT: float = 90.0

    # Adaptive frame-effort tiers (media/visual_tier.py, config-driven from
    # domains.json visualCriticality). HIGH tier over-selects candidates and
    # vision-describes them all BEFORE hires refinement so subject-matter
    # frames (cards, dishes, places) win over presenter shots.
    FRAME_TIER_ENABLED: bool = True
    FRAME_OVERSELECT_COUNT: int = 40
    # Reserved: refine the metadata-derived tier with an early classifier call
    # launched at frames-phase start (classification normally runs later).
    FRAME_TIER_EARLY_CLASSIFIER: bool = False
    # HIGH-tier floor: vision-informed reselection never keeps fewer than this
    # many frames (backfilled by local score when vision over-refuses).
    FRAME_RESELECT_FLOOR: int = 20

    # Frame extraction (visual blocks)
    # Default False for local dev (yt-dlp/ffmpeg may not be installed).
    # docker-compose.yml sets FRAME_EXTRACTION_ENABLED=true for container environments.
    FRAME_EXTRACTION_ENABLED: bool = False
    MAX_FRAMES_PER_VISUAL: int = 6  # Cap frames[] array length per visual block
    MAX_FRAMES_PER_CHAPTER: int = 12  # Total frames across all visual blocks in one chapter
    FRAME_MIN_SPACING_SECONDS: int = 20  # Min gap between frames in same block
    FRAME_WITHIN_BLOCK_DEDUP_THRESHOLD: int = (
        12  # aHash hamming distance (relaxed for within-block)
    )

    # Pipeline output-schema version — single-sourced from
    # packages/shared/src/config/pipeline-version.json (shared with the api
    # gateway; see docs/IDEMPOTENCY.md). Baked into the REDIS response-cache
    # key (bump = cached VIEResponses unreachable, they TTL out) AND stamped
    # as `pipelineVersion` on every persisted Mongo summary doc so the api's
    # serve path regens stale docs — no more out-of-band DB flush. Bump the
    # JSON on any schemas/*.txt or assembler props change; never set via env.
    PIPELINE_VERSION: str = get_pipeline_version()

    # ─── RabbitMQ worker ────────────────────────────────────────────────
    # AMQP URL — kept aligned with the API's RABBITMQ_URL in docker-compose.
    RABBITMQ_URL: str = "amqp://vie:vie-dev@vie-rabbitmq:5672/"
    # Concurrent jobs processed in one worker process. Cap matches the LLM
    # provider's parallelism — 2 keeps Anthropic 529 (overloaded) rare.
    WORKER_CONCURRENCY: int = 2
    # Re-publish a failed message up to this many times before sending to DLQ.
    # Tracked via the x-attempt header so retries survive restarts.
    WORKER_MAX_RETRIES: int = 3
    # Initial backoff for retry republish (seconds). Doubles each attempt.
    WORKER_RETRY_BACKOFF_SECONDS: float = 5.0
    # Prefetch — RabbitMQ pushes at most this many unacked messages to one
    # consumer. Setting it ~= 2x concurrency keeps the pipeline full without
    # holding messages a second consumer could be processing.
    WORKER_PREFETCH: int = 4

    @property
    def llm_model(self) -> str:
        """Get the configured LLM model with provider prefix."""
        if self.LLM_MODEL:
            return self.LLM_MODEL
        return get_model(self.LLM_PROVIDER, "default")

    @property
    def llm_fast_model(self) -> str:
        """Get the configured fast LLM model with provider prefix."""
        if self.LLM_FAST_MODEL:
            return self.LLM_FAST_MODEL
        provider = self.LLM_FAST_PROVIDER or self.LLM_PROVIDER
        return get_model(provider, "fast")

    @property
    def llm_fallback_models(self) -> list[str] | None:
        """Get fallback model chain if configured."""
        if self.LLM_FALLBACK_PROVIDER:
            return [get_model(self.LLM_FALLBACK_PROVIDER, "default")]
        return None

    # Map stage_name (as used in call_llm_with_retry) → settings attribute.
    # Used by stage call sites to look up their per-stage override, and by
    # the vision / description stages (which bypass the retry wrapper) to
    # read their own overrides. ClassVar marks this as a class-level
    # constant — without it, Pydantic v2 might try to interpret it as a
    # configurable field, which would silently break depending on version.
    _STAGE_TO_SETTING: ClassVar[dict[str, str]] = {
        "classifier": "LLM_CLASSIFIER_MODEL",
        "chapter_detect": "LLM_CHAPTER_DETECT_MODEL",
        "description_analysis": "LLM_DESCRIPTION_MODEL",
        "synthesis": "LLM_SYNTHESIS_MODEL",
        "enrichment": "LLM_ENRICHMENT_MODEL",
        "extraction": "LLM_EXTRACTION_MODEL",
        "translation": "LLM_TRANSLATION_MODEL",
        "translation_tabs": "LLM_TRANSLATION_MODEL",
        "translation_meta": "LLM_TRANSLATION_MODEL",
        "translation_labels": "LLM_TRANSLATION_MODEL",
        "vision": "LLM_VISION_MODEL",
    }

    def get_stage_model(self, stage_name: str) -> str | None:
        """Return the per-stage model override or None to use fast/primary."""
        attr = self._STAGE_TO_SETTING.get(stage_name)
        return getattr(self, attr, None) if attr else None

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()


_PROD_ENVS = frozenset({"production", "prod", "staging", "stg"})
_DEV_ENVS = frozenset({"", "development", "dev", "test", "local"})


@cache
def _warn_disk_prompts_ignored(env_name: str) -> None:
    logging.getLogger(__name__).warning(
        "PROMPT_SOURCE=disk ignored in ENVIRONMENT=%s — prompts load from the registry", env_name
    )


def prompts_from_disk() -> bool:
    """True when prompt templates must skip the Langfuse registry.

    A stray ``PROMPT_SOURCE=disk`` on prod would silently pin the pipeline
    to whatever .txt files shipped in the image, so prod/staging ignore it.
    """
    if settings.PROMPT_SOURCE != "disk":
        return False
    env_name = settings.ENVIRONMENT.lower()
    if env_name in _PROD_ENVS:
        _warn_disk_prompts_ignored(env_name)
        return False
    return True


def validate_secrets() -> None:
    """Fail-fast if INTERNAL_SECRET is using the default value in production/staging.

    Called from application lifespan (not import time) to avoid breaking test imports.
    """
    env_name = settings.ENVIRONMENT.lower()
    if settings.INTERNAL_SECRET != _DEFAULT_INTERNAL_SECRET:
        return

    if env_name in _PROD_ENVS:
        raise ValueError(
            f"INTERNAL_SECRET must be set via environment variable in {env_name}! "
            "Using the default value is a security risk."
        )
    if env_name not in _DEV_ENVS:
        logging.getLogger(__name__).warning(
            "INTERNAL_SECRET is using the default value (ENVIRONMENT=%s) "
            "— set it via environment variable in production!",
            env_name,
        )
