#!/usr/bin/env python3
"""Migrate S3 transcript keys from legacy to new path structure.

Legacy: transcripts/{youtube_id}.json
New:    videos/{youtube_id}/transcript.json

This script:
1. Queries MongoDB for documents with legacy rawTranscriptRef
2. Copies S3 objects to new key path
3. Updates MongoDB rawTranscriptRef field
4. Optionally deletes old S3 keys

Usage:
    python scripts/migrate-s3-keys.py --dry-run
    python scripts/migrate-s3-keys.py --batch-size 10
    python scripts/migrate-s3-keys.py --delete-old
"""

import argparse
import asyncio
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate S3 transcript keys")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument("--batch-size", type=int, default=20, help="Batch size for parallel ops")
    parser.add_argument("--delete-old", action="store_true", help="Delete old S3 keys after copy")
    parser.add_argument("--mongodb-uri", default=None, help="MongoDB URI (or MONGODB_URI env var)")
    parser.add_argument("--s3-bucket", default=None, help="S3 bucket name (or S3_BUCKET env var)")
    return parser.parse_args()


def _new_key(youtube_id: str) -> str:
    return f"videos/{youtube_id}/transcript.json"


async def migrate(args: argparse.Namespace) -> None:
    from aiobotocore.session import get_session
    from motor.motor_asyncio import AsyncIOMotorClient

    mongodb_uri = args.mongodb_uri or os.environ.get(
        "MONGODB_URI", "mongodb://localhost:27017/video-insight-engine?directConnection=true"
    )
    bucket = args.s3_bucket or os.environ.get("S3_BUCKET", "vie-transcripts")

    client = AsyncIOMotorClient(mongodb_uri)
    db_name = mongodb_uri.rsplit("/", 1)[-1].split("?")[0]
    db = client[db_name]
    collection = db["videoSummaries"]

    # S3 config from env
    s3_config: dict = {"region_name": os.environ.get("AWS_REGION", "us-east-1")}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        s3_config["endpoint_url"] = endpoint_url
    aws_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if aws_key and aws_secret:
        s3_config["aws_access_key_id"] = aws_key
        s3_config["aws_secret_access_key"] = aws_secret

    session = get_session()

    # Find documents with legacy keys
    cursor = collection.find(
        {"rawTranscriptRef": {"$regex": "^transcripts/"}},
        {"_id": 1, "rawTranscriptRef": 1, "youtubeId": 1},
    )
    # Limit query results to prevent memory exhaustion on large collections.
    # Re-run the script if more documents exist beyond the limit.
    docs = await cursor.to_list(length=1000)
    logger.info("Found %d documents with legacy transcript keys", len(docs))

    if not docs:
        logger.info("Nothing to migrate")
        return

    migrated = 0
    skipped = 0
    failed = 0

    async with session.create_client("s3", **s3_config) as s3:
        for i in range(0, len(docs), args.batch_size):
            batch = docs[i : i + args.batch_size]

            for doc in batch:
                old_key = doc["rawTranscriptRef"]
                youtube_id = doc.get("youtubeId") or old_key.split("/")[-1].replace(".json", "")
                new = _new_key(youtube_id)

                try:
                    # Check if new key already exists
                    try:
                        await s3.head_object(Bucket=bucket, Key=new)
                        logger.info("Skipped %s: already migrated", youtube_id)
                        skipped += 1
                        continue
                    except Exception as exc:
                        # Only proceed if key doesn't exist (NoSuchKey / 404).
                        # Re-raise unexpected errors (auth failures, throttling).
                        error_code = getattr(getattr(exc, "response", None), "Error", {}).get(
                            "Code", ""
                        )
                        if error_code not in ("404", "NoSuchKey"):
                            raise
                        # Key doesn't exist, proceed with migration

                    if args.dry_run:
                        logger.info("[DRY RUN] Would migrate %s → %s", old_key, new)
                        migrated += 1
                        continue

                    # Copy object
                    await s3.copy_object(
                        Bucket=bucket,
                        CopySource={"Bucket": bucket, "Key": old_key},
                        Key=new,
                    )

                    # Update MongoDB
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"rawTranscriptRef": new}},
                    )

                    # Optionally delete old key
                    if args.delete_old:
                        await s3.delete_object(Bucket=bucket, Key=old_key)
                        logger.info("Migrated + deleted %s → %s", old_key, new)
                    else:
                        logger.info("Migrated %s → %s", old_key, new)

                    migrated += 1

                except Exception as e:
                    logger.error("Failed to migrate %s: %s", old_key, e)
                    failed += 1

    client.close()

    prefix = "[DRY RUN] " if args.dry_run else ""
    logger.info(
        "%sMigration complete. Migrated: %d, Skipped: %d, Failed: %d",
        prefix,
        migrated,
        skipped,
        failed,
    )


def main() -> None:
    args = parse_args()
    asyncio.run(migrate(args))


if __name__ == "__main__":
    main()
