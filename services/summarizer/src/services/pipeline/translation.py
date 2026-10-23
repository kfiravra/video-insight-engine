"""Translation step — English-canonical pipeline.

Generation always produces English, so the assembled output IS the English
primary. This step translates that English output INTO the detected source
language and nests the result under ``sourceLanguage`` so the FE toggle can
swap the whole surface. The English top-level is returned unchanged.

It walks the output tree recursively, collects every translatable prose string
into a flat list (filtered by a leaf-only deny-list of structural, asset, and
enum-shaped keys), translates them on the fast model in small batches run in
parallel (``TRANSLATION_BATCH_CHARS`` source characters each, so the phase
takes about one small call's time and a large video is fully translated rather
than capped), and applies the translations back into a deep copy that becomes
the ``sourceLanguage`` artifact ({code, name, isRTL, tabs, meta}).

A speaker's verbatim line (``translation_verbatim``: a found ``quote`` and any leaf of
its card restating it, a verified interview ``question``) is already in the source
language: it is never sent, and the English rendering beside it (``quoteEn`` /
``questionEn``) is left out of the source-language copy (D12, output-quality 4.2). That
English is written by the source→English pass over the same lines (``translation_quotes``, 4.6c).

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
from .translation_flat import (
    TRANSLATION_BATCH_CHARS,
    char_batches,
    is_batch_echo,
    is_mirror,
    translate_flat_list,
)
from .translation_verbatim import ENGLISH_ONLY_KEYS, drop_english_only, is_verbatim_line

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

# Keys whose PRIMITIVE STRING values must NOT be translated. LEAF-ONLY: the rule
# applies only when the value at the key is a string — lists and dicts are always
# recursed, so a container under a deny-listed name (``warmup: [{...}]``) still is.
_SKIP_KEYS: frozenset[str] = frozenset(
    {
        # Structural identifiers / routing. ``degradedFrom`` names a component;
        # ``primaryTag`` picks the page's domain (translated, the web reads learning).
        "id",
        "component",
        "degradedFrom",
        "language",
        "targetTab",
        "type",
        "layout",
        "platform",
        "primaryTag",
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
        # An exchange row's question check ("verified" / "translated", 4.0 c / 4.1).
        "questionStatus",
        # Enums the web matches in English (pC review): a translated claim ``status``
        # fails the claims schema and the source view's tab fell to display_section
        # (S15); so would a verdict ``badge``, a row's ``winner``, an attachment's
        # ``slot`` / ``size``, a tip's ``style`` and a grid's / deck's ``mode``.
        "status",
        "badge",
        "winner",
        "slot",
        "size",
        "style",
        "mode",
        # The raw YouTube title, source-language even in the English meta: the phase
        # translates it both ways (``_translate_title``), so it never pollutes mirror detection.
        "videoTitle",
        # Always a number in the fitness schema. ``reps`` / ``rest`` / ``duration``
        # are deliberately ABSENT: the schema writes prose there ("30 שניות", "AMRAP").
        "sets",
        # Vision output, prompted in English (a scene type is an enum token). ``frameOcr``
        # is deliberately ABSENT: OCR is the on-screen language, translated as prose.
        "frameCaption",
        "frameSceneType",
        "sceneType",
        "frameEvidence",
    }
)

# Min collected-string length (after strip): skips numerics, single tokens and
# 1-3 char strings, usually labels in another vocabulary (enum-ish remnants).
_MIN_TRANSLATABLE_LEN = 3

# Runaway guard so a pathological payload (deeply nested or an oversized blob in
# props) can't blow the recursion stack: far above any real shape (a few hundred
# strings, ~6 levels). Batching keeps the call small, so nothing is dropped.
_MAX_COLLECTED_STRINGS = 5000
_MAX_RECURSION_DEPTH = 32

# Strings per call, next to the character budget (``TRANSLATION_BATCH_CHARS``):
# every entry adds its JSON quoting to the answer and one more way to miscount
# the list, so a long run of short labels is cut by count instead.
_MAX_TRANSLATION_BATCH = 100

# Calls in flight per video, retry halves included. One output's batches
# (7 on EqaMrzd9nZU) all start at once, so the phase takes about one batch's
# time; a longer output queues. The provider reserves each request's prompt
# plus ``max_tokens`` against its tokens-per-minute limit when the request
# starts: these 8 and the RAG text translation's 4 alongside hold ≈ 55k tokens
# at peak (``max_tokens`` sized to the batch; ≈ 150k with the old flat 16,384),
# per pipeline run — a worker runs WORKER_CONCURRENCY of them (prod 1, dev 2).
_MAX_CONCURRENT_TRANSLATION_BATCHES = 8

# JSON path — alternating dict keys (str) and list indices (int).
JsonPath = tuple[str | int, ...]


# ─── Walker ──────────────────────────────────────────────────────────────────


def _kept_as_is(parent: dict[str, Any], key: str, value: str) -> bool:
    """A string leaf the walker never sends to translation."""
    if key in _SKIP_KEYS or key in ENGLISH_ONLY_KEYS:
        return True
    return is_verbatim_line(parent, key, value)


def _collect_strings(
    obj: Any,
    path: JsonPath,
    pairs: list[tuple[JsonPath, str]],
    depth: int = 0,
) -> None:
    """Recursively append ``(path, value)`` for every translatable string.

    Container nodes (list/dict) are ALWAYS recursed. The deny-list applies
    ONLY when the value at a key is a primitive string — see ``_SKIP_KEYS``; so
    do D12's verbatim lines and their English renderings (``_kept_as_is``).
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
            if isinstance(value, str) and _kept_as_is(obj, key, value):
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


async def _call(
    llm_service: LLMService,
    batch: list[str],
    source_language: str,
    target_language: str,
    slots: asyncio.Semaphore,
) -> list[str] | None:
    """One ``translation_full`` call, in one of the video's ``slots``; None when it
    failed or echoed its batch, so an echo takes the half retry and is salvaged
    (masked) rather than shipping English inside the source view. The echo test
    is the per-batch one: names kept verbatim in a small batch are no echo."""
    async with slots:
        result = await translate_flat_list(llm_service, batch, source_language, target_language)
    if result is None or is_batch_echo(batch, result):
        return None
    return result


def _kept_or_salvaged(
    batch: list[str], result: tuple[list[str], list[bool]] | list[str] | BaseException | None
) -> tuple[list[str], list[bool]]:
    """``batch``'s ``(strings, salvaged_mask)`` from its call's ``result`` — a batch's
    pair or a half's translated list. A failed call (None) or one that raised keeps the
    batch's English, salvaged."""
    if isinstance(result, tuple):
        return result
    if isinstance(result, list):
        return result, [False] * len(result)
    if isinstance(result, BaseException):
        logger.warning(
            "Translation batch of %d strings raised %s: %s — kept as English",
            len(batch),
            type(result).__name__,
            result,
        )
    return batch, [True] * len(batch)


async def _translate_or_salvage(
    llm_service: LLMService,
    batch: list[str],
    source_language: str,
    target_language: str,
    slots: asyncio.Semaphore,
) -> tuple[list[str], list[bool]]:
    """Translate ``batch``; on failure retry once at half size, then salvage.

    Returns ``(strings, salvaged_mask)`` — a SAME-LENGTH list where any string
    that could not be translated falls back to its English original (so the
    positional path mapping in ``translate_to_source`` stays valid) plus a
    per-string mask where ``True`` marks an English-fallback string (not really
    translated). The mask lets the caller exclude salvaged strings from the
    ``is_mirror`` echo check — they are English by construction and would
    otherwise inflate the mirror ratio. A count mismatch on a large batch
    ("expected 40, got 23") usually means the model truncated its output, so a
    half-size retry typically fits within the budget; only a half that still
    fails is salvaged as English.
    """
    result = await _call(llm_service, batch, source_language, target_language, slots)
    if result is not None:
        return result, [False] * len(result)
    if len(batch) <= 1:
        return batch, [True] * len(batch)  # single string failed — keep English

    mid = len(batch) // 2
    halves_src = (batch[:mid], batch[mid:])
    halves_res = await asyncio.gather(
        *(_call(llm_service, half, source_language, target_language, slots) for half in halves_src),
        return_exceptions=True,
    )
    out: list[str] = []
    salvaged_mask: list[bool] = []
    for half_src, half_res in zip(halves_src, halves_res):
        chunk, chunk_mask = _kept_or_salvaged(half_src, half_res)
        out.extend(chunk)
        salvaged_mask.extend(chunk_mask)
    salvaged = sum(salvaged_mask)
    if salvaged:
        logger.warning(
            "Translation salvaged %d/%d strings as English after batch retry",
            salvaged,
            len(batch),
        )
    return out, salvaged_mask


async def _all_batches(
    llm_service: LLMService,
    batches: list[list[str]],
    source_language: str,
    target_language: str,
) -> tuple[list[str], list[bool]]:
    """Every batch through ``_translate_or_salvage``, concatenated in order. Every batch
    is awaited: one that raises costs its own strings, kept as English, never its
    siblings' (left running, unawaited and billed, when gather re-raised — pC review)."""
    # Each call takes a slot, not each batch: a batch retrying as two halves
    # holds none while it waits, so retries never push past the cap.
    slots = asyncio.Semaphore(_MAX_CONCURRENT_TRANSLATION_BATCHES)
    results = await asyncio.gather(
        *(
            _translate_or_salvage(llm_service, batch, source_language, target_language, slots)
            for batch in batches
        ),
        return_exceptions=True,
    )
    out: list[str] = []
    salvaged_mask: list[bool] = []
    for batch, result in zip(batches, results):
        chunk, chunk_mask = _kept_or_salvaged(batch, result)
        out.extend(chunk)
        salvaged_mask.extend(chunk_mask)
    return out, salvaged_mask


async def translate_in_batches(
    llm_service: LLMService,
    strings: list[str],
    source_language: str,
    target_language: str,
) -> tuple[list[str], list[bool]] | None:
    """Translate ``strings`` in concurrent size-bounded batches, concatenated in order
    (the output into the source language here; the verbatim lines into English,
    ``translation_quotes``).

    Resilient to a single bad batch: a batch that fails (LLM error or a count
    mismatch) is retried once split in half, and any half that still fails falls
    back to its English source strings, as does a batch whose call raises
    (``_all_batches``) — a mostly-translated source-language surface is far
    better than none (the previous all-or-nothing behaviour discarded the
    entire ``sourceLanguage`` block on one truncated batch).

    Returns ``(strings, salvaged_mask)`` — the translated strings (English in
    salvaged positions) and a per-string mask where ``True`` marks a salvaged
    English-fallback string. Returns None ONLY when no batch produced a real
    translation (every string salvaged), so the caller's ``is_mirror`` gate /
    no-toggle path still apply. Concurrency is bounded by
    ``_MAX_CONCURRENT_TRANSLATION_BATCHES`` to stay under provider rate limits.
    """
    if not strings:
        return [], []
    batches = char_batches(strings, TRANSLATION_BATCH_CHARS, _MAX_TRANSLATION_BATCH)
    out, salvaged_mask = await _all_batches(llm_service, batches, source_language, target_language)
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
        llm_service: LLM service for the fast-model calls.
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

    batched = await translate_in_batches(llm_service, originals, "en", target_lang)
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
    # exists here (else translate_in_batches returned None).
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
    drop_english_only(translated)
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
