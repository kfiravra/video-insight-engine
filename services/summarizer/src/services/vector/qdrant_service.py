"""Qdrant vector service for transcript chunk storage and retrieval.

Provides semantic search over transcript chunks, assembled output
(tabs + props) and the frames' visual annotations using cosine similarity.
All three content sources live in a single ``transcript_chunks`` collection
and are filtered apart by the ``source`` payload field. Gracefully degrades if Qdrant is unavailable —
pipeline continues without vector storage.
"""

from __future__ import annotations

import hashlib
import logging

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from src.config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "transcript_chunks"
VECTOR_SIZE = 384  # all-MiniLM-L6-v2 / bge-small-en-v1.5

SOURCE_TRANSCRIPT = "transcript"
SOURCE_DEFAULT_OUTPUT = "default_output"
# Rendered <visual_annotations> entries (frame captions + on-screen text).
SOURCE_VISUAL = "visual"

# Payload schema version stamped on every new point.
# v2: adds ``timestamp``/``end_timestamp`` (seconds, float) for [MM:SS]
# citations. v1 points (written before this field existed) carry neither
# ``schema_version`` nor ``timestamp`` — readers must null-check (the
# assistant's formatters already do). No eager migration: the collection was
# flushed before v2 shipped, and any stray v1 points heal lazily on the next
# re-ingest of their video (store path pre-deletes by video+source).
PAYLOAD_SCHEMA_VERSION = 2

_INT63_MASK = 0x7FFFFFFFFFFFFFFF


def _point_id(
    source: str,
    video_id: str,
    tab_id: str | None,
    prop_path: str | None,
    chunk_idx: int,
) -> int:
    """Build a deterministic 63-bit point ID per (source, video, tab, prop, idx).

    Transcript IDs (source="transcript", tab_id=None, prop_path=None) keep the
    legacy ``"{video_id}_{idx}"`` key so existing points written before this
    schema extension remain addressable — required because pre-existing
    transcript points were upserted without a ``source`` field, so a
    delete-by-source pre-delete cannot reach them.

    All other variants (output chunks) use the namespaced key, so different
    (source, tab, prop) tuples never collide even when ``chunk_idx`` overlaps.
    """
    if source == SOURCE_TRANSCRIPT and tab_id is None and prop_path is None:
        key = f"{video_id}_{chunk_idx}"
    else:
        key = f"{source}:{video_id}:{tab_id or ''}:{prop_path or ''}:{chunk_idx}"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") & _INT63_MASK


class VectorService:
    """Store and search transcript and output chunks in Qdrant."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self._host = host or settings.QDRANT_HOST
        self._port = port or settings.QDRANT_PORT
        self._client: QdrantClient | None = None

    def _get_client(self) -> QdrantClient:
        """Lazy-initialize Qdrant client."""
        if self._client is None:
            self._client = QdrantClient(host=self._host, port=self._port, timeout=5)
        return self._client

    def _ensure_collection(self) -> bool:
        """Create collection if it doesn't exist. Returns True on success."""
        try:
            client = self._get_client()
            collections = client.get_collections().collections
            if not any(c.name == COLLECTION_NAME for c in collections):
                client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(
                        size=VECTOR_SIZE,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created Qdrant collection: %s", COLLECTION_NAME)
            return True
        except Exception as e:
            logger.warning("Qdrant collection setup failed: %s", e)
            return False

    def store_chunks(
        self,
        video_id: str,
        chunks: list[dict],
        embeddings: list[list[float]],
        language: str = "en",
        original_chunks: list[dict] | None = None,
        source: str = SOURCE_TRANSCRIPT,
        tab_id: str | None = None,
        tab_component: str | None = None,
        prop_paths: list[str] | None = None,
        tab_ids: list[str | None] | None = None,
        tab_components: list[str | None] | None = None,
    ) -> bool:
        """Store chunks with embeddings in a single upsert. Returns True on success.

        Args:
            video_id: YouTube video ID.
            chunks: Text chunks (English for non-English videos, original for English).
            embeddings: Embedding vectors for the chunks.
            language: ISO 639-1 language code.
            original_chunks: Original-language chunks (only for non-English videos).
            source: ``"transcript"`` (default), ``"default_output"`` or ``"visual"``.
            tab_id: Scalar tab identifier broadcast across all chunks. Ignored
                when ``tab_ids`` is provided. ``None`` for transcript.
            tab_component: Scalar component name broadcast across all chunks.
                Ignored when ``tab_components`` is provided. ``None`` for
                transcript.
            prop_paths: Per-chunk prop paths aligned 1:1 with ``chunks``;
                ``None`` for transcript.
            tab_ids: Per-chunk tab identifiers (1:1 with ``chunks``). Use when a
                single batch spans multiple tabs to avoid N-round-trip upserts.
            tab_components: Per-chunk component names (1:1 with ``chunks``).
        """
        if not self._ensure_collection():
            return False

        try:
            points = []
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                prop_path = prop_paths[i] if prop_paths and i < len(prop_paths) else None
                this_tab_id = tab_ids[i] if tab_ids is not None and i < len(tab_ids) else tab_id
                this_tab_component = (
                    tab_components[i]
                    if tab_components is not None and i < len(tab_components)
                    else tab_component
                )
                points.append(
                    PointStruct(
                        id=_point_id(source, video_id, this_tab_id, prop_path, i),
                        vector=emb,
                        payload={
                            "source": source,
                            "video_id": video_id,
                            "user_id": None,  # reserved for future user-content writes
                            "tab_id": this_tab_id,
                            "tab_component": this_tab_component,
                            "prop_path": prop_path,
                            "chunk_index": i,
                            "text": chunk["text"],
                            "text_original": (
                                original_chunks[i]["text"]
                                if original_chunks and i < len(original_chunks)
                                else None
                            ),
                            "language": language,
                            "start_char": chunk.get("start_char", 0),
                            "end_char": chunk.get("end_char", 0),
                            # Seconds into the video; None for output chunks
                            # (no timeline) and for chunkers that could not
                            # map segments (see assign_chunk_timestamps).
                            "timestamp": chunk.get("start_time"),
                            "end_timestamp": chunk.get("end_time"),
                            "schema_version": PAYLOAD_SCHEMA_VERSION,
                        },
                    )
                )
            self._get_client().upsert(
                collection_name=COLLECTION_NAME,
                points=points,
            )
            logger.info(
                "Stored %d chunks (source=%s) for video %s in Qdrant",
                len(points),
                source,
                video_id,
            )
            return True
        except Exception as e:
            logger.warning(
                "Qdrant store_chunks failed for %s (source=%s): %s",
                video_id,
                source,
                e,
            )
            return False

    def search(
        self,
        query_embedding: list[float],
        video_id: str | None = None,
        limit: int = 5,
        sources: list[str] | None = None,
    ) -> list[dict]:
        """Search chunks by similarity. Returns empty list on failure."""
        try:
            conditions = []
            if video_id:
                conditions.append(
                    FieldCondition(key="video_id", match=MatchValue(value=video_id)),
                )
            if sources:
                conditions.append(
                    FieldCondition(key="source", match=MatchAny(any=sources)),
                )
            query_filter = Filter(must=conditions) if conditions else None

            response = self._get_client().query_points(
                collection_name=COLLECTION_NAME,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit,
            )
            return [_result_to_dict(r) for r in response.points]
        except Exception as e:
            logger.warning("Qdrant search failed: %s", e)
            return []

    def search_multi_video(
        self,
        query_embedding: list[float],
        video_ids: list[str],
        limit: int = 10,
        sources: list[str] | None = None,
    ) -> list[dict]:
        """Search across multiple videos (for collection-level queries)."""
        try:
            conditions: list = [
                FieldCondition(key="video_id", match=MatchAny(any=video_ids)),
            ]
            if sources:
                conditions.append(
                    FieldCondition(key="source", match=MatchAny(any=sources)),
                )
            query_filter = Filter(must=conditions)

            response = self._get_client().query_points(
                collection_name=COLLECTION_NAME,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit,
            )
            return [_result_to_dict(r) for r in response.points]
        except Exception as e:
            logger.warning("Qdrant multi-video search failed: %s", e)
            return []

    def delete_video(self, video_id: str) -> bool:
        """Delete all chunks for a video. Returns True on success."""
        try:
            self._get_client().delete(
                collection_name=COLLECTION_NAME,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="video_id",
                            match=MatchValue(value=video_id),
                        ),
                    ],
                ),
            )
            logger.info("Deleted Qdrant chunks for video %s", video_id)
            return True
        except Exception as e:
            logger.warning("Qdrant delete_video failed for %s: %s", video_id, e)
            return False

    def count_points(self, video_id: str, source: str | None = None) -> int:
        """Count stored points matching ``(video_id, source)``.

        Used by the verification harness (and any future health probes) to
        confirm orphan-free reprocesses without reaching into ``_get_client``.
        Returns ``0`` if Qdrant is unreachable.
        """
        try:
            conditions: list = [
                FieldCondition(key="video_id", match=MatchValue(value=video_id)),
            ]
            if source:
                conditions.append(
                    FieldCondition(key="source", match=MatchValue(value=source)),
                )
            flt = Filter(must=conditions)

            client = self._get_client()
            total = 0
            next_offset = None
            while True:
                points, next_offset = client.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=flt,
                    limit=256,
                    offset=next_offset,
                    with_payload=False,
                    with_vectors=False,
                )
                total += len(points)
                if next_offset is None:
                    break
            return total
        except Exception as e:
            logger.warning(
                "Qdrant count_points failed for %s (source=%s): %s",
                video_id,
                source,
                e,
            )
            return 0

    def delete_by_video_and_source(self, video_id: str, source: str) -> bool:
        """Delete chunks for a single (video_id, source). Returns True on success.

        Used as a pre-delete before re-upsert so reprocesses with fewer chunks
        don't leave orphan points behind.
        """
        try:
            self._get_client().delete(
                collection_name=COLLECTION_NAME,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="video_id", match=MatchValue(value=video_id)),
                        FieldCondition(key="source", match=MatchValue(value=source)),
                    ],
                ),
            )
            logger.info(
                "Deleted Qdrant chunks for video %s (source=%s)",
                video_id,
                source,
            )
            return True
        except Exception as e:
            logger.warning(
                "Qdrant delete_by_video_and_source failed for %s (source=%s): %s",
                video_id,
                source,
                e,
            )
            return False


def _result_to_dict(r) -> dict:
    """Map a Qdrant ScoredPoint to the public response shape."""
    payload = r.payload or {}
    return {
        "text": payload.get("text", ""),
        "text_original": payload.get("text_original"),
        "language": payload.get("language", "en"),
        "video_id": payload.get("video_id", ""),
        "score": r.score,
        "chunk_index": payload.get("chunk_index", 0),
        "timestamp": payload.get("timestamp"),
        "end_timestamp": payload.get("end_timestamp"),
        "source": payload.get("source", SOURCE_TRANSCRIPT),
        "tab_id": payload.get("tab_id"),
        "tab_component": payload.get("tab_component"),
        "prop_path": payload.get("prop_path"),
    }
