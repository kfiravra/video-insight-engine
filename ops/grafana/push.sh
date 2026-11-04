#!/usr/bin/env bash
#
# push.sh — upsert vie's Grafana Cloud dashboards, alert rules, Telegram contact
# point and notification policy (observability D12, plan R3).
#
# Usage:
#   ops/grafana/push.sh [--dry-run] [--test-alert] [--env-file PATH]
#
#   --dry-run     print every request it would send; sends nothing, needs no token
#   --test-alert  push the DLQ rule with a threshold that always fires (one forced
#                 alert to Telegram); run push.sh again without it to restore
#   --env-file    where to read missing settings from (default: <repo>/.env)
#
# Settings (environment first, then the env file, which is read, never sourced):
#   GRAFANA_CLOUD_URL        https://<stack>.grafana.net
#   GRAFANA_CLOUD_API_TOKEN  service-account token, role Editor
#   ALERT_WEBHOOK_URL        https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>
#   GRAFANA_PROM_DS_UID / GRAFANA_LOKI_DS_UID  optional: the datasource uids the alert
#                            rules query (default: the stack's grafanacloud-*-prom / -logs)
#
# Everything is keyed by a fixed uid, so a re-run updates in place. Note: the
# notification policy is the stack's root policy — this script owns it.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DRY_RUN=false
TEST_ALERT=false
ENV_FILE="$ROOT/.env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --test-alert) TEST_ALERT=true; shift ;;
    --env-file)
      if [[ $# -lt 2 ]]; then echo "--env-file needs a path" >&2; exit 2; fi
      ENV_FILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

setting() {
  # $1: name. The environment wins; else the env file's last `NAME=value` row.
  local name="$1" value="${!1:-}"
  if [[ -z "$value" && -f "$ENV_FILE" ]]; then
    value="$(grep -E "^${name}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true)"
  fi
  printf '%s' "$value"
}

GRAFANA_URL="$(setting GRAFANA_CLOUD_URL)"
GRAFANA_URL="${GRAFANA_URL%/}"
TOKEN="$(setting GRAFANA_CLOUD_API_TOKEN)"
WEBHOOK="$(setting ALERT_WEBHOOK_URL)"
PROM_UID="$(setting GRAFANA_PROM_DS_UID)"
LOKI_UID="$(setting GRAFANA_LOKI_DS_UID)"

if ! $DRY_RUN && [[ -z "$GRAFANA_URL" || -z "$TOKEN" ]]; then
  echo "GRAFANA_CLOUD_URL and GRAFANA_CLOUD_API_TOKEN are required (or use --dry-run)" >&2
  exit 2
fi

# api METHOD PATH [BODY_FILE] → response body on stdout; HTTP status in $API_STATUS.
API_STATUS=""
api() {
  local method="$1" path="$2" body="${3:-}" out
  if $DRY_RUN; then
    if [[ -n "$body" ]]; then
      echo "[dry-run] $method $path ($(wc -c < "$body" | tr -d ' ') bytes)" >&2
    else
      echo "[dry-run] $method $path" >&2
    fi
    API_STATUS="200"
    echo "{}"
    return 0
  fi
  out="$(mktemp)"
  local args=(-sS -o "$out" -w '%{http_code}' -X "$method" "$GRAFANA_URL$path"
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"
    -H "X-Disable-Provenance: true")
  if [[ -n "$body" ]]; then args+=(--data-binary "@$body"); fi
  API_STATUS="$(curl "${args[@]}")"
  cat "$out"
  rm -f "$out"
}

require_ok() {
  # $1: what; succeeds on 2xx, else prints the body and exits.
  if [[ "$API_STATUS" != 2* ]]; then
    echo "$1 failed: HTTP $API_STATUS" >&2
    exit 1
  fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ─── 1. Folder ───────────────────────────────────────────────────────────────
api GET /api/folders/vie > /dev/null
if [[ "$API_STATUS" == "404" ]]; then
  printf '{"uid": "vie", "title": "vie"}' > "$WORK/folder.json"
  api POST /api/folders "$WORK/folder.json" > /dev/null
  require_ok "create folder vie"
fi
echo "folder vie: ok"

# ─── 2. Datasource uids for the alert rules ─────────────────────────────────
if [[ -z "$PROM_UID" || -z "$LOKI_UID" ]]; then
  if $DRY_RUN; then
    PROM_UID="${PROM_UID:-<prometheus-uid>}"
    LOKI_UID="${LOKI_UID:-<loki-uid>}"
  else
    api GET /api/datasources > "$WORK/datasources.json"
    require_ok "list datasources"
    read -r found_prom found_loki < <(python3 - "$WORK/datasources.json" <<'PY'
import json, re, sys
sources = json.load(open(sys.argv[1]))
def pick(kind, suffix):
    typed = [s for s in sources if s.get("type") == kind]
    named = [s for s in typed if re.fullmatch(rf"grafanacloud-.+-{suffix}", s.get("name", ""))]
    chosen = (named or typed or [{}])[0]
    return chosen.get("uid", "")
print(pick("prometheus", "prom") or "-", pick("loki", "logs") or "-")
PY
)
    PROM_UID="${PROM_UID:-$found_prom}"
    LOKI_UID="${LOKI_UID:-$found_loki}"
  fi
fi
if [[ "$PROM_UID" == "-" || "$LOKI_UID" == "-" ]]; then
  echo "no Prometheus or Loki datasource found; set GRAFANA_PROM_DS_UID / GRAFANA_LOKI_DS_UID" >&2
  exit 1
fi
echo "datasources: prometheus=$PROM_UID loki=$LOKI_UID"

# ─── 3. Dashboards (uid-keyed, overwrite) ────────────────────────────────────
for file in "$HERE"/dashboards/*.json; do
  [[ -e "$file" ]] || continue
  python3 - "$file" "$WORK/dashboard.json" <<'PY'
import json, sys
dashboard = json.load(open(sys.argv[1]))
dashboard["id"] = None
json.dump({"dashboard": dashboard, "folderUid": "vie", "overwrite": True}, open(sys.argv[2], "w"))
PY
  api POST /api/dashboards/db "$WORK/dashboard.json" > /dev/null
  require_ok "dashboard $(basename "$file")"
  echo "dashboard $(basename "$file" .json): ok"
done

# ─── 4. Telegram contact point (from ALERT_WEBHOOK_URL) ─────────────────────
if ! python3 - "$HERE/contact-points.json" "$WEBHOOK" "$WORK/contact.json" "$DRY_RUN" <<'PY'
import json, re, sys
from urllib.parse import parse_qs, urlparse
template, webhook, out, dry_run = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "true"
url = urlparse(webhook)
match = re.fullmatch(r"/bot([^/]+)/sendMessage", url.path or "")
chat = (parse_qs(url.query).get("chat_id") or [""])[0]
if url.hostname != "api.telegram.org" or not match or not chat:
    if not dry_run:
        print("ALERT_WEBHOOK_URL is not a Telegram sendMessage URL with chat_id", file=sys.stderr)
        sys.exit(1)
    token, chat = "<bot-token>", "<chat-id>"
else:
    token = match.group(1)
text = open(template).read().replace("__TELEGRAM_BOT_TOKEN__", token).replace("__TELEGRAM_CHAT_ID__", chat)
open(out, "w").write(text)
PY
then
  exit 1
fi
api PUT /api/v1/provisioning/contact-points/vie-telegram "$WORK/contact.json" > /dev/null
if [[ "$API_STATUS" == "404" || "$API_STATUS" == "400" ]]; then
  api POST /api/v1/provisioning/contact-points "$WORK/contact.json" > /dev/null
fi
require_ok "contact point vie-telegram"
echo "contact point vie-telegram: ok"

# ─── 5. Notification policy (root → Telegram) ───────────────────────────────
api PUT /api/v1/provisioning/policies "$HERE/notification-policy.json" > /dev/null
require_ok "notification policy"
echo "notification policy: ok"

# ─── 6. Alert rules (one rule group, replaced whole) ────────────────────────
python3 - "$HERE/alerts/vie.json" "$WORK/rules.json" "$PROM_UID" "$LOKI_UID" "$TEST_ALERT" <<'PY'
import json, sys
source, out, prom, loki, test_alert = sys.argv[1:6]
group = json.loads(open(source).read().replace("__PROM_UID__", prom).replace("__LOKI_UID__", loki))
if test_alert == "true":
    for rule in group["rules"]:
        if rule["uid"] == "vie-dlq-not-empty":
            rule["for"] = "0s"
            rule["title"] += " (TEST: forced by push.sh --test-alert)"
            for step in rule["data"]:
                if step["refId"] == "C":
                    step["model"]["conditions"][0]["evaluator"]["params"] = [-1]
json.dump(group, open(out, "w"))
PY
api PUT /api/v1/provisioning/folder/vie/rule-groups/vie "$WORK/rules.json" > /dev/null
require_ok "alert rule group vie"
if $TEST_ALERT; then
  echo "alert rules: ok (DLQ rule FORCED to fire; run push.sh again without --test-alert to restore)"
else
  echo "alert rules: ok"
fi
