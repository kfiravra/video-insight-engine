"""Tests for ``llm_common.observability.redaction`` (moved from the summarizer's client tests).

Covers PII and secret-token redaction, payload truncation without a sizing
oracle, and structured (Anthropic block) content: text redacted, images kept.
"""

from __future__ import annotations

from llm_common.observability.redaction import (
    normalize_input,
    redact_block,
    redact_pii,
    truncate_payload,
)

# ─── PII ────────────────────────────────────────────────────────────────


def test_redact_pii_strips_emails():
    text = "Contact me at alice@example.com or bob+spam@test.io please"
    out = redact_pii(text)
    assert "alice@example.com" not in out
    assert "bob+spam@test.io" not in out
    assert "[REDACTED]" in out


def test_redact_pii_strips_phone_like_runs():
    out = redact_pii("Call +1 (555) 123-4567 anytime")
    assert "555" not in out
    assert "[REDACTED]" in out


def test_redact_pii_handles_none():
    assert redact_pii(None) == ""


# ─── Secret tokens ──────────────────────────────────────────────────────


def test_redact_pii_strips_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    assert jwt not in redact_pii(f"token={jwt} more text")


def test_redact_pii_strips_aws_keys():
    assert "AKIAIOSFODNN7EXAMPLE" not in redact_pii("key AKIAIOSFODNN7EXAMPLE seen")


def test_redact_pii_strips_anthropic_key():
    out = redact_pii("sk-ant-api03-xxxxxxxxxxxxxxxxxxxxabcdef in the logs")
    assert "sk-ant-api03" not in out
    # The "sk-ant-" prefix is redacted as one unit so the generic sk- pattern
    # cannot re-leak it.
    assert "sk-ant" not in out


def test_redact_pii_strips_openai_key():
    assert "sk-proj-" not in redact_pii("OPENAI=sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaa visible")


def test_redact_pii_strips_github_token():
    token = "ghp_abcdefghijklmnopqrstuvwxyzABCD1234"
    assert token not in redact_pii(f"GH {token} leaked")


def test_redact_pii_strips_bearer_header():
    assert "my.long.token.abc" not in redact_pii("Authorization: Bearer my.long.token.abc")


def test_redact_pii_strips_google_api_key():
    key = "AIzaSyA-1234567890abcdefghijklmnopqrstuvw"
    assert key not in redact_pii(f"GMAPS_KEY={key} extra")


def test_redact_pii_strips_slack_token():
    assert "xoxb-1234567890abcdefghij" not in redact_pii("SLACK=xoxb-1234567890abcdefghij more")


def test_redact_pii_strips_stripe_live_key():
    key = "sk_live_abcdef1234567890ABCDEF"
    assert key not in redact_pii(f"STRIPE={key} and more")


def test_redact_pii_strips_pem_private_key():
    pem = (
        "before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpQIBAAKCAQEAxYz1Z\n"
        "secret_body_here\n"
        "-----END RSA PRIVATE KEY-----\n"
        "after"
    )
    out = redact_pii(pem)
    assert "MIIEpQIBAAKCAQEAxYz1Z" not in out
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert "before" in out and "after" in out


# ─── Truncation ─────────────────────────────────────────────────────────


def test_truncate_payload_passthrough_when_small():
    assert truncate_payload("hello", max_bytes=100) == "hello"


def test_truncate_payload_caps_large_strings():
    out = truncate_payload("x" * 1000, max_bytes=200)
    assert out.startswith("x" * 200)
    assert out.endswith("[TRUNCATED]")
    # The original length must NOT be encoded: that is a sizing oracle.
    assert "1000" not in out


def test_truncate_payload_handles_none():
    assert truncate_payload(None) == ""


# ─── Structured content ─────────────────────────────────────────────────


def test_structured_content_blocks_are_redacted_and_images_kept():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Slide says alice@example.com"},
                {"type": "image", "source": {"type": "base64", "data": "ignored"}},
            ],
        },
    ]
    sent = normalize_input(messages)
    assert "alice@example.com" not in sent[0]["content"][0]["text"]
    assert "[REDACTED]" in sent[0]["content"][0]["text"]
    assert sent[0]["content"][1] == messages[0]["content"][1]
    # The caller's list is never mutated.
    assert messages[0]["content"][0]["text"] == "Slide says alice@example.com"


def test_redact_block_stops_at_the_depth_cap():
    deep: object = "leaf"
    for _ in range(20):
        deep = [deep]
    flat = redact_block(deep)
    for _ in range(16):
        assert isinstance(flat, list)
        flat = flat[0]
    assert flat == "[REDACTED:depth]"


def test_normalize_input_redacts_plain_strings_and_keeps_other_shapes():
    assert normalize_input("mail a@b.co") == "mail [REDACTED]"
    assert normalize_input({"audioSeconds": 3.0}) == {"audioSeconds": 3.0}
