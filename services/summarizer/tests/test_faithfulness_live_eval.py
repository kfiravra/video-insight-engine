"""Judge-accuracy scaffold over the labeled faithfulness fixture.

The fixture (``tests/fixtures/faithfulness_labeled_claims.json``) holds a
clearly-synthetic transcript plus ~20 claims labeled supported/hallucinated.

Two layers:

- **Always-on fixture validation** — cheap structural checks so the labeled
  dataset can't silently rot (runs in the normal suite, no LLM calls).
- **Live accuracy eval** — sends every labeled claim through the real judge
  (``_judge_one`` + ``_select_claim_context``) and asserts aggregate
  accuracy. Spends real LLM tokens, so it is skip-gated behind
  ``LIVE_EVAL=1``:

      LIVE_EVAL=1 .venv/bin/python -m pytest -q tests/test_faithfulness_live_eval.py -s
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.services.pipeline import faithfulness as fh

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "faithfulness_labeled_claims.json"

_VALID_LABELS = {"supported", "hallucinated"}

# Minimum acceptable judge accuracy over the labeled set. The dataset is
# deliberately easy (explicit statements vs. flat contradictions/absences),
# so a healthy judge should clear this comfortably; a drop below it signals
# prompt or model drift.
_MIN_ACCURACY = 0.8


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text())


# ─── Always-on fixture validation ───────────────────────────────────────
class TestLabeledClaimsFixture:
    def test_fixture_has_transcript_and_enough_claims(self):
        data = _load_fixture()
        assert len(data["transcript"]) > 1_000
        assert len(data["claims"]) >= 20

    def test_every_claim_is_well_formed(self):
        data = _load_fixture()
        seen_ids: set[str] = set()
        for entry in data["claims"]:
            assert entry["claim"].strip(), f"empty claim: {entry}"
            assert entry["label"] in _VALID_LABELS, f"bad label: {entry}"
            assert entry["id"] not in seen_ids, f"duplicate id: {entry['id']}"
            seen_ids.add(entry["id"])

    def test_both_labels_are_represented(self):
        data = _load_fixture()
        labels = {entry["label"] for entry in data["claims"]}
        assert labels == _VALID_LABELS

    def test_supported_claims_localize_within_fixture_transcript(self):
        """Sanity: context selection finds lexical signal for supported claims."""
        data = _load_fixture()
        transcript = data["transcript"]
        for entry in data["claims"]:
            if entry["label"] != "supported":
                continue
            context = fh._select_claim_context(transcript, entry["claim"])
            assert context, f"no context selected for {entry['id']}"


# ─── Live accuracy eval (deferred spend — LIVE_EVAL=1) ──────────────────
@pytest.mark.skipif(
    os.environ.get("LIVE_EVAL") != "1",
    reason="live judge eval spends LLM tokens; run with LIVE_EVAL=1",
)
class TestLiveJudgeAccuracy:
    @pytest.mark.asyncio
    async def test_judge_accuracy_over_labeled_claims(self, monkeypatch):
        from src.config import Settings
        from src.services.llm import LLMService
        from src.services.llm_provider import LLMProvider

        # conftest blanks every stage model; the eval measures the judge as configured.
        monkeypatch.setattr(fh.settings, "LLM_JUDGE_MODEL", Settings().LLM_JUDGE_MODEL)
        data = _load_fixture()
        transcript = data["transcript"]
        claims = data["claims"]
        service = LLMService(LLMProvider())

        contexts = [fh._select_claim_context(transcript, c["claim"]) for c in claims]
        verdicts = await asyncio.gather(
            *(fh._judge_one(service, ctx, c["claim"]) for ctx, c in zip(contexts, claims)),
            return_exceptions=True,
        )

        correct = 0
        judged = 0
        mismatches: list[str] = []
        for entry, verdict in zip(claims, verdicts):
            if not isinstance(verdict, bool):
                mismatches.append(f"{entry['id']}: judge error ({verdict!r})")
                continue
            judged += 1
            expected = entry["label"] == "supported"
            if verdict == expected:
                correct += 1
            else:
                mismatches.append(
                    f"{entry['id']}: expected {entry['label']}, judge said grounded={verdict}"
                )

        assert judged >= len(claims) * 0.8, f"too many judge errors: {mismatches}"
        accuracy = correct / judged
        print(f"\n[live-eval] judge accuracy: {correct}/{judged} = {accuracy:.2%}")
        if mismatches:
            print("[live-eval] mismatches:\n  " + "\n  ".join(mismatches))
        assert accuracy >= _MIN_ACCURACY, (
            f"judge accuracy {accuracy:.2%} below {_MIN_ACCURACY:.0%}: {mismatches}"
        )
