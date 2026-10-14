#!/usr/bin/env bash
# Sourced by the run wrappers. load_env_keys <ERE> [file]: export the `.env` lines whose
# key matches the ERE WITHOUT running them as shell code — `.env` values are not
# guaranteed shell-safe (a `$`, backtick or space in a password would be expanded or run).
# One pair of surrounding quotes is stripped, as the `.env` loaders do.
load_env_keys() {
  local pattern="$1" file="${2:-.env}" line key val
  while IFS= read -r line; do
    key="${line%%=*}"
    val="${line#*=}"
    case "$val" in
      \"*\") val="${val:1:${#val}-2}" ;;
      \'*\') val="${val:1:${#val}-2}" ;;
    esac
    export "$key=$val"
  done < <(grep -E "$pattern" "$file")
}
