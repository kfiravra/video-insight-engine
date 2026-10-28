"""Wire-format payload for video processing jobs.

Mirrors the Zod schema in `api/src/services/queue-topology.ts`. Use aliases so
the model accepts camelCase JSON from the publisher while exposing snake_case
attributes to Python code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


_Provider = Literal["anthropic", "openai", "gemini"]

# What pays for the run (billing-roles P3.4): ``paid`` = plan or credit
# minutes, ``free`` = the free video. Sets nothing here (the publisher already
# chose the message priority); it tags the job's Sentry transaction.
QueuePlan = Literal["free", "paid"]


class ProviderConfig(BaseModel):
    """Per-request LLM provider overrides (dev-tools flow)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    default: _Provider
    fast: _Provider | None = None
    fallback: _Provider | None = None


class VideoJobPayload(BaseModel):
    """RabbitMQ message body for video pipeline jobs.

    JSON aliases match the API publisher's camelCase wire format. Use
    ``model_dump(by_alias=True, mode="json")`` when serializing for republish.
    A message published before P3.4 carries ``tier`` instead of ``plan``:
    ``extra="ignore"`` drops it and ``plan`` defaults to ``free``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    video_summary_id: str = Field(alias="videoSummaryId", min_length=1)
    youtube_id: str = Field(alias="youtubeId", pattern=r"^[a-zA-Z0-9_-]{11}$")
    url: str
    user_id: str | None = Field(default=None, alias="userId")
    plan: QueuePlan = "free"
    priority: int = Field(ge=1, le=10)
    providers: ProviderConfig | None = None
    bypass_cache: bool = Field(default=False, alias="bypassCache")
    request_id: str = Field(alias="requestId", min_length=1)
    attempt: int = Field(default=1, ge=1)
    created_at: str = Field(alias="createdAt")
