#!/usr/bin/env bash
# Rebuild dev images one at a time, then force-recreate their containers.
#
#   tools/rebuild-images.sh                 # summarizer (+ worker, same image) and assistant
#   tools/rebuild-images.sh vie-api         # any compose service names
#
# Why the throwaway DOCKER_CONFIG: on this WSL box `docker compose build` can fail with
# "error getting credentials" (the Desktop credential helper) → build with a `{}` config
# plus a symlink to the CLI plugins. Builds run under the heavy lock (never two at once —
# WSL memory). vie-summarizer-worker shares the vie-summarizer image: recreated with it.
# Log: tools/out/rebuild/<UTC stamp>.log
set -euo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$TOOLS/../../../.." && pwd)"
LOCK="${VIE_HEAVY_LOCK:-/tmp/vie-heavy.lock}"
OUT="$TOOLS/out/rebuild"
mkdir -p "$OUT"
LOG="$OUT/$(date -u +%Y%m%dT%H%M%SZ).log"

services=("$@")
[[ ${#services[@]} -eq 0 ]] && services=(vie-summarizer vie-assistant)

recreate=()
for svc in "${services[@]}"; do
  recreate+=("$svc")
  [[ "$svc" == "vie-summarizer" ]] && recreate+=(vie-summarizer-worker)
done

cfg="$(mktemp -d)"
trap 'rm -rf "$cfg"' EXIT
echo '{}' > "$cfg/config.json"
[[ -d "$HOME/.docker/cli-plugins" ]] && ln -s "$HOME/.docker/cli-plugins" "$cfg/cli-plugins"

cd "$ROOT"
{
  for svc in "${services[@]}"; do
    echo "=== build $svc ($(date -u +%H:%M:%SZ))"
    flock "$LOCK" env DOCKER_CONFIG="$cfg" docker compose build "$svc"
  done
  echo "=== recreate ${recreate[*]} ($(date -u +%H:%M:%SZ))"
  docker compose up -d --force-recreate --no-build --no-deps "${recreate[@]}"
  echo "=== done ($(date -u +%H:%M:%SZ))"
} 2>&1 | tee "$LOG"
