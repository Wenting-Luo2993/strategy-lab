#!/usr/bin/env bash
# Rotate one KEY=value secret in an env file without echoing the secret.
#
# Usage (on the deployment host):
#   sudo bash rotate-env-key.sh <VAR_NAME> [ENV_FILE]
#
# Examples:
#   sudo bash rotate-env-key.sh RESEARCH_SUPABASE_SERVICE_ROLE_KEY
#   sudo bash rotate-env-key.sh DASHBOARD__SUPABASE_SERVICE_KEY /opt/strategy-lab/deploy/ibkr-docker/secrets/trading-bot.env
#
# Flow: backup -> hidden prompt for new value -> staged copy -> show old/new
# length and last 4 chars -> confirm -> atomic replace -> optional container
# recreate. Declining at the confirm step leaves the original file untouched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAR="${1:-}"
FILE="${2:-$SCRIPT_DIR/secrets/trading-bot.env}"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.paper.yml"
SERVICE="trading-bot"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -n "$VAR" ]] || die "usage: sudo bash $0 <VAR_NAME> [ENV_FILE]"
[[ "$VAR" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "invalid variable name: $VAR"
[[ -f "$FILE" ]] || die "env file not found: $FILE"
[[ -r "$FILE" && -w "$FILE" ]] || die "no read/write access to $FILE (run with sudo)"

count="$(grep -c "^${VAR}=" "$FILE" || true)"
[[ "$count" == "1" ]] || die "expected exactly 1 line '${VAR}=' in $FILE, found ${count}"

summary() {
  # Print "<len> chars, ends ...XXXX" for VAR in the given file; never the value.
  awk -v k="$VAR" 'index($0, k "=") == 1 {
      v = substr($0, length(k) + 2)
      printf "%d chars, ends ...%s\n", length(v), (length(v) >= 4 ? substr(v, length(v) - 3) : "(short)")
    }' "$1"
}

backup="${FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$FILE" "$backup"
echo "Backup written: $backup"

NEWKEY=""
read -rsp "Paste new value for ${VAR} (input hidden): " NEWKEY
echo
NEWKEY="${NEWKEY%$'\r'}"
[[ -n "$NEWKEY" ]] || die "empty value; nothing changed"
[[ "$NEWKEY" =~ ^[^[:space:]]+$ ]] || die "value contains whitespace; nothing changed"
export NEWKEY

tmp="$(mktemp "${FILE}.tmp.XXXXXX")"
trap 'rm -f "$tmp"; unset NEWKEY' EXIT
cp -p "$FILE" "$tmp"
awk -v k="$VAR" 'index($0, k "=") == 1 { print k "=" ENVIRON["NEWKEY"]; next } { print }' "$FILE" > "$tmp"

dupes="$(awk -v k="$VAR" -F= '$1 != k && $1 ~ /KEY$/ {
    v = substr($0, length($1) + 2); if (v != "" && v == ENVIRON["NEWKEY"]) print $1
  }' "$FILE")"
unset NEWKEY

echo
echo "  ${VAR}"
echo "    current: $(summary "$FILE")"
echo "    new:     $(summary "$tmp")"
if [[ -n "$dupes" ]]; then
  echo "  WARNING: new value is identical to: $dupes"
  echo "           (research and live-trading keys are meant to be separate)"
fi
echo

read -rp "Apply this replacement? [y/N] " answer
if [[ ! "$answer" =~ ^[Yy]$ ]]; then
  echo "Aborted. $FILE unchanged (backup kept at $backup)."
  exit 1
fi

mv -f "$tmp" "$FILE"
echo "Replaced ${VAR} in $FILE. Now: $(summary "$FILE")"
echo "Rollback if needed: sudo cp -p '$backup' '$FILE'"

if [[ -f "$COMPOSE_FILE" ]] && command -v docker >/dev/null 2>&1; then
  read -rp "Recreate '${SERVICE}' container now so it loads the new value? [y/N] " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate "$SERVICE"
    echo "Waiting 20s for startup logs..."
    sleep 20
    docker compose -f "$COMPOSE_FILE" logs --since 2m "$SERVICE" \
      | grep -iE 'RemoteDataPublisher|supabase|401|403' || echo "(no matching log lines yet)"
  else
    echo "Remember: docker compose -f $COMPOSE_FILE up -d --force-recreate $SERVICE"
  fi
fi
