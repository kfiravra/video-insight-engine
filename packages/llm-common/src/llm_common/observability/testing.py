"""Test helper: the real Langfuse v4 SDK exporting to memory instead of the network.

For test suites only (the services' ``langfuse_capture`` fixtures).
:func:`start_capture` initialises the shared client with an in-memory span
exporter and a recording ``create_score``, so a test asserts on what Langfuse
would ingest: observations with their model, usage, cost, metadata and parent
span, and the scores sent. :func:`stop_capture` resets the client and the SDK.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from llm_common.observability import client as shared_client

_META_PREFIX = "langfuse.observation.metadata."


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


@dataclass
class Observation:
    """One exported observation, its Langfuse attributes decoded."""

    name: str
    type: str | None
    parent: str | None
    trace_id: str
    attributes: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def attr(self, key: str) -> Any:
        return _maybe_json(self.attributes.get(f"langfuse.observation.{key}"))

    @property
    def model(self) -> str | None:
        return self.attributes.get("langfuse.observation.model.name")

    @property
    def model_parameters(self) -> dict[str, Any] | None:
        return self.attr("model.parameters")

    @property
    def usage(self) -> dict[str, int] | None:
        return self.attr("usage_details")

    @property
    def cost(self) -> dict[str, float] | None:
        return self.attr("cost_details")

    @property
    def level(self) -> str | None:
        return self.attributes.get("langfuse.observation.level")


@dataclass
class LangfuseCapture:
    exporter: InMemorySpanExporter
    create_score: MagicMock

    def observations(self) -> list[Observation]:
        client = shared_client.get_client()
        if client is not None:
            client.flush()
        spans: list[ReadableSpan] = list(self.exporter.get_finished_spans())
        names = {s.context.span_id: s.name for s in spans}
        out = []
        for s in spans:
            attrs = dict(s.attributes or {})
            out.append(
                Observation(
                    name=s.name,
                    type=attrs.get("langfuse.observation.type"),
                    parent=names.get(s.parent.span_id) if s.parent else None,
                    trace_id=format(s.context.trace_id, "032x"),
                    attributes=attrs,
                    metadata={
                        k.removeprefix(_META_PREFIX): _maybe_json(v)
                        for k, v in attrs.items()
                        if k.startswith(_META_PREFIX)
                    },
                )
            )
        return out

    def generations(self, name: str | None = None) -> list[Observation]:
        return [
            o
            for o in self.observations()
            if o.type == "generation" and (name is None or o.name == name)
        ]

    def named(self, name: str) -> Observation:
        matches = [o for o in self.observations() if o.name == name]
        assert len(matches) == 1, f"{len(matches)} observations named {name!r}"
        return matches[0]

    @property
    def scores(self) -> list[dict[str, Any]]:
        return [c.kwargs for c in self.create_score.call_args_list]


def start_capture() -> LangfuseCapture:
    """Initialise the shared client against memory; the caller resets it afterwards."""
    shared_client._reset_for_tests()
    exporter = InMemorySpanExporter()
    settings = SimpleNamespace(
        LANGFUSE_PUBLIC_KEY=f"pk-lf-test-{uuid4().hex}",
        LANGFUSE_SECRET_KEY="sk-lf-test",
        LANGFUSE_BASE_URL="http://127.0.0.1:9",
        ENVIRONMENT="test",
        LANGFUSE_USER_ID_MODE="identity",
        LANGFUSE_USER_ID_HASH_SALT="",
    )
    client = shared_client.init_langfuse(settings, span_exporter=exporter)
    assert client is not None
    create_score = MagicMock(name="create_score")
    client.create_score = create_score
    return LangfuseCapture(exporter=exporter, create_score=create_score)


def stop_capture() -> None:
    from langfuse._client.resource_manager import LangfuseResourceManager

    shared_client._reset_for_tests()
    LangfuseResourceManager.reset()
