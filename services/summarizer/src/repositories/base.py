"""Repository protocol definitions for video summarization."""

from typing import Optional, Protocol

from src.models.schemas import ErrorCode, ProcessingStatus


class VideoRepository(Protocol):
    """Protocol for video summary storage operations."""

    def get_video_summary(self, video_summary_id: str) -> Optional[dict]:
        """Get video summary cache entry by ID."""
        ...

    def update_status(
        self,
        video_summary_id: str,
        status: ProcessingStatus,
        error_message: Optional[str] = None,
        error_code: Optional[ErrorCode] = None,
    ) -> None:
        """Update processing status."""
        ...

    def save_structured_result(self, video_summary_id: str, result: dict) -> bool:
        """Save structured pipeline result."""
        ...

    def increment_retry(self, video_summary_id: str) -> int:
        """Increment retry count and return new value."""
        ...
