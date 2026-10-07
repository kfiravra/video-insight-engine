"""Quality scorers for fast-tier model benchmark.

Pure functions, no LLM calls. Each scorer takes the Sonnet baseline output
plus a candidate output for the same input, returns a float in [0, 1].

Importers must already have the summarizer source on sys.path (the main
benchmark script handles that before importing this module).
"""

from __future__ import annotations

import re
from typing import Any

# ─── chapter_detect ─────────────────────────────────────────────────────────


def _word_overlap(a: str, b: str) -> float:
    """Symmetric Jaccard on lowercased word sets, ignoring stopwords."""
    stop = {"the", "a", "an", "and", "or", "of", "in", "to", "for", "with"}
    aw = {w for w in re.findall(r"\w+", (a or "").lower()) if w not in stop}
    bw = {w for w in re.findall(r"\w+", (b or "").lower()) if w not in stop}
    if not aw and not bw:
        return 1.0
    union = aw | bw
    return len(aw & bw) / len(union) if union else 0.0


def score_chapter_detect(baseline: list, candidate: list) -> float:
    """Score chapter detection output.

    50% chapter-count agreement, 50% mean title-word-overlap on paired indices.
    Both args are lists of ChapterChunk-like objects (any duck-type with
    ``title``).
    """
    if not baseline and not candidate:
        return 1.0
    if not baseline or not candidate:
        return 0.0

    base_n = len(baseline)
    cand_n = len(candidate)
    count_score = max(0.0, 1.0 - abs(base_n - cand_n) / max(base_n, 1))

    pairs = min(base_n, cand_n)
    if pairs == 0:
        title_score = 0.0
    else:
        sims = [_word_overlap(baseline[i].title, candidate[i].title) for i in range(pairs)]
        title_score = sum(sims) / pairs

    return round(0.5 * count_score + 0.5 * title_score, 4)


# ─── description analysis ──────────────────────────────────────────────────


def _ratio(c: int, b: int) -> float:
    """Bounded ratio: 1.0 when baseline empty OR candidate ≥ baseline.

    Used by the description and enrichment scorers to compare item counts.
    Extras on the candidate side never penalize — we only flag undercounts.
    """
    if b == 0:
        return 1.0
    return min(1.0, c / b)


def score_description(baseline: Any, candidate: Any) -> float:
    """Score description analyzer output (DescriptionAnalysis dataclass)."""
    if baseline is None or candidate is None:
        return 0.0

    fields = ["links", "resources", "related_videos", "social_links"]
    ratios = [
        _ratio(len(getattr(candidate, f, [])), len(getattr(baseline, f, [])))
        for f in fields
    ]
    return round(sum(ratios) / len(ratios), 4)


# ─── synthesis ──────────────────────────────────────────────────────────────


def score_synthesis(baseline: Any, candidate: Any) -> float:
    """Score synthesis output (SynthesisResult Pydantic model).

    Penalizes missing/empty fields and length anomalies (>3× baseline or
    <0.25× baseline on master_summary).
    """
    if baseline is None or candidate is None:
        return 0.0

    score = 0.0
    if candidate.tldr and len(candidate.tldr.strip()) >= 20:
        score += 0.25
    if candidate.key_takeaways and len(candidate.key_takeaways) >= max(2, len(baseline.key_takeaways) // 2):
        score += 0.25
    if candidate.master_summary:
        base_len = len(baseline.master_summary or "")
        cand_len = len(candidate.master_summary)
        if base_len == 0 or 0.25 <= cand_len / max(base_len, 1) <= 3.0:
            score += 0.25
    if candidate.seo_description and len(candidate.seo_description.strip()) >= 30:
        score += 0.25

    return round(score, 4)


# ─── enrichment ─────────────────────────────────────────────────────────────


def score_enrichment(baseline: Any, candidate: Any) -> float:
    """Score enrichment output (EnrichmentData).

    Equally weight quiz/flashcards/scenarios/cheat_sheet count ratios.
    Each is scored 0.0 if absent on candidate but present on baseline.
    """
    if baseline is None or candidate is None:
        return 0.0

    fields = [("quiz", 0.35), ("flashcards", 0.35), ("scenarios", 0.15), ("cheat_sheet", 0.15)]
    total = 0.0
    weight_sum = 0.0
    for field, weight in fields:
        b = getattr(baseline, field, None)
        c = getattr(candidate, field, None)
        if b is None and c is None:
            continue  # neither domain supports this — exclude from weighting
        weight_sum += weight
        if not b:
            total += weight  # baseline absent, candidate-anything is fine
            continue
        if not c:
            continue  # baseline had it, candidate empty → 0
        total += weight * _ratio(len(c), len(b))

    if weight_sum == 0:
        return 1.0
    return round(total / weight_sum, 4)


# ─── translation ────────────────────────────────────────────────────────────


def score_translation(baseline: Any, candidate: Any, expected_lang: str = "he") -> float:
    """Score translation output.

    Both args are translated dict outputs. Validates: (1) candidate is a
    dict with the same top-level keys as baseline; (2) at least one
    translated string contains characters in the target script.
    ``expected_lang``: "he" (Hebrew), "es" (Spanish), "fr" (French), etc.
    """
    if not isinstance(candidate, dict) or not isinstance(baseline, dict):
        return 0.0

    base_keys = set(baseline.keys())
    cand_keys = set(candidate.keys())
    if not base_keys:
        return 1.0
    key_score = len(base_keys & cand_keys) / len(base_keys)

    # Language presence: any value contains script-specific chars.
    flat_text = " ".join(str(v) for v in candidate.values() if isinstance(v, str))
    lang_score = 1.0 if _matches_script(flat_text, expected_lang) else 0.0

    return round(0.5 * key_score + 0.5 * lang_score, 4)


def _matches_script(text: str, lang: str) -> bool:
    """Cheap script detector. Avoids the langdetect dependency."""
    patterns = {
        "he": r"[֐-׿]",  # Hebrew block
        "es": r"[ñáéíóúü¿¡]",       # Spanish-specific letters
        "fr": r"[àâäéèêëîïôöùûüÿç]",
        "ar": r"[؀-ۿ]",
        "ja": r"[぀-ヿ一-鿿]",
        "zh": r"[一-鿿]",
        "ru": r"[Ѐ-ӿ]",
    }
    pat = patterns.get(lang)
    if not pat:
        return bool(text.strip())
    return bool(re.search(pat, text))


# ─── frame vision ───────────────────────────────────────────────────────────


def score_vision(baseline: list[dict], candidate: list[dict]) -> dict[str, Any]:
    """Score vision output against baseline (list of frame dicts).

    Returns granular metrics (scene match %, text-visible similarity).
    The aggregate ``score`` is the scene-type match rate — matches what
    ``scripts/spotcheck_frame_vision.py`` already optimizes for.
    """
    if not baseline or not candidate:
        return {"score": 0.0, "scene_matches": 0, "text_matches": 0, "total": len(baseline)}

    by_idx_base: dict[int, dict] = {}
    for f in baseline:
        idx = f.get("frame_index")
        if isinstance(idx, int):
            by_idx_base[idx] = f
    by_idx_cand: dict[int, dict] = {}
    for f in candidate:
        idx = f.get("frame_index")
        if isinstance(idx, int):
            by_idx_cand[idx] = f
    indices = sorted(set(by_idx_base) | set(by_idx_cand))

    scene_matches = 0
    text_matches = 0
    for idx in indices:
        b = by_idx_base.get(idx, {})
        c = by_idx_cand.get(idx, {})
        if b.get("scene_type") == c.get("scene_type"):
            scene_matches += 1
        bt = (b.get("text_visible") or "").strip().lower()
        ct = (c.get("text_visible") or "").strip().lower()
        if bt == ct or _string_similarity(bt, ct) >= 0.8:
            text_matches += 1

    n = len(indices) or 1
    return {
        "score": round(scene_matches / n, 4),
        "scene_matches": scene_matches,
        "text_matches": text_matches,
        "total": n,
    }


def _string_similarity(a: str, b: str) -> float:
    """Length-normalized edit-distance proxy via word-set overlap.

    Good enough for OCR strings where word order matters less than
    presence of the same tokens.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    aw = set(a.split())
    bw = set(b.split())
    union = aw | bw
    return len(aw & bw) / len(union) if union else 0.0
