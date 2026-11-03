"""Redaction + payload-size helpers for the Langfuse observability layer.

Shared by every service that ships LLM payloads to Langfuse (summarizer,
assistant). The patterns and helpers here have no dependency on the
Langfuse SDK — they're pure string transforms — so they can be reused for
any other "scrub before logging" need.

Design contract: helpers must NEVER raise and must NEVER mutate input.
``redact_pii`` returns the redacted string; ``_sanitize_messages`` returns
a new list with new dicts and never edits caller-owned objects.
"""

from __future__ import annotations

import re
from typing import Any

# ─── PII / secret redaction ─────────────────────────────────────────────
# Two layers: obvious PII (email, phone-shaped digit runs) plus narrow
# secret-token formats. The token patterns target high-value leaks that
# would be catastrophic if surfaced to a third-party SaaS:
#   - JWT (eyJ... header)
#   - AWS access key ID (AKIA / ASIA / AGPA / AROA / AIDA)
#   - OpenAI keys (sk-... at least 20 chars)
#   - Anthropic keys (sk-ant-...)
#   - GitHub PAT / OAuth (ghp_/ghu_/ghs_/gho_/ghr_)
#   - Google API keys (AIza...)
#   - Slack tokens (xoxb-/xoxp-/...)
#   - Stripe live keys (sk_live_/rk_live_/pk_live_)
#   - PEM private-key blocks
#   - Generic "Bearer <token>" headers in transcripts
# Over-redaction would break debugging; under-redaction would leak. We
# accept some false negatives (no Luhn/CC check, no name detection) and
# document the model in docs/OBSERVABILITY.md.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{8,}\d")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA|AGPA|AROA|AIDA)[A-Z0-9]{16}\b")
_ANTHROPIC_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
_OPENAI_KEY_RE = re.compile(r"sk-(?!ant-)[A-Za-z0-9_\-]{20,}")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")
_STRIPE_LIVE_RE = re.compile(r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}\b")
# Catch the whole PEM block, header through footer. Process first so the
# line-level patterns above don't fragment the redaction.
_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----",
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}")
_REDACTED = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    _PEM_BLOCK_RE,  # block-level — first so line patterns don't fragment it
    _ANTHROPIC_KEY_RE,  # ordered before _OPENAI_KEY_RE so sk-ant- isn't double-matched
    _OPENAI_KEY_RE,
    _STRIPE_LIVE_RE,
    _AWS_KEY_RE,
    _GITHUB_TOKEN_RE,
    _GOOGLE_API_KEY_RE,
    _SLACK_TOKEN_RE,
    _JWT_RE,
    _BEARER_RE,
)

MAX_PAYLOAD_BYTES = 50_000
_TRUNCATED_SUFFIX = "\n\n[TRUNCATED]"

# Recursion cap on structured content. Anthropic blocks in practice are
# at most 2-3 levels deep; the guard exists to bound worst-case CPU on
# pathological inputs (e.g., a deeply-nested JSON dump in a payload).
_REDACT_MAX_DEPTH = 16


def redact_pii(text: str | None) -> str:
    """Strip emails, phone-shaped runs, and known secret-token formats.

    Safe on ``None`` — returns ``""``. The order matters: Anthropic ``sk-ant-``
    keys are redacted before generic ``sk-`` so the prefix isn't lost.
    """
    if not text:
        return ""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _PHONE_RE.sub(_REDACTED, text)
    return text


def truncate_payload(text: str | None, max_bytes: int = MAX_PAYLOAD_BYTES) -> str:
    """Cap payload size so Langfuse uploads stay cheap.

    Safe on ``None`` — returns ``""``. The suffix is fixed-string only;
    encoding the original length would leak a sizing oracle to any
    downstream viewer.
    """
    if not text:
        return ""
    if len(text) <= max_bytes:
        return text
    return text[:max_bytes] + _TRUNCATED_SUFFIX


def redact_block(block: Any, _depth: int = 0) -> Any:
    """Recursively redact text inside Anthropic-style structured content.

    Vision messages package OCR text and prompt fragments as a list of
    blocks like ``{"type": "text", "text": "..."}`` or
    ``{"type": "image", "source": {...}}``. We walk the structure and
    redact any ``text`` field we find — anything else (image bytes, tool
    calls) is left intact. Bounded by ``_REDACT_MAX_DEPTH`` so a
    pathologically nested payload can't burn CPU.
    """
    if _depth >= _REDACT_MAX_DEPTH:
        # Past the depth cap, surface a sentinel so reviewers can tell
        # at a glance why a sub-tree disappeared.
        return "[REDACTED:depth]"
    if isinstance(block, str):
        return truncate_payload(redact_pii(block))
    if isinstance(block, dict):
        out: dict[str, Any] = {}
        for key, value in block.items():
            if key == "text" and isinstance(value, str):
                out[key] = truncate_payload(redact_pii(value))
            elif isinstance(value, (dict, list)):
                out[key] = redact_block(value, _depth + 1)
            else:
                out[key] = value
        return out
    if isinstance(block, list):
        return [redact_block(item, _depth + 1) for item in block]
    return block


def sanitize_messages(messages: list[Any]) -> list[Any]:
    """Redact + truncate per-message content. Walks structured blocks too."""
    out: list[Any] = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            out.append({**msg, "content": truncate_payload(redact_pii(content))})
        elif isinstance(content, list):
            # Anthropic structured content — recurse into each block's
            # ``text`` field. Frame-vision OCR lives here and was previously
            # leaking unredacted.
            out.append({**msg, "content": redact_block(content)})
        else:
            out.append(msg)
    return out


def normalize_input(payload: Any) -> Any:
    """Normalize an LLM input payload (string or message list) for upload."""
    if isinstance(payload, str):
        return truncate_payload(redact_pii(payload))
    if isinstance(payload, list):
        return sanitize_messages(payload)
    return payload
