"""Translation step — English-canonical pipeline.

Generation always produces English, so the assembled output IS the English
primary. This step translates that English output INTO the detected source
language and nests the result under ``sourceLanguage`` so the FE toggle can
swap the whole surface. The English top-level is returned unchanged.

It walks the output tree recursively, collects every translatable prose string
into a flat list (filtered by a leaf-only deny-list of structural, asset, and
enum-shaped keys), translates them in batched Haiku calls (so a large video is
fully translated rather than capped), and applies the translations back into a
deep copy that becomes the ``sourceLanguage`` artifact ({code, name, isRTL,
tabs, meta}).

On any failure (LLM error, length mismatch, mirror detection): returns the
input unchanged (no ``sourceLanguage`` key) so the FE simply renders no toggle.

Mirror detection samples the 3 longest collected strings — small models
occasionally echo input strings back, and longest-string sampling avoids
false-positives on short tokens that legitimately match across languages
(proper nouns, ASCII identifiers). The call and the echo check live in
``translation_flat``, shared with the RAG transcript translation.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Any

from ...utils.language_utils import (
    get_native_name,
    is_rtl,
)
from .translation_flat import is_mirror, translate_flat_list

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

# Keys whose PRIMITIVE STRING values must NOT be translated.
#
# LEAF-ONLY semantics: the deny rule only applies when the value at the key
# is a primitive string. Lists and dicts are always recursed, so container
# keys with deny-listed names (e.g. a hypothetical ``warmup: [{...}]``) still
# descend into their contents. The rule only blocks translation when the
# named key directly identifies a leaf string.
_SKIP_KEYS: frozenset[str] = frozenset(
    {
        # Structural identifiers / routing
        "id",
        "component",
        "language",
        "targetTab",
        "type",
        "layout",
        "platform",
        # Asset references — URLs, S3 keys, raw code
        "url",
        "thumbnailUrl",
        "s3Key",
        "code",
        # Numeric routing keys — pure offsets/indices, never prose
        "timestamp",
        "time",
        "seconds",
        "startSeconds",
        "endSeconds",
        # Enums / non-prose primitives kept stable across languages
        "emoji",
        "correctIndex",
        "difficulty",
        "mood",
        # ``videoTitle`` is the raw YouTube metadata title — source-language even in
        # the English-canonical meta. The translation phase handles it explicitly in
        # both directions via ``_translate_title``, so the batch walker skips it;
        # otherwise a source-language string gets mislabeled as English, round-
        # tripped for nothing, and pollutes mirror detection.
        "videoTitle",
        # ``sets`` is always a number in the fitness schema. ``reps``/``rest``/
        # ``duration`` are deliberately ABSENT — the fitness schema emits prose
        # at these keys (e.g. ``"30 שניות"``, ``"AMRAP"``, ``"until failure"``),
        # so they must flow through translation or English exercise cards
        # render mixed-language strings.
        "sets",
        # Vision-LLM output kept verbatim — vision is prompted in English so its
        # caption/evidence strings are already English regardless of source
        # language. ``frameOcr`` is deliberately ABSENT: Tesseract output is the
        # original on-screen language (e.g. Hebrew screen text in a Hebrew video),
        # so it must flow through the translator like any other prose. ``sceneType``
        # is an enum-shape token ("talking_head", "screen_capture", …).
        "frameCaption",
        "frameSceneType",
        "frameEvidence",
    }
)

# Min collected-string length (after strip). Skips numerics, single tokens,
# 1-3 char strings that are usually labels in another vocabulary (status,
# enum-ish remnants, etc.).
_MIN_TRANSLATABLE_LEN = 3

# Runaway guard so a pathological payload (deeply nested or accidentally-large
# blob in props) can't blow the recursion stack. Set well above any real
# assembled-tab shape (a rich long video carries a few hundred strings); the
# LLM call is kept small by batching, not by capping collection, so nothing is
# silently dropped. 32 levels is far beyond any real shape (~6 levels typical).
_MAX_COLLECTED_STRINGS = 5000
_MAX_RECURSION_DEPTH = 32

# Strings per LLM translation call. Collected strings are split into batches of
# this size and translated concurrently, then concatenated — this removes the
# old fail-closed "too many strings" abort while keeping each call within the
# fast model's output budget.
_MAX_TRANSLATION_BATCH = 200

# Cap on how many translation batches run at once. A long video can produce
# enough strings to fan out into many batches; without a bound, ``gather``
# would launch them all and risk tripping the LLM provider's concurrency/rate
# limits — and since translation is all-or-nothing, one rate-limited batch
# discards the whole source-language artifact. 4 keeps throughput high while
# staying comfortably under provider limits.
_MAX_CONCURRENT_TRANSLATION_BATCHES = 4

# JSON path — alternating dict keys (str) and list indices (int).
JsonPath = tuple[str | int, ...]


# ─── Walker ──────────────────────────────────────────────────────────────────


def _collect_strings(
    obj: Any,
    path: JsonPath,
    pairs: list[tuple[JsonPath, str]],
    depth: int = 0,
) -> None:
    """Recursively append ``(path, value)`` for every translatable string.

    Container nodes (list/dict) are ALWAYS recursed. The deny-list applies
    ONLY when the value at a key is a primitive string — see ``_SKIP_KEYS``.
    Bounded by ``_MAX_RECURSION_DEPTH`` and ``_MAX_COLLECTED_STRINGS`` so a
    runaway payload can't waste a 90s LLM call or stack-overflow. Truncation
    is observed by ``translate_to_source`` after the walk completes, since
    logging inside the recursion would either fire many times per cap-hit or
    require a mutable flag plumbed through every frame.
    """
    if depth > _MAX_RECURSION_DEPTH or len(pairs) >= _MAX_COLLECTED_STRINGS:
        return
    if isinstance(obj, str):
        if len(obj.strip()) > _MIN_TRANSLATABLE_LEN:
            pairs.append((path, obj))
        return
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            _collect_strings(item, path + (i,), pairs, depth + 1)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            # Leaf-only: skip only when the deny-listed key holds a string.
            if key in _SKIP_KEYS and isinstance(value, str):
                continue
            _collect_strings(value, path + (key,), pairs, depth + 1)


def _set_at_path(root: Any, path: JsonPath, value: str) -> None:
    """Walk to ``path``'s parent in ``root`` and set the leaf to ``value``.

    Silent no-op on broken paths so an off-by-one in the translation list
    cannot corrupt unrelated subtrees.
    """
    if not path:
        return
    target = root
    try:
        for segment in path[:-1]:
            target = target[segment]
        target[path[-1]] = value
    except (KeyError, IndexError, TypeError):
        return


# ─── Orchestrator ────────────────────────────────────────────────────────────


async def _translate_or_salvage(
    llm_service: LLMService,
    batch: list[str],
    source_language: str,
    target_language: str,
) -> tuple[list[str], list[bool]]:
    """Translate ``batch``; on failure retry once at half size, then salvage.

    Returns ``(strings, salvaged_mask)`` — a SAME-LENGTH list where any string
    that could not be translated falls back to its English original (so the
    positional path mapping in ``translate_to_source`` stays valid) plus a
    per-string mask where ``True`` marks an English-fallback string (not really
    translated). The mask lets the caller exclude salvaged strings from the
    ``is_mirror`` echo check — they are English by construction and would
    otherwise inflate the mirror ratio. A count mismatch on a large batch
    ("expected 200, got 72") usually means the model truncated its output, so a
    half-size retry typically fits within the budget; only a half that still
    fails is salvaged as English.
    """
    result = await translate_flat_list(
        llm_service,
        batch,
        source_language,
        target_language,
    )
    if result is not None:
        return result, [False] * len(result)
    if len(batch) <= 1:
        return batch, [True] * len(batch)  # single string failed — keep English

    mid = len(batch) // 2
    halves_src = (batch[:mid], batch[mid:])
    halves_res = await asyncio.gather(
        *(
            translate_flat_list(llm_service, half, source_language, target_language)
            for half in halves_src
        )
    )
    out: list[str] = []
    salvaged_mask: list[bool] = []
    salvaged = 0
    for half_src, half_res in zip(halves_src, halves_res):
        if half_res is not None:
            out.extend(half_res)
            salvaged_mask.extend([False] * len(half_res))
        else:
            out.extend(half_src)
            salvaged_mask.extend([True] * len(half_src))
            salvaged += len(half_src)
    if salvaged:
        logger.warning(
            "Translation salvaged %d/%d strings as English after batch retry",
            salvaged,
            len(batch),
        )
    return out, salvaged_mask


async def _translate_in_batches(
    llm_service: LLMService,
    strings: list[str],
    source_language: str,
    target_language: str,
) -> tuple[list[str], list[bool]] | None:
    """Translate ``strings`` in concurrent batches and concatenate in order.

    Resilient to a single bad batch: a batch that fails (LLM error or a count
    mismatch) is retried once split in half, and any half that still fails falls
    back to its English source strings — a mostly-translated source-language
    surface is far better than none (the previous all-or-nothing behaviour
    discarded the entire ``sourceLanguage`` block on one truncated batch).

    Returns ``(strings, salvaged_mask)`` — the translated strings (English in
    salvaged positions) and a per-string mask where ``True`` marks a salvaged
    English-fallback string. Returns None ONLY when no batch produced a real
    translation (every string salvaged), so the caller's ``is_mirror`` gate /
    no-toggle path still apply. Concurrency is bounded by
    ``_MAX_CONCURRENT_TRANSLATION_BATCHES`` to stay under provider rate limits.
    """
    if not strings:
        return [], []
    batches = [
        strings[i : i + _MAX_TRANSLATION_BATCH]
        for i in range(0, len(strings), _MAX_TRANSLATION_BATCH)
    ]
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TRANSLATION_BATCHES)

    async def _bounded(batch: list[str]) -> tuple[list[str], list[bool]]:
        async with semaphore:
            return await _translate_or_salvage(
                llm_service,
                batch,
                source_language,
                target_language,
            )

    results = await asyncio.gather(*(_bounded(batch) for batch in batches))
    out: list[str] = []
    salvaged_mask: list[bool] = []
    for chunk, chunk_mask in results:
        out.extend(chunk)
        salvaged_mask.extend(chunk_mask)
    if all(salvaged_mask):
        return None  # nothing actually translated — let the caller skip the toggle
    if len(out) != len(strings):
        # Salvage preserves length, so this is a defensive invariant; if it ever
        # trips, fail closed rather than write a misaligned source-language tree.
        logger.error(
            "Batched translation length mismatch after salvage: expected %d, got %d",
            len(strings),
            len(out),
        )
        return None
    return out, salvaged_mask


async def translate_to_source(
    llm_service: LLMService,
    output: dict[str, Any],
    target_lang: str,
) -> dict[str, Any]:
    """Translate the English ``output`` INTO ``target_lang`` for the source view.

    Args:
        llm_service: LLM service for the Haiku calls.
        output: English-primary dict, shape ``{"tabs": [...], "meta": {...}}``.
        target_lang: ISO 639-1 code of the source language to translate into
            (e.g. ``"he"``).

    Returns:
        On success: a NEW dict whose top level is the English ``output``
        unchanged, plus the translated artifact nested under ``sourceLanguage``
        ({code, native ``name``, ``isRTL``, ``tabs``, ``meta``}). On any failure:
        the ``output`` arg unchanged (no ``sourceLanguage`` key). Callers detect
        success via ``"sourceLanguage" in result``.
    """
    pairs: list[tuple[JsonPath, str]] = []
    _collect_strings(output, (), pairs)
    if not pairs:
        # Pathological but possible: no translatable prose (very short clips,
        # all-emoji titles, or every populated key deny-listed). Log so it is
        # visible in observability rather than a silently toggle-less video.
        logger.info(
            "Translation no-op: no translatable prose collected (target=%s). "
            "Output unchanged; FE will render no language toggle.",
            target_lang,
        )
        return output

    originals = [s for _, s in pairs]
    logger.info(
        "Translating %d strings (%d chars) from English to %s",
        len(originals),
        sum(len(s) for s in originals),
        target_lang,
    )

    batched = await _translate_in_batches(llm_service, originals, "en", target_lang)
    if batched is None:
        logger.warning(
            "Translation failed — returning input unchanged (no sourceLanguage)",
        )
        return output
    translations, salvaged_mask = batched

    # Judge the echo gate only on strings the model actually translated. Salvaged
    # strings are their English originals by construction, so including them would
    # inflate the mirror ratio and could discard a genuinely-translated surface
    # just because part of it was salvaged. At least one non-salvaged string
    # exists here (else _translate_in_batches returned None).
    mirror_originals = [o for o, sal in zip(originals, salvaged_mask) if not sal]
    mirror_translations = [t for t, sal in zip(translations, salvaged_mask) if not sal]
    if is_mirror(mirror_originals, mirror_translations):
        logger.warning(
            "Translation appears to mirror English — discarding sourceLanguage",
        )
        return output

    # Build the translated source-language artifact from a deep copy so a later
    # mutation of the English ``output`` (still referenced by ctx) can't corrupt
    # the persisted block.
    translated = copy.deepcopy(output)
    for (path, _), value in zip(pairs, translations):
        _set_at_path(translated, path, value)
    if isinstance(translated.get("meta"), dict):
        translated["meta"]["language"] = target_lang
        translated["meta"]["isRTL"] = is_rtl(target_lang)

    # Top level stays English; attach the translated copy under sourceLanguage.
    result = copy.deepcopy(output)
    if isinstance(result.get("meta"), dict):
        result["meta"]["language"] = "en"
        result["meta"]["isRTL"] = False
    result["sourceLanguage"] = {
        "code": target_lang,
        "name": get_native_name(target_lang),
        "isRTL": is_rtl(target_lang),
        "tabs": translated.get("tabs", []),
        "meta": translated.get("meta", {}),
    }
    return result


async def translate_text(
    llm_service: LLMService,
    text: str,
    source_language: str,
    target_language: str,
) -> str | None:
    """Translate a single string (e.g. the video title). None on failure."""
    if not text:
        return None
    result = await translate_flat_list(
        llm_service,
        [text],
        source_language,
        target_language,
        stage_name="translation_title",
    )
    return result[0] if result else None
