"""Override endpoint for changing detected category during pipeline processing.

HTTP layer only — state management lives in services/override_state.py.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.routes.deps import require_internal_secret
from src.services.override_state import set_override
from src.services.video.youtube import VALID_CATEGORIES
from src.shared_config.domain_config import map_category_to_tag

logger = logging.getLogger(__name__)

router = APIRouter(tags=["override"])


class OverrideRequest(BaseModel):
    """Request body for category override."""

    category: str


class OverrideResponse(BaseModel):
    """Response from category override."""

    category: str
    contentTag: str


@router.post(
    "/override/{video_summary_id}",
    response_model=OverrideResponse,
    dependencies=[Depends(require_internal_secret)],
)
async def override_category(
    video_summary_id: str,
    request: OverrideRequest,
) -> OverrideResponse:
    """Override detected category for an active pipeline.

    Must be called before triage runs — the override provides
    a category hint that influences content tag selection.

    Requires X-Internal-Secret header matching INTERNAL_SECRET config.
    """
    category = request.category.lower().strip()

    if category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category '{request.category}'. Valid: {sorted(VALID_CATEGORIES)}",
        )

    content_tag = map_category_to_tag(category)

    set_override(
        video_summary_id,
        {
            "category": category,
        },
    )

    return OverrideResponse(
        category=category,
        contentTag=content_tag,
    )
