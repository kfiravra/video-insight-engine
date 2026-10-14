#!/bin/bash
# Phase-3 prod benchmark: admin, cold, one at a time, only when no visitor run is active.
# Args: <ytid>:<label> ... ; stops at the first refusal (rc 4 = the admin's daily cost cap).
# Needs in the repo .env (never printed): PROD_ADMIN_EMAIL / PROD_ADMIN_PASSWORD, VIE_PROD_API_URL
# (prod API base URL), VIE_PROD_SSH (ssh destination, user@host), VIE_PROD_SSH_KEY (identity file).
# Outputs: tools/out/prod/prod-<ytid>-<label>.{json,err} + prodruns.jsonl (appended by prodrun.py).
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$TOOLS/../../../.." && pwd)"
P="$TOOLS/out/prod"
mkdir -p "$P"
cd "$ROOT"
. "$TOOLS/_env.sh"
load_env_keys '^(PROD_ADMIN_(EMAIL|PASSWORD)|VIE_PROD_API_URL|VIE_PROD_SSH|VIE_PROD_SSH_KEY)='
[ -n "$PROD_ADMIN_EMAIL" ] && [ -n "$PROD_ADMIN_PASSWORD" ] || { echo "PROD_ADMIN_* missing in .env"; exit 2; }
[ -n "$VIE_PROD_API_URL" ] && [ -n "$VIE_PROD_SSH" ] && [ -n "$VIE_PROD_SSH_KEY" ] || { echo "VIE_PROD_API_URL / VIE_PROD_SSH / VIE_PROD_SSH_KEY missing in .env"; exit 2; }
export EVAL_USER_EMAIL="$PROD_ADMIN_EMAIL" EVAL_USER_PASSWORD="$PROD_ADMIN_PASSWORD"; unset EVAL_ALLOW_REGISTER
busy() { timeout 40 ssh -o ConnectTimeout=10 -o BatchMode=yes -i "$VIE_PROD_SSH_KEY" "$VIE_PROD_SSH" 'a=$(docker exec vie-mongodb sh -c '"'"'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin video-insight-engine --eval "print(db.videoSummaryCache.countDocuments({status:{\$in:[\"pending\",\"processing\"]}, updatedAt:{\$gte:new Date(Date.now()-30*60*1000)}}))"'"'"' | tail -1); l=$(docker exec vie-redis sh -c '"'"'redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --no-auth-warning --scan --pattern "vie:pipeline:lock:*" | wc -l'"'"'); echo "$a $l"'; }
for arg in "$@"; do
  v=${arg%%:*}; label=${arg#*:}
  for i in $(seq 1 60); do read a l < <(busy); echo "$(date +%T) $v/$label pre-check: active rows=$a locks=$l"; [ "$a" = 0 ] && [ "$l" = 0 ] && break; sleep 30; done
  [ "$a" = 0 ] && [ "$l" = 0 ] || { echo "$v/$label: a visitor run stayed active 30 min — skipped"; continue; }
  echo "### $v $label $(date +%T)"
  services/summarizer/.venv/bin/python "$TOOLS/prodrun.py" "$v" "$label" > "$P/prod-$v-$label.json" 2> "$P/prod-$v-$label.err"; rc=$?
  echo "rc=$rc $(date +%T)"; cat "$P/prod-$v-$label.json" | head -40
  [ $rc = 4 ] && { echo "REFUSED (cost cap?) — stopping the benchmark"; break; }
done
echo "BENCHMARK END $(date +%T)"
