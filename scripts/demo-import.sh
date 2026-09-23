#!/usr/bin/env bash
# Import a demo export (scripts/demo-export.sh) into the running stack on the
# server. Run from the repo checkout once the stack is up and healthy.
#
#   MongoDB  The archive is restored into a staging collection and the docs
#            are merged into videoSummaryCache. Docs whose _id
#            already exists are left untouched.
#   Qdrant   A snapshot upload REPLACES the whole transcript_chunks collection,
#            so the import refuses when the server collection already has
#            points, unless --replace-vectors is given.
#
# The manifest's pipeline version must equal this checkout's
# packages/shared/src/config/pipeline-version.json. With any other version the
# imported docs would regenerate on submission instead of serving from cache.
#
# Usage:
#   scripts/demo-import.sh demo-data/<timestamp>
#   scripts/demo-import.sh demo-data/<timestamp> --replace-vectors
#
# Env: MONGO_CONTAINER (default vie-mongodb), QDRANT_HOST (vie-qdrant:6333),
#      DOCKER_NETWORK (vie-network). Qdrant publishes no host port in prod, so
#      its API is called from a throwaway curl container on DOCKER_NETWORK.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MONGO_CONTAINER="${MONGO_CONTAINER:-vie-mongodb}"
MONGO_DB="video-insight-engine"
QDRANT_HOST="${QDRANT_HOST:-vie-qdrant:6333}"
DOCKER_NETWORK="${DOCKER_NETWORK:-vie-network}"
CURL_IMAGE="curlimages/curl:8.10.1"
STAGING="demoImport_videoSummaryCache"

usage() {
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
}

SRC=""
REPLACE_VECTORS=0
for arg in "$@"; do
  case "$arg" in
    --replace-vectors) REPLACE_VECTORS=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
    *) SRC="$arg" ;;
  esac
done
if [[ -z "$SRC" || ! -f "$SRC/manifest.json" ]]; then
  echo "usage: $0 <demo-export-directory> [--replace-vectors]" >&2
  exit 2
fi
SRC="$(cd "$SRC" && pwd)"

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

qdrant_api() {
  docker run --rm --network "$DOCKER_NETWORK" -v "$SRC:/demo:ro" "$CURL_IMAGE" -sS "$@"
}

manifest() {
  jq -r "$1" "$SRC/manifest.json"
}

EXPECTED_VERSION="$(jq -r '.version' "$PROJECT_ROOT/packages/shared/src/config/pipeline-version.json")"
EXPORT_VERSION="$(manifest '.pipeline_version')"
ARCHIVE="$SRC/$(manifest '.mongo_archive')"
COLLECTION="$(manifest '.qdrant_collection')"
SNAPSHOT="$(manifest '.qdrant_snapshot')"
EXPORT_DOCS="$(manifest '.docs')"
EXPORT_POINTS="$(manifest '.qdrant_points')"

echo "=== Demo import from $SRC ==="
if [[ "$EXPORT_VERSION" != "$EXPECTED_VERSION" ]]; then
  echo "ERROR: export is pipeline $EXPORT_VERSION but this checkout is $EXPECTED_VERSION; the docs would regenerate instead of serving from cache" >&2
  exit 1
fi
[[ -f "$ARCHIVE" ]] || { echo "ERROR: missing $ARCHIVE" >&2; exit 1; }
[[ -f "$SRC/$SNAPSHOT" ]] || { echo "ERROR: missing $SRC/$SNAPSHOT" >&2; exit 1; }

# Check Qdrant before touching Mongo, so a refusal leaves nothing half-imported.
EXISTING_POINTS="$(qdrant_api "http://$QDRANT_HOST/collections/$COLLECTION" | jq -r '.result.points_count // 0')"
if [[ "$EXISTING_POINTS" -gt 0 && "$REPLACE_VECTORS" -ne 1 ]]; then
  echo "ERROR: $COLLECTION already has $EXISTING_POINTS points on this server and the snapshot would replace them. Re-run with --replace-vectors to accept." >&2
  exit 1
fi

echo "--- MongoDB: $EXPORT_DOCS docs → videoSummaryCache ---"
mongo_tool mongosh --quiet "$MONGO_DB" --eval "db.getCollection('$STAGING').drop()" < /dev/null > /dev/null
mongo_tool mongorestore --archive --gzip --noIndexRestore \
  --nsInclude "$MONGO_DB.videoSummaryCache" \
  --nsFrom "$MONGO_DB.videoSummaryCache" --nsTo "$MONGO_DB.$STAGING" < "$ARCHIVE"
MERGE_RESULT="$(mongo_tool mongosh --quiet "$MONGO_DB" --eval "
  const staging = db.getCollection('$STAGING');
  const ids = staging.distinct('_id');
  staging.aggregate([
    { \$merge: { into: 'videoSummaryCache', on: '_id', whenMatched: 'keepExisting', whenNotMatched: 'insert' } },
  ]);
  const present = db.videoSummaryCache.countDocuments({ _id: { \$in: ids } });
  staging.drop();
  print(JSON.stringify({ archive: ids.length, present: present }));
" < /dev/null)"
PRESENT="$(jq -r '.present' <<< "$MERGE_RESULT")"

echo "--- Qdrant: $SNAPSHOT → $COLLECTION (replaces the collection) ---"
qdrant_api -f -X POST "http://$QDRANT_HOST/collections/$COLLECTION/snapshots/upload?priority=snapshot&wait=true" \
  -F "snapshot=@/demo/$SNAPSHOT" > /dev/null
IMPORTED_POINTS="$(qdrant_api "http://$QDRANT_HOST/collections/$COLLECTION" | jq -r '.result.points_count // 0')"

echo ""
echo "videoSummaryCache: $PRESENT of $EXPORT_DOCS docs present"
echo "$COLLECTION: $IMPORTED_POINTS points (export had $EXPORT_POINTS)"
if [[ "$PRESENT" -ne "$EXPORT_DOCS" || "$IMPORTED_POINTS" -ne "$EXPORT_POINTS" ]]; then
  echo "ERROR: counts do not match the export manifest" >&2
  exit 1
fi
echo "Import complete. Log in as the demo user and submit the URLs in $SRC/urls.txt"
