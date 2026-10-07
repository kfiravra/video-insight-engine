"""Leave the process as a replay found it: ``src`` module globals and lru caches.

A replay runs the real pipeline, which warms process-wide state — prompt-file
lru caches (``prompt_builder._read_file_cached``), per-model provider caches
(``llm_retry._wrap_with_override``), lazily-set module globals
(``transcript_chunker._CHAPTER_DETECT_PROMPT``). Left behind, that state leaks
into later tests (e.g. a test patching ``Path.read_text`` reads the cached
real prompt instead). The pipeline's lazily-imported modules are imported
BEFORE the snapshot so their globals are covered too; afterwards every
rebound global is put back, globals the run added are dropped, and every
lru cache that grew is cleared. ``_LAZY_PIPELINE_MODULES`` is hand-kept, so a
run that imports a ``src`` module it does not list fails loudly, naming it.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Any

_SRC_PREFIX = "src."
# Imported inside functions by the pipeline (phase wrappers, lazy helpers);
# importing them first keeps a replay from adding unsnapshotted modules.
_LAZY_PIPELINE_MODULES = (
    "src.services.pipeline.phases.metadata",
    "src.services.pipeline.phases.transcript",
    "src.services.pipeline.phases.frames",
    "src.services.pipeline.phases.triage",
    "src.services.pipeline.phases.extraction",
    "src.services.pipeline.phases.synthesis",
    "src.services.pipeline.phases.enrichment",
    "src.services.pipeline.phases.assembly",
    "src.services.pipeline.phases.translation",
    "src.services.transcription.transcript_chunker",
    "src.services.pipeline.scene_frames",
    "src.services.pipeline.faithfulness",
    "src.services.media.frame_analyzer",
    "src.services.media.frame_scorer",
    "src.services.media.hires_prefetch",
    "src.services.media.hires_refiner",
    "src.services.media.local_video",
    "src.services.media.download_utils",
    "src.services.pipeline.assembly.moment_frame_fill",
    "src.services.video.description_analyzer",
    "src.services.video.youtube",
    "src.utils.language_utils",
    "src.routes.cached_response",
)


class UnsnapshottedModulesError(RuntimeError):
    """The replay imported ``src`` modules whose state was never snapshotted."""


@dataclass(frozen=True)
class _Snapshot:
    module_globals: dict[str, dict[str, Any]]
    cache_sizes: dict[str, int]


def _src_modules() -> dict[str, ModuleType]:
    return {
        name: module
        for name, module in list(sys.modules.items())
        if name.startswith(_SRC_PREFIX) and isinstance(module, ModuleType)
    }


def _lru_caches(modules: dict[str, ModuleType]) -> dict[str, Callable[..., Any]]:
    """Module-level ``functools.lru_cache`` wrappers defined in each module."""
    caches = {}
    for name, module in modules.items():
        for attr, value in list(vars(module).items()):
            if hasattr(value, "cache_clear") and getattr(value, "__module__", None) == name:
                caches[f"{name}.{attr}"] = value
    return caches


def _take_snapshot() -> _Snapshot:
    modules = _src_modules()
    return _Snapshot(
        module_globals={name: dict(vars(module)) for name, module in modules.items()},
        cache_sizes={name: fn.cache_info().currsize for name, fn in _lru_caches(modules).items()},
    )


def _restore_globals(module: ModuleType, saved: dict[str, Any]) -> None:
    current = vars(module)
    for attr in [a for a in current if a not in saved]:
        if not isinstance(current[attr], ModuleType):
            delattr(module, attr)
    for attr, value in saved.items():
        if current.get(attr) is not value:
            setattr(module, attr, value)


def _restore(snapshot: _Snapshot) -> list[str]:
    """Put the snapshot back; returns ``src`` modules first imported during the run."""
    modules = _src_modules()
    for name, saved in snapshot.module_globals.items():
        if name in modules:
            _restore_globals(modules[name], saved)
    for name, fn in _lru_caches(modules).items():
        if fn.cache_info().currsize != snapshot.cache_sizes.get(name, 0):
            fn.cache_clear()
    return sorted(set(modules) - set(snapshot.module_globals))


@contextmanager
def preserved_process_state() -> Iterator[None]:
    """Snapshot ``src`` module state, run the body, then put it back."""
    for module_name in _LAZY_PIPELINE_MODULES:
        importlib.import_module(module_name)
    snapshot = _take_snapshot()
    try:
        yield
    finally:
        new_modules = _restore(snapshot)
        if new_modules:
            raise UnsnapshottedModulesError(
                "replay imported src modules missing from _LAZY_PIPELINE_MODULES "
                f"(their globals/caches are not restored): {', '.join(new_modules)}"
            )
