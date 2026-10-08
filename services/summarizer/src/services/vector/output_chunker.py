"""Per-component output chunker for assembled tab output.

Walks ``assembled_tabs: list[TabEntry-shaped dict]`` and emits one
``OutputChunk`` per natural retrieval unit (one summary, one quiz question,
one comparison row...). Each chunk records the originating tab, component,
and prop path so retrieval results can deep-link back into the UI.

Design rules (authoritative — see ``rag-vector-alignment-plan.md``):

* **Per-component table** — every supported component has an explicit
  rendering rule. Components NOT in ``_COMPONENT_HANDLERS`` emit nothing.
  This is deliberate: silent fallbacks would index button labels, IDs, and
  enum flags as embeddings, polluting retrieval with low-signal noise.
* **<6-word drop** — a chunk whose final text has fewer than 6 whitespace-
  separated tokens is discarded. Below that, embeddings encode incidentals
  (single words, fragments) more than meaning.
* **No code, numbers, timestamps, URLs, raw JSON** — only natural-language
  prose. Code/number-shaped fields are skipped at the per-component layer.
* **``display_section`` whitelist** — for the catch-all generic component,
  only values under a fixed set of "prose-shaped" keys are flattened.
  Anything else (IDs, mapQuery strings, button labels) is silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

_MIN_WORDS = 6

_DISPLAY_SECTION_WHITELIST = frozenset(
    [
        "text",
        "description",
        "summary",
        "explanation",
        "content",
        "analysis",
        "caption",
        "label",
        "instruction",
        "tip",
        "note",
    ]
)

_WORD_SPLITTER = re.compile(r"\s+")


@dataclass
class OutputChunk:
    """A retrievable chunk extracted from one prop path of one tab."""

    text: str
    tab_id: str
    tab_component: str
    prop_path: str


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def chunk_assembled_tabs(tabs: list[dict]) -> list[OutputChunk]:
    """Convert assembled tabs into a flat list of retrievable text chunks."""
    chunks: list[OutputChunk] = []
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        tab_id = tab.get("id") or ""
        component = tab.get("component") or ""
        if not tab_id or not component:
            continue

        handler = _COMPONENT_HANDLERS.get(component)
        props = tab.get("props") or {}
        if handler is not None and isinstance(props, dict):
            for chunk in handler(tab_id, component, props):
                if _meets_minimum_length(chunk.text):
                    chunks.append(chunk)

        # Secondary-tier attachments (interactive-overhaul-v2 P2) hang off the
        # tab; route each through its own handler so distilled tips / summaries
        # stay RAG-retrievable. Derivative content (frames, reused quiz) is
        # deduplicated downstream by the embedding store.
        for att in tab.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            att_component = att.get("component") or ""
            att_handler = _COMPONENT_HANDLERS.get(att_component)
            att_props = att.get("props") or {}
            if att_handler is None or not isinstance(att_props, dict):
                continue
            for chunk in att_handler(tab_id, att_component, att_props):
                if _meets_minimum_length(chunk.text):
                    chunks.append(chunk)

    return chunks


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _meets_minimum_length(text: str) -> bool:
    return len(_WORD_SPLITTER.split(text.strip())) >= _MIN_WORDS if text else False


def _norm(value: Any) -> str:
    """Coerce a raw prop value to a stripped string; return '' for None/non-str."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _join_clean(parts: list[str], sep: str = " ") -> str:
    """Join non-empty parts with ``sep``, collapsing internal whitespace."""
    cleaned = [_WORD_SPLITTER.sub(" ", p.strip()) for p in parts if p and p.strip()]
    return sep.join(cleaned)


def _make(text: str, tab_id: str, component: str, prop_path: str) -> OutputChunk:
    return OutputChunk(text=text, tab_id=tab_id, tab_component=component, prop_path=prop_path)


# ──────────────────────────────────────────────────────────────────────
# Per-component handlers — each returns a generator of OutputChunk
# (callers filter by minimum length).
# ──────────────────────────────────────────────────────────────────────


def _h_overview(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """The overview assembler stores its fields under ``props.data`` (A23: never
    indexed while this read ``props``); a flat ``props`` is still accepted."""
    data = props.get("data")
    fields, prefix = (data, "data.") if isinstance(data, dict) else (props, "")
    out: list[OutputChunk] = []
    master = _norm(fields.get("masterSummary"))
    if master:
        out.append(_make(master, tab_id, component, f"{prefix}masterSummary"))
    for i, item in enumerate(fields.get("keyTakeaways") or []):
        text = _norm(item)
        if text:
            out.append(_make(text, tab_id, component, f"{prefix}keyTakeaways[{i}]"))
    tldr = _norm(fields.get("tldr"))
    if tldr:
        out.append(_make(tldr, tab_id, component, f"{prefix}tldr"))
    return out


def _h_spot_explorer(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, spot in enumerate(props.get("spots") or []):
        if not isinstance(spot, dict):
            continue
        name = _norm(spot.get("name"))
        description = _norm(spot.get("description"))
        tips = spot.get("tips")
        tips_text = ""
        if isinstance(tips, list):
            tips_text = _join_clean([_norm(t) for t in tips], sep=" ")
        elif isinstance(tips, str):
            tips_text = _norm(tips)
        parts = []
        if name:
            parts.append(f"{name}.")
        if description:
            parts.append(description)
        if tips_text:
            parts.append(f"Tips: {tips_text}")
        text = _join_clean(parts)
        if text:
            out.append(_make(text, tab_id, component, f"spots[{i}]"))
    return out


def _h_timeline(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """Legacy — superseded by ``moment_track``. Kept so historical tabs that
    were serialized with ``component="timeline"`` still chunk correctly on
    reprocess. Safe to remove once production data no longer contains it.
    """
    out: list[OutputChunk] = []
    for i, entry in enumerate(props.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        timestamp = _norm(entry.get("timestamp"))
        label = _norm(entry.get("label"))
        if not label:
            continue
        text = f"At {timestamp}: {label}" if timestamp else label
        out.append(_make(text, tab_id, component, f"entries[{i}]"))
    return out


def _h_moment_track(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """Unified replacement for legacy ``timeline`` and ``clip_player``.

    Pulls only prose fields (``label`` and ``description``). Numeric
    ``seconds`` / ``endSeconds`` and pre-formatted ``time`` strings are
    intentionally excluded — embedding them poisons retrieval with low-signal
    numbers and clock-shaped tokens.
    """
    out: list[OutputChunk] = []
    for i, item in enumerate(props.get("items") or []):
        if not isinstance(item, dict):
            continue
        label = _norm(item.get("label"))
        description = _norm(item.get("description"))
        if not (label or description):
            continue
        text = f"{label}. {description}" if (label and description) else (label or description)
        out.append(_make(text, tab_id, component, f"items[{i}]"))
    return out


def _h_code_explorer(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, snippet in enumerate(props.get("snippets") or []):
        if not isinstance(snippet, dict):
            continue
        explanation = _norm(snippet.get("explanation"))
        if explanation:
            out.append(_make(explanation, tab_id, component, f"snippets[{i}].explanation"))
    return out


def _h_comparison(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, p in enumerate(props.get("pros") or []):
        text = _norm(p)
        if text:
            out.append(_make(text, tab_id, component, f"pros[{i}]"))
    for i, c in enumerate(props.get("cons") or []):
        text = _norm(c)
        if text:
            out.append(_make(text, tab_id, component, f"cons[{i}]"))
    for i, row in enumerate(props.get("comparisons") or []):
        if not isinstance(row, dict):
            continue
        feature = _norm(row.get("feature"))
        this_p = _norm(row.get("thisProduct"))
        competitor = _norm(row.get("competitor"))
        if feature and (this_p or competitor):
            text = f"{feature}: {this_p} vs {competitor}".strip()
            out.append(_make(text, tab_id, component, f"comparisons[{i}]"))
    # The retired standalone verdict component folds into the comparison tab's
    # ReviewSummary header (props.verdict); chunk its text here so the review's
    # bottom-line + audience guidance stays retrievable for RAG.
    verdict = props.get("verdict")
    if isinstance(verdict, dict):
        out.extend(_verdict_chunks(tab_id, component, verdict, prefix="verdict."))
    return out


def _verdict_chunks(
    tab_id: str,
    component: str,
    props: dict,
    prefix: str = "",
) -> list[OutputChunk]:
    """Chunk a verdict block (bottomLine + bestFor/notFor lists)."""
    out: list[OutputChunk] = []
    bottom_line = _norm(props.get("bottomLine"))
    if bottom_line:
        out.append(_make(bottom_line, tab_id, component, f"{prefix}bottomLine"))
    for i, item in enumerate(props.get("bestFor") or []):
        text = _norm(item)
        if text:
            out.append(_make(text, tab_id, component, f"{prefix}bestFor[{i}]"))
    for i, item in enumerate(props.get("notFor") or []):
        text = _norm(item)
        if text:
            out.append(_make(text, tab_id, component, f"{prefix}notFor[{i}]"))
    return out


def _h_info_grid(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, item in enumerate(props.get("items") or []):
        if not isinstance(item, dict):
            continue
        key = _norm(item.get("key"))
        value = _norm(item.get("value"))
        if key and value:
            out.append(_make(f"{key}: {value}", tab_id, component, f"items[{i}]"))
    return out


def _h_checklist(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, item in enumerate(props.get("items") or []):
        if not isinstance(item, dict):
            continue
        label = _norm(item.get("label"))
        note = _norm(item.get("note"))
        if not label:
            continue
        text = f"{label}. {note}" if note else label
        out.append(_make(text, tab_id, component, f"items[{i}]"))
    return out


def _h_step_player(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, step in enumerate(props.get("steps") or []):
        if not isinstance(step, dict):
            continue
        instruction = _norm(step.get("instruction"))
        tips = step.get("tips")
        tips_text = ""
        if isinstance(tips, list):
            tips_text = _join_clean([_norm(t) for t in tips])
        elif isinstance(tips, str):
            tips_text = _norm(tips)
        if not instruction:
            continue
        text = f"{instruction} Tips: {tips_text}" if tips_text else instruction
        out.append(_make(text, tab_id, component, f"steps[{i}]"))
    return out


def _h_exercise_tracker(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, ex in enumerate(props.get("exercises") or []):
        if not isinstance(ex, dict):
            continue
        name = _norm(ex.get("name"))
        form_cues = ex.get("formCues")
        cues_text = ""
        if isinstance(form_cues, list):
            cues_text = ", ".join([c for c in (_norm(x) for x in form_cues) if c])
        if not name:
            continue
        text = f"{name}. Form cues: {cues_text}" if cues_text else name
        out.append(_make(text, tab_id, component, f"exercises[{i}]"))
    return out


def _h_quiz(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, q in enumerate(props.get("questions") or []):
        if not isinstance(q, dict):
            continue
        question = _norm(q.get("question"))
        options = q.get("options")
        correct_index = q.get("correctIndex")
        explanation = _norm(q.get("explanation"))
        correct_answer = ""
        if (
            isinstance(options, list)
            and isinstance(correct_index, int)
            and 0 <= correct_index < len(options)
        ):
            correct_answer = _norm(options[correct_index])
        if not question:
            continue
        parts = [f"Q: {question}"]
        if correct_answer:
            parts.append(f"A: {correct_answer}")
        if explanation:
            parts.append(f"— {explanation}")
        out.append(_make(_join_clean(parts), tab_id, component, f"questions[{i}]"))
    return out


def _h_flash_deck(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, card in enumerate(props.get("cards") or []):
        if not isinstance(card, dict):
            continue
        front = _norm(card.get("front"))
        back = _norm(card.get("back"))
        if not (front or back):
            continue
        text = f"{front} — {back}".strip(" —")
        out.append(_make(text, tab_id, component, f"cards[{i}]"))
    return out


def _h_budget(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, tip in enumerate(props.get("savingTips") or []):
        text = _norm(tip)
        if text:
            out.append(_make(text, tab_id, component, f"savingTips[{i}]"))
    return out


def _h_gallery(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, img in enumerate(props.get("images") or []):
        if not isinstance(img, dict):
            continue
        caption = _norm(img.get("caption"))
        if caption:
            out.append(_make(caption, tab_id, component, f"images[{i}].caption"))
    return out


def _h_clip_player(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """Legacy — superseded by ``moment_track``. Same retention rationale as
    ``_h_timeline``: defensive coverage for historical MongoDB records that
    were serialized before the unified component shipped.
    """
    out: list[OutputChunk] = []
    for i, clip in enumerate(props.get("clips") or []):
        if not isinstance(clip, dict):
            continue
        label = _norm(clip.get("label"))
        description = _norm(clip.get("description"))
        if not (label or description):
            continue
        text = f"{label}. {description}" if (label and description) else (label or description)
        out.append(_make(text, tab_id, component, f"clips[{i}]"))
    return out


def _h_lyrics_player(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, section in enumerate(props.get("sections") or []):
        if not isinstance(section, dict):
            continue
        analysis = _norm(section.get("analysis"))
        if analysis:
            out.append(_make(analysis, tab_id, component, f"sections[{i}].analysis"))
    return out


def _h_display_section(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """Catch-all flattener — only whitelisted prose keys are extracted.

    The whitelist is intentional: blind flattening of ``data`` would pull in
    IDs, URLs, button labels, and enum flags, polluting the embedding index.
    """
    data = props.get("data")
    parts: list[str] = []
    _collect_whitelisted_strings(data, parts)
    text = _join_clean(parts)
    if not text:
        return []
    return [_make(text, tab_id, component, "data")]


def _collect_whitelisted_strings(node: Any, out: list[str]) -> None:
    """Recursively gather string values whose key is in the whitelist."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and k in _DISPLAY_SECTION_WHITELIST:
                cleaned = v.strip()
                if cleaned:
                    out.append(cleaned)
            elif isinstance(v, (dict, list)):
                _collect_whitelisted_strings(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_whitelisted_strings(item, out)


# ──────────────────────────────────────────────────────────────────────
# Secondary-tier (attachment-only) handlers — interactive-overhaul-v2 P2.
# Attachments hang off a primary tab, so their content is largely derivative
# of the primary. tip_callout / summary_header carry a distilled line worth
# indexing on its own; stat_banner / diagram_card add short labelled facts;
# frame_strip / quick_quiz reuse the gallery / quiz handlers verbatim.
# ──────────────────────────────────────────────────────────────────────


def _h_tip_callout(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    title = _norm(props.get("title"))
    text = _norm(props.get("text"))
    body = _join_clean([f"{title}:" if title else "", text])
    return [_make(body, tab_id, component, "text")] if text else []


def _h_summary_header(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    summary = _norm(props.get("summary"))
    return [_make(summary, tab_id, component, "summary")] if summary else []


def _h_stat_banner(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, stat in enumerate(props.get("stats") or []):
        if not isinstance(stat, dict):
            continue
        label = _norm(stat.get("label"))
        value = _norm(stat.get("value"))
        if label and value:
            out.append(_make(f"{label}: {value}", tab_id, component, f"stats[{i}]"))
    return out


def _h_connect_canvas(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """One chunk per matching pair — keeps the concept relationships the quiz
    tests (prompt ↔ match) retrievable for RAG."""
    out: list[OutputChunk] = []
    for i, pair in enumerate(props.get("pairs") or []):
        if not isinstance(pair, dict):
            continue
        prompt = _norm(pair.get("prompt"))
        match = _norm(pair.get("match"))
        if not (prompt and match):
            continue
        out.append(_make(f"{prompt} — {match}", tab_id, component, f"pairs[{i}]"))
    return out


def _h_diagram_card(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    out: list[OutputChunk] = []
    for i, node in enumerate(props.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        label = _norm(node.get("label"))
        detail = _norm(node.get("detail"))
        if not label:
            continue
        text = f"{label}. {detail}" if detail else label
        out.append(_make(text, tab_id, component, f"nodes[{i}]"))
    return out


def _h_claims_tracker(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """One chunk per claim — keeps the assertion, its source, and its
    verification status retrievable for RAG ("what did X claim about Y?")."""
    out: list[OutputChunk] = []
    for i, claim in enumerate(props.get("claims") or []):
        if not isinstance(claim, dict):
            continue
        text_claim = _norm(claim.get("claim"))
        if not text_claim:
            continue
        source = _norm(claim.get("source"))
        status = _norm(claim.get("status"))
        citation = _norm(claim.get("sourceCitation"))
        parts = [text_claim]
        if source:
            parts.append(f"(claimed by {source}")
            parts[-1] += f", {status})" if status else ")"
        elif status:
            parts.append(f"({status})")
        if citation:
            parts.append(f"Source: {citation}")
        out.append(_make(_join_clean(parts), tab_id, component, f"claims[{i}]"))
    return out


def _h_tier_list(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """One chunk per ranked item — keeps the creator's tier + reason
    retrievable for RAG ("what tier is X?")."""
    out: list[OutputChunk] = []
    for i, item in enumerate(props.get("items") or []):
        if not isinstance(item, dict):
            continue
        label = _norm(item.get("item"))
        if not label:
            continue
        tier = _norm(item.get("tier"))
        reason = _norm(item.get("reason"))
        parts = [label]
        if tier:
            parts.append(f"({tier} tier)")
        if reason:
            parts.append(reason)
        out.append(_make(_join_clean(parts), tab_id, component, f"items[{i}]"))
    return out


def _h_formation_diagram(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """One chunk per player position — keeps who lined up where retrievable
    for RAG ("who played striker?")."""
    out: list[OutputChunk] = []
    for i, pos in enumerate(props.get("positions") or []):
        if not isinstance(pos, dict):
            continue
        player = _norm(pos.get("player"))
        if not player:
            continue
        role = _norm(pos.get("role"))
        text = f"{player} ({role})" if role else player
        out.append(_make(text, tab_id, component, f"positions[{i}]"))
    return out


def _h_concept_canvas(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """One chunk per concept — concept_canvas emits ``{concepts:[{name,
    definition,…}]}`` (NOT the flash_deck ``{cards}`` shape), so it needs its
    own handler or the promoted learning/science deck indexes nothing."""
    out: list[OutputChunk] = []
    for i, c in enumerate(props.get("concepts") or []):
        if not isinstance(c, dict):
            continue
        name = _norm(c.get("name"))
        if not name:
            continue
        definition = _norm(c.get("definition"))
        text = f"{name}. {definition}" if definition else name
        out.append(_make(text, tab_id, component, f"concepts[{i}]"))
    return out


def _h_packing_mission(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """One chunk per packing item — packing_mission emits ``{items:[{item,…}]}``
    keyed on ``item`` (NOT the checklist ``label``), so it needs its own handler."""
    out: list[OutputChunk] = []
    for i, it in enumerate(props.get("items") or []):
        if not isinstance(it, dict):
            continue
        label = _norm(it.get("item"))
        if not label:
            continue
        category = _norm(it.get("category"))
        text = f"{label} ({category})" if category else label
        out.append(_make(text, tab_id, component, f"items[{i}]"))
    return out


def _h_video_filmstrip(tab_id: str, component: str, props: dict) -> list[OutputChunk]:
    """One chunk per captioned frame — video_filmstrip / frame_strip emit
    ``{frames:[{caption,…}]}`` (NOT the retired gallery ``{images}`` shape)."""
    out: list[OutputChunk] = []
    for i, frame in enumerate(props.get("frames") or []):
        if not isinstance(frame, dict):
            continue
        caption = _norm(frame.get("caption"))
        if caption:
            out.append(_make(caption, tab_id, component, f"frames[{i}].caption"))
    return out


# ──────────────────────────────────────────────────────────────────────
# Registry — authoritative list of supported components.
# Adding a new render component requires adding a row here.
# ──────────────────────────────────────────────────────────────────────

_COMPONENT_HANDLERS: dict[str, Callable[[str, str, dict], list[OutputChunk]]] = {
    # ASSEMBLER_REGISTRY components — keep in sync with
    # ``services/summarizer/src/services/pipeline/assembly/registry.py``.
    # Most new components DELEGATE to a legacy assembler (code_playground ->
    # assemble_code_explorer, etc.), so their props shape is identical and they
    # reuse the matching ``_h_*`` handler. Components whose assembler emits a
    # DIFFERENT props shape (concept_canvas/{concepts}, packing_mission/{items
    # keyed on `item`}, video_filmstrip|frame_strip/{frames}) get a dedicated
    # handler — reusing the look-alike legacy handler would silently emit zero
    # chunks and drop the tab from RAG.
    "overview": _h_overview,
    "spot_explorer": _h_spot_explorer,
    "moment_track": _h_moment_track,
    "comparison": _h_comparison,
    "info_grid": _h_info_grid,
    "checklist": _h_checklist,
    "step_player": _h_step_player,
    "flash_deck": _h_flash_deck,
    "budget": _h_budget,
    "display_section": _h_display_section,
    "code_playground": _h_code_explorer,
    "workout_room": _h_exercise_tracker,
    "lyrics_karaoke": _h_lyrics_player,
    "quiz_arena": _h_quiz,
    "video_filmstrip": _h_video_filmstrip,
    "comparison_radar": _h_comparison,
    "packing_mission": _h_packing_mission,
    "step_flow_canvas": _h_step_player,
    "concept_canvas": _h_concept_canvas,
    "connect_canvas": _h_connect_canvas,
    "claims_tracker": _h_claims_tracker,
    "tier_list": _h_tier_list,
    "formation_diagram": _h_formation_diagram,
    # Secondary-tier (attachment-only) — interactive-overhaul-v2 P2.
    "tip_callout": _h_tip_callout,
    "summary_header": _h_summary_header,
    "stat_banner": _h_stat_banner,
    "diagram_card": _h_diagram_card,
    "frame_strip": _h_video_filmstrip,
    "quick_quiz": _h_quiz,
    # Legacy — superseded in the video-to-action overhaul. Retained so historical
    # MongoDB records keep producing chunks on reprocess. ``gallery`` predates
    # ``video_filmstrip`` and uses the ``{images}`` shape ``_h_gallery`` reads.
    # Remove once ``db.videoSummary.distinct("tabs.component")`` no longer
    # returns them.
    "timeline": _h_timeline,
    "clip_player": _h_clip_player,
    "gallery": _h_gallery,
}
