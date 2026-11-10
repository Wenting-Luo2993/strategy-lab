#!/usr/bin/env bash
# One-time cutover of a host from the native systemd stack to Docker Compose.
#
# Usage:  sudo bash deploy/ibkr-docker/cutover-systemd-to-docker.sh
# Undo:   sudo bash deploy/ibkr-docker/rollback-to-systemd.sh
#
# Run import-ibkr-credentials.sh first. Images are pulled/built before anything
# is stopped, so downtime is only the stop -> copy -> gateway login window.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE=(docker compose -f "$SCRIPT_DIR/docker-compose.paper.yml")
SYSTEMD_ENV=/etc/strategy-lab/trading-bot-phase2.env
SRC_DATA="$REPO_DIR/data"
DST_DATA="$SCRIPT_DIR/data/trading-bot"
UNITS=(trading-bot-phase2 ibc-gateway x11vnc-ibkr xvfb-ibkr)
# The weekday 13:20 UTC timer Requires=ibc-gateway, so leaving it enabled
# restarts the legacy gateway and steals the IB login from Docker.
TIMERS=(trading-bot-phase2-start.timer)
BACKUP_DIR=/var/backups/strategy-lab
OWNER="${SUDO_USER:-root}"
TS="$(date +%Y%m%d-%H%M%S)"

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -s "$SCRIPT_DIR/secrets/ib-gateway.env" && -s "$SCRIPT_DIR/secrets/tws_password" ]] \
  || die "IBKR credentials missing; run: sudo bash $SCRIPT_DIR/import-ibkr-credentials.sh"
[[ -f "$SYSTEMD_ENV" ]] || die "$SYSTEMD_ENV not found"
[[ -d "$SRC_DATA" ]] || die "$SRC_DATA not found"

# Compose needs the env file to exist to validate/build; the real copy happens after stop.
[[ -f "$SCRIPT_DIR/secrets/trading-bot.env" ]] || install -m 600 -o "$OWNER" -g "$(id -gn "$OWNER")" "$SYSTEMD_ENV" "$SCRIPT_DIR/secrets/trading-bot.env"

step "Pulling gateway image and building bot image (systemd stack still running)"
"${COMPOSE[@]}" pull ib-gateway
"${COMPOSE[@]}" build trading-bot

read -r -p "Stop the systemd stack and start Docker now? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted; systemd stack untouched."; exit 1; }

step "Stopping and disabling systemd units and timers (unit files kept for rollback)"
systemctl disable --now "${TIMERS[@]}" 2>/dev/null || true
systemctl stop trading-bot-phase2
systemctl stop ibc-gateway x11vnc-ibkr xvfb-ibkr
systemctl disable "${UNITS[@]}"

step "Backing up $SRC_DATA to $BACKUP_DIR/data-$TS.tgz"
mkdir -p "$BACKUP_DIR"
tar -C "$REPO_DIR" -czf "$BACKUP_DIR/data-$TS.tgz" data

step "Copying bot data to $DST_DATA"
if [[ -d "$DST_DATA" && -n "$(ls -A "$DST_DATA")" ]]; then
  mv "$DST_DATA" "$DST_DATA.pre-cutover-$TS"
fi
mkdir -p "$DST_DATA" "$SCRIPT_DIR/logs/trading-bot" "$SCRIPT_DIR/state/tws_settings"
cp -a "$SRC_DATA/." "$DST_DATA/"
chown -R 1000:1000 "$DST_DATA" "$SCRIPT_DIR/logs/trading-bot" "$SCRIPT_DIR/state/tws_settings"

step "Installing bot env from $SYSTEMD_ENV"
install -m 600 -o "$OWNER" -g "$(id -gn "$OWNER")" "$SYSTEMD_ENV" "$SCRIPT_DIR/secrets/trading-bot.env"

step "Starting Docker stack (waits for IB Gateway login)"
"${COMPOSE[@]}" up -d \
  || die "compose up failed. Check: ${COMPOSE[*]} logs ib-gateway. Roll back: sudo bash $SCRIPT_DIR/rollback-to-systemd.sh"

step "Waiting for trading-bot health (up to 5 min)"
status=missing
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' strategy-lab-trading-bot 2>/dev/null || echo missing)"
  [[ "$status" == "healthy" ]] && break
  sleep 5
done
"${COMPOSE[@]}" ps
[[ "$status" == "healthy" ]] \
  || die "trading-bot is $status. Check: ${COMPOSE[*]} logs trading-bot. Roll back: sudo bash $SCRIPT_DIR/rollback-to-systemd.sh"

echo
echo "Cutover complete. Data backup: $BACKUP_DIR/data-$TS.tgz"
echo "$SRC_DATA is now stale; the live copy is $DST_DATA."
