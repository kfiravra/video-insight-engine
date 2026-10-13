#!/usr/bin/env python3
"""
Backfill raw transcripts to S3 for existing video summaries.

This migration script:
1. Finds all video summaries without rawTranscriptRef
2. Checks if they have transcript segments
3. Stores the transcript in S3
4. Updates MongoDB with the S3 reference

Usage:
    # Dry run (preview what would be migrated)
    docker exec vie-summarizer python /app/scripts/backfill-transcripts.py --dry-run

    # Run migration
    docker exec vie-summarizer python /app/scripts/backfill-transcripts.py

    # Run with batch size limit
    docker exec vie-summarizer python /app/scripts/backfill-transcripts.py --batch-size 100
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run_migration(dry_run: bool = True, batch_size: int | None = None) -> dict[str, Any]:
    """
    Run the transcript backfill migration.

    This is a synchronous function that uses asyncio.run() for async S3 operations.
    This is intentional for a one-off migration script to avoid mixing paradigms.

    Args:
        dry_run: If True, only preview changes without applying them
        batch_size: Maximum number of documents to process (None = all)

    Returns:
        Migration statistics
    """
    # Import inside function to avoid import errors outside container
    from pymongo import MongoClient

    from src.config import settings
    from src.services.s3_client import S3Client, s3_client
    from src.services.transcript_store import transcript_store

    # Check if S3 is available
    if not S3Client.is_available():
        logger.error("S3 storage is not available (aiobotocore not installed)")
        return {"status": "error", "message": "S3 not available"}

    # Connect to MongoDB
    client = MongoClient(settings.MONGODB_URI)
    db = client.get_default_database()
    collection = db.videoSummaryCache

    # Find documents without rawTranscriptRef that have transcriptSegments
    query = {
        "rawTranscriptRef": {"$exists": False},
        "transcriptSegments": {"$exists": True, "$ne": []},
    }

    total_count = collection.count_documents(query)
    logger.info(f"Found {total_count} documents to migrate")

    if total_count == 0:
        client.close()
        return {
            "status": "success",
            "total": 0,
            "migrated": 0,
            "skipped": 0,
            "errors": 0,
        }

    # Determine batch size
    limit = batch_size if batch_size else total_count
    cursor = collection.find(query).limit(limit)

    stats = {
        "total": total_count,
        "processed": 0,
        "migrated": 0,
        "skipped": 0,
        "errors": 0,
    }

    # Ensure S3 bucket exists
    if not dry_run:
        try:
            asyncio.run(s3_client.ensure_bucket_exists())
        except Exception as e:
            logger.error(f"Failed to ensure S3 bucket exists: {e}")
            client.close()
            return {"status": "error", "message": str(e)}

    for doc in cursor:
        stats["processed"] += 1
        youtube_id = doc.get("youtubeId") or doc.get("youtube_id")
        doc_id = doc["_id"]

        if not youtube_id:
            logger.warning(f"Document {doc_id} has no YouTube ID, skipping")
            stats["skipped"] += 1
            continue

        segments = doc.get("transcriptSegments", [])
        if not segments:
            logger.warning(f"Document {doc_id} has no segments, skipping")
            stats["skipped"] += 1
            continue

        # Source lives in the per-run transcriptMeta block (the top-level
        # transcriptSource field was documented but never written).
        transcript_source = (doc.get("transcriptMeta") or {}).get("source") or "api"
        if transcript_source == "s3":
            # The blob is already in S3; re-storing it would record source="s3"
            # and lose which layer originally produced the transcript.
            logger.info(f"Document {doc_id} was served from S3, skipping")
            stats["skipped"] += 1
            continue

        logger.info(
            f"[{stats['processed']}/{limit}] Processing {youtube_id} "
            f"({len(segments)} segments, source={transcript_source})"
        )

        if dry_run:
            logger.info(f"  DRY RUN: Would store transcript for {youtube_id}")
            stats["migrated"] += 1
            continue

        try:
            # Store transcript in S3 (async call wrapped in asyncio.run)
            s3_key = asyncio.run(
                transcript_store.store(
                    youtube_id=youtube_id,
                    segments=segments,
                    source=transcript_source,
                    language=None,
                )
            )

            # Update MongoDB with S3 reference
            collection.update_one(
                {"_id": doc_id},
                {
                    "$set": {
                        "rawTranscriptRef": s3_key,
                        "migratedAt": datetime.now(timezone.utc),
                    }
                },
            )

            logger.info(f"  Migrated {youtube_id} -> {s3_key}")
            stats["migrated"] += 1

        except Exception as e:
            logger.error(f"  Failed to migrate {youtube_id}: {e}")
            stats["errors"] += 1

    client.close()

    stats["status"] = "success" if stats["errors"] == 0 else "partial"
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill raw transcripts to S3 for existing video summaries"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Maximum number of documents to process",
    )

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Transcript Backfill Migration")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("MODE: Dry run (no changes will be made)")
    else:
        logger.info("MODE: Live migration")

    if args.batch_size:
        logger.info(f"BATCH SIZE: {args.batch_size}")

    logger.info("")

    # Run migration (synchronous function)
    stats = run_migration(dry_run=args.dry_run, batch_size=args.batch_size)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Migration Complete")
    logger.info("=" * 60)
    logger.info(f"Status: {stats.get('status', 'unknown')}")
    logger.info(f"Total documents: {stats.get('total', 0)}")
    logger.info(f"Processed: {stats.get('processed', 0)}")
    logger.info(f"Migrated: {stats.get('migrated', 0)}")
    logger.info(f"Skipped: {stats.get('skipped', 0)}")
    logger.info(f"Errors: {stats.get('errors', 0)}")

    # Exit with error code if there were errors
    if stats.get("errors", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
