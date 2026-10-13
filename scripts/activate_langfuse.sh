#!/usr/bin/env bash
# Activate Langfuse observability for vie-summarizer + vie-assistant.
#
# Run once after putting LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY /
# LANGFUSE_BASE_URL into .env. Idempotent — safe to re-run.
#
# Steps performed:
#   1. Pre-flight  — .env has all three vars, non-empty, no surrounding quotes
#   2. Restart     — docker compose restart of the two services that init Langfuse
#   3. Verify env  — keys visible inside the running container
#   4. Verify init — container logs show "Langfuse initialized" not "Langfuse keys not set"
#   5. Dry-run     — host-side dry run of scripts/register_prompts.py (no uploads)
#   6. Commit      — gated `docker compose run --rm vie-langfuse-init` (real upload, same env as runtime containers)
#   7. Hand-off    — print next manual steps (process one video → check trace)
#
# Stops before the golden-dataset eval — that takes ~2h and spends real LLM budget.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
SUMMARIZER_CONTAINER="vie-summarizer"
ASSISTANT_CONTAINER="vie-assistant"
WORKER_CONTAINER="vie-summarizer-worker"

# ─── tiny helpers ───────────────────────────────────────────────────────
red()    { printf '\033[31m%s\033[0m\n' "$*" >&2; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
step()   { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }

die() { red "✗ $*"; exit 1; }

# Extract a value from .env without leaking the value into the shell history.
# Returns empty string if the var is unset.
env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  # Match KEY= at start of line; strip the KEY= prefix; ignore comments.
  awk -F= -v k="$key" '
    /^[[:space:]]*#/ { next }
    $1 == k { sub(/^[^=]*=/, ""); print; exit }
  ' "$ENV_FILE"
}

# ─── Step 1: pre-flight ─────────────────────────────────────────────────
step "1/7  Pre-flight check on $ENV_FILE"
[[ -f "$ENV_FILE" ]] || die "No .env file at $ENV_FILE — create it from .env.example first."

for key in LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_BASE_URL; do
  value="$(env_value "$key")"
  if [[ -z "$value" ]]; then
    die "$key is not set in .env (or value is empty)."
  fi
  # Reject surrounding quotes — Pydantic Settings keeps them as part of the value.
  if [[ "${value:0:1}" == '"' || "${value:0:1}" == "'" ]]; then
    die "$key has surrounding quotes in .env. Remove them — Pydantic keeps quotes as part of the value."
  fi
done
green "✓ All three Langfuse vars present, no quoting issues."

# Show host without echoing secrets.
HOST_VALUE="$(env_value LANGFUSE_BASE_URL)"
yellow "  LANGFUSE_BASE_URL=$HOST_VALUE"

# ─── Step 2: restart services ───────────────────────────────────────────
step "2/7  Restart summarizer + assistant containers"
cd "$REPO_ROOT"
docker compose restart "$SUMMARIZER_CONTAINER" "$WORKER_CONTAINER" "$ASSISTANT_CONTAINER"
green "✓ Restart issued."

# ─── Step 3: verify env propagated ──────────────────────────────────────
step "3/7  Verify env vars reached the container"
for key in LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_BASE_URL; do
  if ! docker exec "$SUMMARIZER_CONTAINER" env | grep -q "^${key}="; then
    die "$key is NOT visible inside $SUMMARIZER_CONTAINER. Check docker-compose env_file wiring, then re-run."
  fi
done
green "✓ Container sees all three Langfuse vars."

# ─── Step 4: verify SDK initialized ─────────────────────────────────────
step "4/7  Wait for 'Langfuse initialized' marker in logs (timeout 20s)"
deadline=$((SECONDS + 20))
while (( SECONDS < deadline )); do
  if docker compose logs --tail=200 "$SUMMARIZER_CONTAINER" 2>/dev/null | grep -q "Langfuse initialized"; then
    green "✓ Langfuse SDK initialized in $SUMMARIZER_CONTAINER."
    break
  fi
  if docker compose logs --tail=200 "$SUMMARIZER_CONTAINER" 2>/dev/null | grep -q "Langfuse keys not set"; then
    die "Container reports keys not set even though env vars are visible. Check container startup logs."
  fi
  sleep 1
done

if (( SECONDS >= deadline )); then
  yellow "⚠ Didn't see 'Langfuse initialized' marker within 20s — service may still be starting. Continuing anyway."
fi

# ─── Step 5: dry-run prompt registry ────────────────────────────────────
step "5/7  Dry-run prompt registry sync (no uploads)"
# Host-side dry run to preview what the init container would upload. The
# script reads from the host's environment; load .env so it can connect.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
python3 scripts/register_prompts.py
green "✓ Dry-run complete."

# ─── Step 6: gated commit ───────────────────────────────────────────────
step "6/7  Commit prompts to Langfuse registry"
yellow "About to upload prompts to: $HOST_VALUE (via vie-langfuse-init container)"
read -r -p "  Proceed with upload? [y/N] " answer
case "${answer,,}" in
  y|yes)
    # Run the same init container the stack uses on `docker compose up`.
    # Guarantees the upload lands in the same project the pipeline reads from.
    docker compose run --rm vie-langfuse-init
    green "✓ Prompts committed to Langfuse."
    ;;
  *)
    yellow "Skipped commit. Re-run this script when ready."
    exit 0
    ;;
esac

# ─── Step 7: hand-off ───────────────────────────────────────────────────
step "7/7  Hand-off — live verification (manual)"
cat <<EOF

Next steps (do these in the Langfuse UI at $HOST_VALUE):

  1. Open the Prompts tab → confirm 45 entries appeared
     (summarizer:plan, summarizer:schema:food, summarizer:enrich_quiz,
      assistant:rag_header).

  2. Process one short test video at http://localhost:5173 → Generate.
     Open the Traces tab → expect ONE trace with nested spans for:
       metadata / transcript / frames / plan / extraction / synthesis /
       enrichment / assembly, plus a 'faithfulness' score on the trace.

  3. On the extraction span, check that metadata shows:
       cacheReadTokens > 0   AND   cacheHit: true
     (cache hits confirm the static schema prefix is being cached as
     designed — see services/summarizer/src/services/llm_telemetry.py)

  4. Once #1–#3 look right, kick the golden-dataset baseline:
       python3 scripts/run_eval.py --output reports/
     This takes ~2 hours and hits live LLM keys. Writes reports/eval-{timestamp}.{csv,md}
     and publishes a dataset run to Langfuse → Datasets → vie-golden-20.

EOF

green "Activation complete."
