"""Zero-unreplaced-placeholder guard across every prompt template.

Each stage renders its prompt via chained ``.replace("{name}", ...)`` calls —
so a template can gain a placeholder (or a stage can lose a replace line) and
nothing fails until an LLM sees a literal ``{tab_goals}`` in production.

Two layers:

- **Per-stage render tests** — drive every prompt-building stage with
  representative inputs, capture the prompt(s) actually handed to the LLM
  call, and assert none of the placeholders declared by the underlying
  template file(s) survive in the rendered text.
- **Inventory guard** — every ``src/prompts/**/*.txt`` file that declares at
  least one placeholder must be claimed by a render test here (or explicitly
  excluded with a reason), so newly added templates can't dodge coverage.

No LLM calls are made; the LLM boundary is mocked at each stage's module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROMPTS_DIR = Path(__file__).parent.parent / "src" / "prompts"

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

# Templates with placeholders that are deliberately NOT render-tested.
# Keep this list justified — anything here is a known gap.
_EXCLUDED_TEMPLATES: dict[str, str] = {}


def _declared_placeholders(*relative_paths: str) -> set[str]:
    """Union of ``{placeholder}`` names declared by the given template files."""
    tokens: set[str] = set()
    for rel in relative_paths:
        tokens |= set(_PLACEHOLDER_RE.findall((PROMPTS_DIR / rel).read_text()))
    return tokens


def _assert_no_unreplaced(rendered: str, *template_paths: str) -> None:
    """Fail if any placeholder declared by the templates survives in ``rendered``."""
    leftover = sorted(
        tok for tok in _declared_placeholders(*template_paths) if f"{{{tok}}}" in rendered
    )
    assert not leftover, (
        f"unreplaced placeholders {leftover} from {list(template_paths)} "
        f"leaked into the rendered prompt"
    )


def _capture_llm(response: str = "") -> tuple[MagicMock, AsyncMock]:
    """(mock LLMService, AsyncMock replacing call_llm_with_retry → response)."""
    return MagicMock(), AsyncMock(return_value=response)


def _captured_prompt_text(mock_call: AsyncMock) -> str:
    """Concatenate every prompt-ish argument passed to the mocked LLM call."""
    parts: list[str] = []
    for call in mock_call.call_args_list:
        for arg in call.args[1:]:  # args[0] is the llm_service
            if isinstance(arg, str):
                parts.append(arg)
        for value in call.kwargs.values():
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


# ─── Inventory guard ────────────────────────────────────────────────────
# Template files (relative to src/prompts) claimed by a render test below.
_COVERED_TEMPLATES: set[str] = {
    "base_extraction.txt",
    "quality_rules.txt",
    "plan.txt",
    "component_toolkit.txt",
    "classify.txt",
    "synthesis.txt",
    "translate_flat.txt",
    "chapter_detect.txt",
    "description_analysis.txt",
    "memory.txt",
    *(f"schemas/{p.name}" for p in (PROMPTS_DIR / "schemas").glob("*.txt")),
    *(f"examples/{p.name}" for p in (PROMPTS_DIR / "examples").glob("*.txt")),
    *(f"enrich/{p.name}" for p in (PROMPTS_DIR / "enrich").glob("*.txt")),
}


def test_every_placeholder_template_is_render_tested():
    """New prompt templates must register a render test (or an exclusion)."""
    all_templates = {str(p.relative_to(PROMPTS_DIR)) for p in PROMPTS_DIR.rglob("*.txt")}
    with_placeholders = {rel for rel in all_templates if _declared_placeholders(rel)}
    unclaimed = with_placeholders - _COVERED_TEMPLATES - set(_EXCLUDED_TEMPLATES)
    assert not unclaimed, (
        f"templates with placeholders lack a render test in this module: "
        f"{sorted(unclaimed)} — add a per-stage test and list them in "
        f"_COVERED_TEMPLATES (or _EXCLUDED_TEMPLATES with a reason)"
    )


def test_excluded_templates_still_exist():
    """Prune _EXCLUDED_TEMPLATES entries once their template is deleted."""
    for rel in _EXCLUDED_TEMPLATES:
        assert (PROMPTS_DIR / rel).exists(), (
            f"{rel} no longer exists — remove it from _EXCLUDED_TEMPLATES"
        )


# ─── Extraction (base_extraction + schemas + examples + quality_rules) ──
_ALL_SCHEMA_TAGS = sorted(p.stem for p in (PROMPTS_DIR / "schemas").glob("*.txt"))


class TestExtractionPromptRenders:
    @pytest.mark.parametrize("tag", _ALL_SCHEMA_TAGS)
    def test_single_domain_prompt_has_no_unreplaced_placeholders(self, tag):
        from src.services.pipeline.prompt_builder import build_extraction_prompt

        prompt = build_extraction_prompt(
            [tag],
            [],
            "the transcript body goes here",
            "quality rules text",
            title="Video Title",
            duration_minutes=12,
        )
        _assert_no_unreplaced(
            prompt,
            "base_extraction.txt",
            "quality_rules.txt",
            f"schemas/{tag}.txt",
        )
        # {transcript}/{batch_context} are late-bound by design — the full
        # prompt builder must have filled both.
        assert "{transcript}" not in prompt
        assert "{batch_context}" not in prompt

    def test_modifier_schema_placeholders_are_filled(self):
        from src.services.pipeline.prompt_builder import build_extraction_prompt

        prompt = build_extraction_prompt(
            ["travel"],
            ["finance"],
            "transcript",
            "rules",
            title="T",
            duration_minutes=30,
        )
        _assert_no_unreplaced(prompt, "schemas/travel.txt", "schemas/finance.txt")

    def test_domain_example_files_declare_no_placeholders(self):
        """Examples are injected verbatim — they must never carry placeholders."""
        for example in sorted((PROMPTS_DIR / "examples").glob("*.txt")):
            assert not _declared_placeholders(f"examples/{example.name}"), (
                f"examples/{example.name} declares placeholders but is "
                f"injected without substitution"
            )

    def test_extraction_template_keeps_late_bound_placeholders_only(self):
        from src.services.pipeline.prompt_builder import build_extraction_template

        template = build_extraction_template(["tech"], [], "rules", title="T")
        remaining = {
            tok for tok in _declared_placeholders("base_extraction.txt") if f"{{{tok}}}" in template
        }
        assert remaining == {"transcript", "batch_context"}


# ─── Plan (plan.txt + component_toolkit.txt) ────────────────────────────
@pytest.mark.asyncio
async def test_plan_prompt_renders_without_placeholders():
    from src.services.pipeline import plan as plan_mod

    llm, mock_call = _capture_llm()
    with patch.object(plan_mod, "call_llm_with_retry", mock_call):
        await plan_mod.run_plan(
            title="Test Video",
            channel="Test Channel",
            description="A description of the video.",
            duration=600,
            category_hint="tech",
            content_format="tutorial",
            transcript_preview="transcript preview text",
            llm_service=llm,
            content_traits="has_code",
        )
    rendered = _captured_prompt_text(mock_call)
    assert rendered, "plan stage never reached the LLM call"
    _assert_no_unreplaced(rendered, "plan.txt", "component_toolkit.txt")


@pytest.mark.asyncio
async def test_plan_prompt_renders_with_optional_fields_absent():
    """None-able inputs (channel/description/traits) must still fill their slots."""
    from src.services.pipeline import plan as plan_mod

    llm, mock_call = _capture_llm()
    with patch.object(plan_mod, "call_llm_with_retry", mock_call):
        await plan_mod.run_plan(
            title="T",
            channel="",
            description="",
            duration=0,
            category_hint=None,
            content_format=None,
            transcript_preview="",
            llm_service=llm,
        )
    _assert_no_unreplaced(
        _captured_prompt_text(mock_call),
        "plan.txt",
        "component_toolkit.txt",
    )


# ─── Classifier (classify.txt) ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_classify_prompt_renders_without_placeholders():
    from src.services.pipeline import classifier as classifier_mod

    llm, mock_call = _capture_llm()
    with patch.object(classifier_mod, "call_llm_with_retry", mock_call):
        await classifier_mod.classify_domain_format(
            title="Test Video",
            channel="Chan",
            duration=300,
            tags=["python", "tutorial"],
            transcript_preview="preview",
            llm_service=llm,
        )
    rendered = _captured_prompt_text(mock_call)
    assert rendered, "classifier never reached the LLM call"
    _assert_no_unreplaced(rendered, "classify.txt")


# ─── Synthesis (synthesis.txt) ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_synthesis_prompt_renders_without_placeholders():
    from src.services.pipeline import synthesis as synthesis_mod

    # Synthesis raises on an empty LLM response — return a minimal valid body.
    llm, mock_call = _capture_llm(
        '{"tldr": "t", "keyTakeaways": ["k"], "masterSummary": "m", "seoDescription": "s"}'
    )
    with patch.object(synthesis_mod, "call_llm_with_retry", mock_call):
        await synthesis_mod.synthesize(
            llm_service=llm,
            title="Test",
            channel=None,
            duration=None,
            output_type="summary",
            extraction_summary="summary of extraction",
        )
    rendered = _captured_prompt_text(mock_call)
    assert rendered, "synthesis never reached the LLM call"
    _assert_no_unreplaced(rendered, "synthesis.txt")


# ─── Enrichment (enrich/enrich_*.txt, one per mapped tag) ───────────────
def _enrichment_prompt_files() -> list[tuple[str, str]]:
    """(tag, template path relative to src/prompts) — one tag per template.

    ENRICHMENT_MAP values already carry the ``enrich/`` prefix.
    """
    from src.services.pipeline.enrichment import ENRICHMENT_MAP

    seen: dict[str, str] = {}
    for tag, rel_path in sorted(ENRICHMENT_MAP.items()):
        seen.setdefault(rel_path, tag)
    return [(tag, rel_path) for rel_path, tag in sorted(seen.items())]


@pytest.mark.asyncio
@pytest.mark.parametrize("tag,rel_path", _enrichment_prompt_files())
async def test_enrichment_prompt_renders_without_placeholders(tag, rel_path):
    from src.services.pipeline import enrichment as enrichment_mod

    llm, mock_call = _capture_llm()
    extraction: dict[str, Any] = {
        "key_points": [{"text": "a meaningful extracted point about the topic"}],
    }
    with patch.object(enrichment_mod, "call_llm_with_retry", mock_call):
        await enrichment_mod.enrich(
            llm_service=llm,
            primary_tag=tag,
            extraction_data=extraction,
            title="Test Video",
        )
    rendered = _captured_prompt_text(mock_call)
    assert rendered, f"enrichment for {tag} never reached the LLM call"
    _assert_no_unreplaced(rendered, rel_path)


def test_every_enrich_template_is_reachable_via_map():
    """Each enrich/*.txt must be mapped, or it can never render (dead prompt)."""
    from src.services.pipeline.enrichment import ENRICHMENT_MAP

    mapped = set(ENRICHMENT_MAP.values())
    on_disk = {f"enrich/{p.name}" for p in (PROMPTS_DIR / "enrich").glob("*.txt")}
    assert on_disk == mapped, (
        f"enrich templates and ENRICHMENT_MAP drifted: "
        f"unmapped files {sorted(on_disk - mapped)}, "
        f"missing files {sorted(mapped - on_disk)}"
    )


# ─── Translation (translate_flat.txt) ───────────────────────────────────
@pytest.mark.asyncio
async def test_translate_flat_prompt_renders_without_placeholders():
    from src.services.pipeline import translation as translation_mod

    llm, mock_call = _capture_llm()
    with patch.object(translation_mod, "call_llm_with_retry", mock_call):
        await translation_mod._translate_flat_list(
            llm,
            ["hello world", "second string"],
            "en",
            "es",
        )
    rendered = _captured_prompt_text(mock_call)
    assert rendered, "translation never reached the LLM call"
    _assert_no_unreplaced(rendered, "translate_flat.txt")


# ─── Chapter detection (chapter_detect.txt) ─────────────────────────────
@pytest.mark.asyncio
async def test_chapter_detect_prompt_renders_without_placeholders():
    from src.services.transcription import transcript_chunker as chunker_mod

    llm, mock_call = _capture_llm()
    segments = [
        {"start": float(i * 10), "duration": 10.0, "text": f"segment {i} text"} for i in range(12)
    ]
    with patch.object(chunker_mod, "call_llm_with_retry", mock_call):
        await chunker_mod._detect_chapters_with_ai(
            title="Test Video",
            description="Description text",
            transcript=" ".join(s["text"] for s in segments),
            duration=120.0,
            segments=segments,
            llm_service=llm,
        )
    rendered = _captured_prompt_text(mock_call)
    assert rendered, "chapter detection never reached the LLM call"
    _assert_no_unreplaced(rendered, "chapter_detect.txt")


# ─── Description analysis (description_analysis.txt) ────────────────────
@pytest.mark.asyncio
async def test_description_analysis_prompt_renders_without_placeholders():
    from src.services import llm_provider as provider_mod
    from src.services.video import description_analyzer as da_mod

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = (
        '{"links": [], "resources": [], "relatedVideos": [], '
        '"hasTimestamps": false, "socialMedia": {}}'
    )
    mock_completion = AsyncMock(return_value=response)
    with patch.object(provider_mod, "acompletion", mock_completion):
        await da_mod._analyze_description_async(
            "A video description long enough to pass the minimum-length gate.",
        )
    assert mock_completion.call_args, "description analysis never called the LLM"
    messages = mock_completion.call_args.kwargs["messages"]
    rendered = "\n".join(m["content"] for m in messages)
    _assert_no_unreplaced(rendered, "description_analysis.txt")


# ─── Video memory (memory.txt) ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_memory_prompt_renders_without_placeholders():
    from src.models.memory_types import MemoryInput
    from src.services.pipeline import memory as memory_mod

    llm, mock_call = _capture_llm()
    request = MemoryInput(
        title="Test Video",
        channel="Test Channel",
        duration=600,
        description="Description text",
        transcript="[0:00] intro text\n[0:20] more text",
    )
    with patch.object(memory_mod, "call_llm_with_retry", mock_call):
        await memory_mod.run_memory(llm, request)
    rendered = _captured_prompt_text(mock_call)
    assert rendered, "memory never reached the LLM call"
    _assert_no_unreplaced(rendered, "memory.txt")
