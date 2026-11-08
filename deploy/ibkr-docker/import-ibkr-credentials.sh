#!/usr/bin/env bash
# Import IBKR login credentials into the Docker secrets without echoing them.
#
# Usage (on the deployment host):
#   sudo bash deploy/ibkr-docker/import-ibkr-credentials.sh            # read from /etc/ibkr/config.ini (legacy systemd host)
#   sudo bash deploy/ibkr-docker/import-ibkr-credentials.sh --prompt   # type them at hidden prompts (fresh host)
#
# Writes:
#   secrets/ib-gateway.env  TWS_USERID=<user>     mode 600, owner = invoking user (read by docker compose)
#   secrets/tws_password    <password>, no EOL    mode 400, owner uid 1000 (read inside the gateway container)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS="$SCRIPT_DIR/secrets"
SRC="${IBC_CONFIG:-/etc/ibkr/config.ini}"
OWNER="${SUDO_USER:-root}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"

if [[ "${1:-}" == "--prompt" ]]; then
  read -r -s -p "IBKR username: " user; echo
  read -r -s -p "IBKR password: " pass; echo
  read -r -s -p "Confirm password: " pass2; echo
  [[ "$pass" == "$pass2" ]] || die "passwords do not match; nothing written"
  unset pass2
  source_desc="hidden prompt"
else
  [[ -r "$SRC" ]] || die "cannot read $SRC (use --prompt on a fresh host)"
  user="$(sed -n 's/^IbLoginId=//p' "$SRC" | tail -n 1 | tr -d '\r')"
  pass="$(sed -n 's/^IbPassword=//p' "$SRC" | tail -n 1 | tr -d '\r')"
  source_desc="$SRC"
fi

[[ -n "$user" && -n "$pass" ]] || die "empty username or password from $source_desc; nothing written"
[[ "$user" =~ ^[A-Za-z0-9._@-]+$ ]] || die "username contains unexpected characters; nothing written"

mkdir -p "$SECRETS"
umask 077

tmp="$(mktemp "$SECRETS/.ib-gateway.env.XXXXXX")"
printf 'TWS_USERID=%s\n' "$user" >"$tmp"
chown "$OWNER": "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$SECRETS/ib-gateway.env"

tmp="$(mktemp "$SECRETS/.tws_password.XXXXXX")"
printf '%s' "$pass" >"$tmp"
chown 1000:1000 "$tmp"
chmod 400 "$tmp"
mv -f "$tmp" "$SECRETS/tws_password"

echo "Imported IBKR credentials from $source_desc:"
echo "  secrets/ib-gateway.env  (username, ${#user} chars)"
echo "  secrets/tws_password    (password, ${#pass} chars)"
unset user pass
