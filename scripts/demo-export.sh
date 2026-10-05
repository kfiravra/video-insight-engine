#!/usr/bin/env bash
# Export demo content from the local stack so a fresh server serves it from
# cache: completed videoSummaryCache docs at the current pipeline version, and
# a snapshot of the Qdrant transcript_chunks collection (video chat).
#
# Output: demo-data/<UTC-timestamp>/ (gitignored), or $1 if given
#   videoSummaryCache.archive.gz  mongodump archive of the selected docs
#   transcript_chunks.snapshot    Qdrant collection snapshot
#   urls.txt                      YouTube URL and title, one line per video
#   manifest.json                 pipeline version and counts, checked on import
#
# Not exported: users, userVideos, llm_usage (local accounts are not demo
# accounts), and frames/transcripts, which live in S3. A server with its own
# bucket also needs each video's videos/<id>/ prefix copied from the dev bucket
# (docs/DEPLOY.md, "Preload demo videos").
# After scripts/demo-import.sh, the demo user submits the URLs in urls.txt and
# the API attaches the cached docs without a pipeline run or a YouTube call.
#
# Usage:
#   scripts/demo-export.sh          # export to demo-data/<timestamp>/
#   scripts/demo-export.sh <dir>    # export to a custom directory
#
# Env: MONGO_CONTAINER (default vie-mongodb), QDRANT_URL (http://localhost:6333)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MONGO_CONTAINER="${MONGO_CONTAINER:-vie-mongodb}"
MONGO_DB="video-insight-engine"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
COLLECTION="transcript_chunks"
PIPELINE_VERSION="$(jq -r '.version' "$PROJECT_ROOT/packages/shared/src/config/pipeline-version.json")"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${1:-$PROJECT_ROOT/demo-data/$TIMESTAMP}"

# Only docs stamped with the current version. An older stamp regenerates on
# submission (a paid pipeline run that calls YouTube). Unstamped docs are
# skipped too, which locally drops the e2e smoke fixture.
QUERY="{\"status\":\"completed\",\"pipelineVersion\":\"$PIPELINE_VERSION\"}"

# Run a Mongo tool inside the container. Auth args come from the container's
# own root-user env (set in prod, absent in dev), as in scripts/backup.sh.
mongo_tool() {
  # shellcheck disable=SC2016  # expanded by sh inside the container
  docker exec -i "$MONGO_CONTAINER" sh -c '
    tool="$1"; shift
    exec "$tool" "$@" \
      ${MONGO_INITDB_ROOT_USERNAME:+--username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin}
  ' sh "$@"
}

mkdir -p "$DEST"
echo "=== Demo export → $DEST (pipeline $PIPELINE_VERSION) ==="

echo "--- MongoDB: videoSummaryCache ---"
DOC_COUNT="$(mongo_tool mongosh --quiet "$MONGO_DB" --eval "print(db.videoSummaryCache.countDocuments($QUERY))" < /dev/null)"
if [[ "$DOC_COUNT" -eq 0 ]]; then
  echo "ERROR: no completed $PIPELINE_VERSION docs in videoSummaryCache, nothing to export" >&2
  exit 1
fi
# A video can have several docs (a regenerated version), so URLs are deduplicated.
mongo_tool mongosh --quiet "$MONGO_DB" --eval "
  const seen = new Set();
  db.videoSummaryCache.find($QUERY, { youtubeId: 1, title: 1 }).sort({ youtubeId: 1 }).forEach(function (d) {
    if (seen.has(d.youtubeId)) return;
    seen.add(d.youtubeId);
    print('https://www.youtube.com/watch?v=' + d.youtubeId + '\t' + (d.title || ''));
  });
" < /dev/null > "$DEST/urls.txt"
URL_COUNT="$(wc -l < "$DEST/urls.txt" | tr -d ' ')"
mongo_tool mongodump --db "$MONGO_DB" --collection videoSummaryCache --query "$QUERY" --archive --gzip \
  < /dev/null > "$DEST/videoSummaryCache.archive.gz"
echo "$DOC_COUNT docs for $URL_COUNT videos, archive $(du -h "$DEST/videoSummaryCache.archive.gz" | cut -f1)"

echo "--- Qdrant: $COLLECTION snapshot ---"
# The snapshot holds chunks for every local video, not only the exported ones.
# The extra chunks are harmless because every search filters by video_id.
QDRANT_POINTS="$(curl -sfS "$QDRANT_URL/collections/$COLLECTION" | jq -r '.result.points_count')"
SNAPSHOT_NAME="$(curl -sfS -X POST "$QDRANT_URL/collections/$COLLECTION/snapshots?wait=true" | jq -r '.result.name')"
curl -sfS "$QDRANT_URL/collections/$COLLECTION/snapshots/$SNAPSHOT_NAME" -o "$DEST/$COLLECTION.snapshot"
# Remove the server-side copy so snapshots don't pile up in the container.
curl -sfS -X DELETE "$QDRANT_URL/collections/$COLLECTION/snapshots/$SNAPSHOT_NAME" > /dev/null
echo "$QDRANT_POINTS points, snapshot $(du -h "$DEST/$COLLECTION.snapshot" | cut -f1)"

jq -n \
  --arg timestamp "$TIMESTAMP" \
  --arg pipeline_version "$PIPELINE_VERSION" \
  --argjson docs "$DOC_COUNT" \
  --argjson urls "$URL_COUNT" \
  --arg collection "$COLLECTION" \
  --argjson qdrant_points "$QDRANT_POINTS" \
  '{
    timestamp: $timestamp,
    pipeline_version: $pipeline_version,
    docs: $docs,
    urls: $urls,
    mongo_archive: "videoSummaryCache.archive.gz",
    qdrant_collection: $collection,
    qdrant_snapshot: ($collection + ".snapshot"),
    qdrant_points: $qdrant_points,
    created_by: "scripts/demo-export.sh"
  }' > "$DEST/manifest.json"

echo ""
echo "Export complete: $DEST"
ls -lh "$DEST"
echo ""
echo "Copy it to the server, then run scripts/demo-import.sh there:"
echo "  ssh ec2-user@<host> mkdir -p video-insight-engine/demo-data"
echo "  scp -r \"$DEST\" ec2-user@<host>:video-insight-engine/demo-data/"
