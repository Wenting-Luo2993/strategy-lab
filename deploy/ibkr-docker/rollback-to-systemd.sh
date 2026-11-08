#!/usr/bin/env bash
# Roll a host back from Docker Compose to the native systemd stack.
#
# Usage:  sudo bash deploy/ibkr-docker/rollback-to-systemd.sh
#
# Stops the Docker stack (frees the IB login and port 8080), optionally copies
# the Docker bot data back so trades/outbox written under Docker are kept, then
# re-enables the systemd units. Secrets rotated under Docker are NOT copied
# back to /etc/strategy-lab/trading-bot-phase2.env; rotate them there too.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE=(docker compose -f "$SCRIPT_DIR/docker-compose.paper.yml")
SRC_DATA="$SCRIPT_DIR/data/trading-bot"
DST_DATA="$REPO_DIR/data"
UNITS=(xvfb-ibkr ibc-gateway x11vnc-ibkr trading-bot-phase2)
TS="$(date +%Y%m%d-%H%M%S)"

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "run with sudo"

step "Stopping Docker stack"
"${COMPOSE[@]}" down

if [[ -d "$SRC_DATA" && -n "$(ls -A "$SRC_DATA")" ]]; then
  read -r -p "Copy Docker bot data back to $DST_DATA (old copy kept as data.pre-rollback-$TS)? [Y/n] " ans
  if [[ ! "$ans" =~ ^[Nn]$ ]]; then
    step "Restoring bot data to $DST_DATA"
    owner="$(stat -c '%U:%G' "$REPO_DIR")"
    [[ -d "$DST_DATA" ]] && mv "$DST_DATA" "$DST_DATA.pre-rollback-$TS"
    mkdir -p "$DST_DATA"
    cp -a "$SRC_DATA/." "$DST_DATA/"
    chown -R "$owner" "$DST_DATA"
  fi
fi

step "Re-enabling systemd units"
systemctl enable --now "${UNITS[@]}"
sleep 5
systemctl is-active "${UNITS[@]}" || true
echo
echo "Rollback done. The bot waits up to 180s for IB Gateway on :4002; follow with:"
echo "  sudo journalctl -u trading-bot-phase2 -f -o cat"
