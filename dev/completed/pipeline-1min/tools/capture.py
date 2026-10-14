#!/usr/bin/env python3
"""Record the byte-exact LLM request baseline of the replay cassettes.

Runs the real ``run_replay`` driver (speed 0, zero network, zero spend) on
each cassette and wraps ``ReplayLLM.acompletion`` — the single seam every
LiteLLM request passes through — so EVERY request is captured, before the
fake answers it, as ``canonical_request(kwargs)``.

Outputs, per video, in ``--out``:
  <video><suffix>.jsonl   one line per request, in call order:
                          {"chars","feature","ordinal","request","seq","sha256","span"}
  <video><suffix>.sha256  seq, feature, span, ordinal, sha256, chars (tab-separated)

``ordinal`` is the cassette key ordinal (per feature+span, call order), so
(feature, span, ordinal) is exactly the ``LLMKey`` the fake served.

Run with the summarizer venv (pass an ABSOLUTE ``--out``: the script chdirs into
services/summarizer before parsing it):
    services/summarizer/.venv/bin/python dev/completed/pipeline-1min/tools/capture.py \
        --out <abs dir> [--parallel-false] [--videos <id> ...] [--speed <x>]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SUMMARIZER_DIR = _REPO_ROOT / "services" / "summarizer"
sys.path.insert(0, str(_SUMMARIZER_DIR))
# Same cwd as the test suite / scripts/replay.py: no repo-root .env is loaded.
os.chdir(_SUMMARIZER_DIR)
# LiteLLM would otherwise fetch its model-cost map over the network at import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.replay import fake_llm  # noqa: E402
from tests.replay.cassette import load_cassette  # noqa: E402
from tests.replay.driver import run_replay  # noqa: E402
from tests.replay.report import divergences  # noqa: E402

from llm_common.context import llm_feature_var  # noqa: E402
from src.config import settings  # noqa: E402

VIDEOS = ("T1dQhQAm8Tc", "jMq8lEu-of0", "uC45_4nnEAI")


# ─── Canonicalisation (copy verbatim into a repo test) ───


def canonical_request(kwargs: dict[str, Any]) -> str:
    """Byte-exact form of one ``acompletion(**kwargs)`` request.

    Every kwarg the provider sends is kept (model, messages after
    ``prepare_for_model`` incl. content-block lists and ``cache_control``,
    max_tokens, temperature, response_format, timeout, num_retries, and
    metadata when a caller sets it): none varied across process restarts,
    hash seeds, in-process repeats or replay speed, so nothing is dropped.
    Non-JSON values raise instead of being stringified.
    """
    return json.dumps(kwargs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ─── Capture ───


@contextmanager
def recording_fake(captured: list[dict[str, Any]]) -> Iterator[None]:
    """Wrap the replay fake so every request is canonicalised before it is answered."""
    original = fake_llm.ReplayLLM.acompletion
    ordinals: dict[tuple[str, str | None], int] = {}

    async def recording(self: fake_llm.ReplayLLM, **kwargs: Any) -> Any:
        feature = llm_feature_var.get()
        span = fake_llm._span_var.get()
        ordinal = ordinals.get((feature, span), 0)
        ordinals[(feature, span)] = ordinal + 1
        canonical = canonical_request(kwargs)
        captured.append(
            {
                "seq": len(captured),
                "feature": feature,
                "span": span,
                "ordinal": ordinal,
                "chars": len(canonical),
                "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "canonical": canonical,
            }
        )
        return await original(self, **kwargs)

    with patch.object(fake_llm.ReplayLLM, "acompletion", recording):
        yield


def _write(out: Path, video_id: str, suffix: str, captured: list[dict[str, Any]]) -> None:
    lines, sums = [], []
    for row in captured:
        meta = {k: v for k, v in row.items() if k != "canonical"}
        head = json.dumps(meta, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        # Spliced, not re-dumped, so the "request" value is canonical_request() byte for byte.
        lines.append(head[:-1] + ',"request":' + row["canonical"] + "}")
        sums.append(
            f"{row['seq']}\t{row['feature']}\t{row['span']}\t{row['ordinal']}"
            f"\t{row['sha256']}\t{row['chars']}"
        )
    (out / f"{video_id}{suffix}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / f"{video_id}{suffix}.sha256").write_text("\n".join(sums) + "\n", encoding="utf-8")


async def _capture_one(video_id: str, speed: float) -> tuple[list[dict[str, Any]], list[str]]:
    captured: list[dict[str, Any]] = []
    with recording_fake(captured):
        result = await run_replay(load_cassette(video_id), speed=speed)
    return captured, divergences(result)


def _settings_note(parallel_false: bool) -> ExitStack:
    stack = ExitStack()
    if parallel_false:
        stack.enter_context(patch.object(settings, "EXTRACTION_PARALLEL", False))
    return stack


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--parallel-false", action="store_true")
    parser.add_argument("--videos", nargs="*", default=list(VIDEOS))
    parser.add_argument(
        "--speed", type=float, default=0.0, help="0 = baseline; >0 only to probe timing effects"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    args.out.mkdir(parents=True, exist_ok=True)
    suffix = ".parallel-false" if args.parallel_false else ""
    failed = False
    with _settings_note(args.parallel_false):
        print(
            f"EXTRACTION_PARALLEL={settings.EXTRACTION_PARALLEL} "
            f"PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '<random>')}"
        )
        for video_id in args.videos:
            captured, problems = asyncio.run(_capture_one(video_id, args.speed))
            _write(args.out, video_id, suffix, captured)
            print(f"{video_id}: {len(captured)} requests, divergences={problems}")
            failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
