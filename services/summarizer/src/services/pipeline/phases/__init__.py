"""Pipeline phase functions for the summarization pipeline.

Each phase is an async generator that takes a PipelineContext, mutates it,
and yields SSE event strings.

Imports are lazy to avoid pulling heavy dependencies (yt_dlp, google-genai)
during test collection or module-level import.
"""

from __future__ import annotations


def run_phase_metadata(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.metadata import run_phase_metadata as _fn

    return _fn(ctx)


def run_phase_transcript(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.transcript import run_phase_transcript as _fn

    return _fn(ctx)


def run_phase_frames(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.frames import run_phase_frames as _fn

    return _fn(ctx)


def run_phase_plan(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.triage import run_phase_plan as _fn

    return _fn(ctx)


def run_phase_memory(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.memory import run_phase_memory as _fn

    return _fn(ctx)


def run_phase_text(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.text import run_phase_text as _fn

    return _fn(ctx)


# Backward-compat alias
run_phase_triage = run_phase_plan


def run_phase_extraction(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.extraction import run_phase_extraction as _fn

    return _fn(ctx)


def run_phase_synthesis(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.synthesis import run_phase_synthesis as _fn

    return _fn(ctx)


def run_phase_enrichment(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.enrichment import run_phase_enrichment as _fn

    return _fn(ctx)


def run_phase_assembly(ctx):  # type: ignore[no-untyped-def]
    from src.services.pipeline.phases.assembly import run_phase_assembly as _fn

    return _fn(ctx)


__all__ = [
    "run_phase_metadata",
    "run_phase_transcript",
    "run_phase_frames",
    "run_phase_plan",
    "run_phase_memory",
    "run_phase_text",
    "run_phase_triage",  # Backward-compat alias for run_phase_plan
    "run_phase_extraction",
    "run_phase_synthesis",
    "run_phase_enrichment",
    "run_phase_assembly",
]
