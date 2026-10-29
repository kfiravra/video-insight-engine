"""Clean MongoDB for new VIE pipeline workflow.

Run once after deploying the new pipeline. Drops deprecated collections
and removes old fields from video documents.

Usage: python scripts/cleanup_db.py
"""

from pymongo import MongoClient

MONGODB_URI = "mongodb://localhost:27017/video-insight-engine?directConnection=true"


def main() -> None:
    client = MongoClient(MONGODB_URI)
    db = client.get_default_database()

    # Drop deprecated collections
    deprecated_collections = [
        "chapters",
        "content_blocks",
        "output_sections",
        "old_summaries",
        "legacy_outputs",
        "blocks",
    ]
    for col in deprecated_collections:
        if col in db.list_collection_names():
            db.drop_collection(col)
            print(f"Dropped collection: {col}")
        else:
            print(f"Collection not found (skip): {col}")

    # Remove deprecated fields from video documents
    deprecated_fields = {
        "chapters": "",
        "blocks": "",
        "old_output": "",
        "output_type": "",
        "content_blocks": "",
        "sections": "",
    }
    result = db.videos.update_many({}, {"$unset": deprecated_fields})
    print(f"Cleaned {result.modified_count} video documents")

    # Create/verify indexes
    db.videos.create_index("youtubeId", unique=True, sparse=True)
    db.videos.create_index("userId")
    db.videos.create_index("folderId", sparse=True)
    print("Indexes created/verified")

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
