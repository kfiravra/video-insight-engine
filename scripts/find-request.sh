#!/usr/bin/env bash
#
# find-request.sh — pivot one correlation id into every log source we control.
#
# Usage:
#   scripts/find-request.sh <request-id | run-id | session-id> [--since 1h]
#
# Greps the docker logs of the five services that log these ids (vie-api,
# vie-summarizer, vie-summarizer-worker, vie-assistant, vie-admin) and prints
# the matching lines under each service's name. Any of the three ids works:
#   - request id: X-Request-ID, stamped by vie-api on every response and
#     carried through the queue payload and internal calls (`requestId`,
#     `request_id` on log lines);
#   - run id: one pipeline attempt (`runId` / `run_id`, also the `run:` tag of
#     its Langfuse trace);
#   - session id: one chat surface's X-Session-Id (`sessionId` / `session_id`;
#     its Langfuse session is `chat-<id>`).
#
# Exits non-zero only on usage errors — empty output is a valid result
# ("the id never reached any service").

set -euo pipefail

if [[ $# -lt 1 ]]; then
  cat >&2 <<USAGE
usage: $0 <request-id | run-id | session-id> [--since 1h]

Searches the docker logs of every service for one correlation id: a request
id (X-Request-ID), a pipeline run id (runs[].runId) or a chat session id
(X-Session-Id). All three are UUIDs or safe opaque ids of 8-128 characters.

Examples:
  $0 4f81c2d8-9ba0-4e0b-bb15-1a37e5e9d2c2
  $0 4f81c2d8-9ba0-4e0b-bb15-1a37e5e9d2c2 --since 30m
USAGE
  exit 2
fi

id="$1"
shift

since_arg=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since)
      since_arg=(--since "$2")
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

services=(
  vie-api
  vie-summarizer
  vie-summarizer-worker
  vie-assistant
  vie-admin
)

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found — install docker or run on the host that owns the containers" >&2
  exit 1
fi

# Use grep -F because the id is a literal string — no regex chars.
# `|| true` keeps `set -e` happy when a service has no matches.
for svc in "${services[@]}"; do
  if ! docker ps --format '{{.Names}}' | grep -q "^${svc}$"; then
    echo "[${svc}] (container not running — skipping)"
    continue
  fi
  echo "=== ${svc} ==="
  docker logs "${since_arg[@]}" "${svc}" 2>&1 | grep -F "${id}" || echo "  (no matches)"
done

echo
echo "Tip: in Langfuse, a run id is the tag 'run:${id}', a request id the tag 'requestId:${id}',"
echo "     a pipeline session 'pipeline-<videoSummaryId>' and a chat session 'chat-${id}'."
echo "Tip: in Sentry, filter events by tag 'requestId:${id}', 'runId:${id}' or 'sessionId:${id}'."
