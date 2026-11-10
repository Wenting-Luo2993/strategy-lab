#!/usr/bin/env bash
# The ONLY supported entry point for routine production operations.
# Humans and coding agents run this instead of hand-typed docker/systemctl
# commands, so every deploy is preflighted, verified and logged the same way.
# Skill: .claude/skills/deploy-trading-bot/SKILL.md   Runbook: deploy/ibkr-docker/README.md
#
# Run on the deploy host as the deploy user (in the docker group). Never sudo.
#
#   bash deploy/ibkr-docker/deploy.sh status         read-only health report
#   bash deploy/ibkr-docker/deploy.sh check          preflight only; changes nothing
#   bash deploy/ibkr-docker/deploy.sh bot            pull main, rebuild + recreate trading-bot, verify
#   bash deploy/ibkr-docker/deploy.sh gateway        pull main, recreate ib-gateway then trading-bot, verify
#   bash deploy/ibkr-docker/deploy.sh rollback-bot   restore the trading-bot image from before the last deploy
#
# Options:
#   --yes                  skip the confirmation prompt
#   --allow-market-hours   permit changes 09:25-16:05 ET on weekdays (incident fixes only)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE=(docker compose -f "$SCRIPT_DIR/docker-compose.paper.yml")
PROJECT=strategy-lab
BOT=strategy-lab-trading-bot
GW=strategy-lab-ib-gateway
BOT_IMAGE=strategy-lab-trading-bot
DEPLOY_LOG="$SCRIPT_DIR/deploys.log"
LEGACY_UNITS=(trading-bot-phase2 ibc-gateway x11vnc-ibkr xvfb-ibkr)
# An enabled legacy timer restarts ibc-gateway on its schedule (Requires=), so
# it must be neither active nor enabled.
LEGACY_TIMERS=(trading-bot-phase2-start.timer)
MIN_FREE_GB=2

REQUIRED_MARKERS=("Connected to IB at ib-gateway:4004" "Trading loop started")
OPTIONAL_MARKERS=("Dashboard RemoteDataPublisher started")

ASSUME_YES=false
ALLOW_MARKET_HOURS=false
ACTION=""

die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
step() { echo; echo "==> $*"; }
ok() { echo "  ok   $*"; }

usage() { sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

for arg in "$@"; do
  case "$arg" in
    status|check|bot|gateway|rollback-bot) [[ -z "$ACTION" ]] || die "only one action allowed"; ACTION="$arg" ;;
    --yes|-y) ASSUME_YES=true ;;
    --allow-market-hours) ALLOW_MARKET_HOURS=true ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $arg" >&2; usage 2 ;;
  esac
done
[[ -n "$ACTION" ]] || usage 2

cd "$REPO_DIR"

# ---------------------------------------------------------------- helpers

confirm() {
  $ASSUME_YES && return 0
  [[ -t 0 ]] || die "no TTY for confirmation; rerun with --yes once the operator has approved"
  local ans
  read -r -p "$1 [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || die "aborted by operator; nothing changed after this point"
}

record() {  # action result [detail]
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) action=$1 result=$2 sha=$(git rev-parse --short HEAD) user=$(id -un)${3:+ $3}"
  echo "$line" >>"$DEPLOY_LOG" 2>/dev/null || warn "could not append to $DEPLOY_LOG"
}

health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo missing; }

wait_healthy() {  # container timeout_seconds
  local c="$1" deadline=$((SECONDS + $2)) s
  while (( SECONDS < deadline )); do
    s="$(health_of "$c")"
    [[ "$s" == healthy ]] && { ok "$c healthy"; return 0; }
    [[ "$s" == unhealthy || "$s" == exited || "$s" == missing ]] && break
    sleep 5
  done
  echo "  FAIL $c is $(health_of "$c")" >&2
  return 1
}

in_market_hours() {
  local dow hm
  read -r dow hm < <(TZ=America/New_York date '+%u %H%M')
  (( dow <= 5 && 10#$hm >= 925 && 10#$hm <= 1605 ))
}

gateway_drift() {  # prints "drift" if `up` would recreate the running gateway
  docker inspect "$GW" >/dev/null 2>&1 || return 0
  "${COMPOSE[@]}" up -d --dry-run --no-deps --no-build ib-gateway 2>&1 | grep -q 'Recreat' && echo drift || true
}

# ------------------------------------------------------------- preflight

preflight() {  # mode: check | change
  local mode="$1" failures=0
  step "Preflight"

  [[ $EUID -ne 0 ]] || die "do not run as root/sudo; run as the deploy user in the docker group"
  for c in git docker curl; do command -v "$c" >/dev/null || die "$c not installed"; done
  docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin missing"
  docker info >/dev/null 2>&1 || die "cannot talk to Docker; is $(id -un) in the docker group?"
  ok "tools and Docker access"

  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  [[ "$branch" == main ]] || { echo "  FAIL host checkout is on '$branch'; production deploys only from main" >&2; failures=$((failures+1)); }
  if ! git diff --quiet HEAD -- || ! git diff --cached --quiet; then
    echo "  FAIL tracked files modified on the host; changes must land on main via git, not edits on the VM:" >&2
    git status --short --untracked-files=no >&2
    failures=$((failures+1))
  fi
  git fetch -q origin main || die "git fetch failed"
  if ! git merge-base --is-ancestor HEAD origin/main; then
    echo "  FAIL host HEAD has commits not on origin/main" >&2; failures=$((failures+1))
  fi
  (( failures )) || ok "git: main, clean, $(git rev-list --count HEAD..origin/main) commit(s) behind origin/main"

  if command -v systemctl >/dev/null; then
    local u active=()
    for u in "${LEGACY_UNITS[@]}"; do systemctl is-active --quiet "$u" 2>/dev/null && active+=("$u"); done
    for u in "${LEGACY_TIMERS[@]}"; do
      { systemctl is-active --quiet "$u" 2>/dev/null || systemctl is-enabled --quiet "$u" 2>/dev/null; } && active+=("$u")
    done
    if (( ${#active[@]} )); then
      echo "  FAIL legacy systemd units active or timers enabled: ${active[*]} (they compete for the IB login and port 8080)" >&2
      echo "       fix: sudo systemctl disable --now ${LEGACY_TIMERS[*]}; sudo systemctl stop ${LEGACY_UNITS[*]}" >&2
      failures=$((failures+1))
    else
      ok "no legacy systemd units active, no legacy timers enabled"
    fi
  fi

  local foreign
  foreign="$(docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' | awk -v p="$PROJECT" '$2 != p {print $1}')"
  if [[ -n "$foreign" ]]; then
    echo "  FAIL containers outside the '$PROJECT' stack are running: $(echo "$foreign" | tr '\n' ' ')" >&2
    echo "       (e.g. vibe/trading_bot/docker-compose.yml is local-dev only); stop them first" >&2
    failures=$((failures+1))
  else
    ok "no foreign containers"
  fi

  local f missing=()
  for f in secrets/ib-gateway.env secrets/tws_password secrets/trading-bot.env; do
    [[ -s "$SCRIPT_DIR/$f" ]] || missing+=("$f")
  done
  if (( ${#missing[@]} )); then
    echo "  FAIL missing secrets: ${missing[*]} (see README 'Fresh Host Setup')" >&2; failures=$((failures+1))
  else
    ok "secrets present (values not read)"
  fi

  if "${COMPOSE[@]}" config --quiet 2>/dev/null; then ok "compose config valid"; else
    echo "  FAIL docker compose config is invalid" >&2; failures=$((failures+1)); fi

  local free_gb
  free_gb="$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
  if (( free_gb < MIN_FREE_GB )); then
    echo "  FAIL only ${free_gb}G free on /; run: docker image prune -f" >&2; failures=$((failures+1))
  else
    ok "${free_gb}G free on /"
  fi

  if in_market_hours; then
    if [[ "$mode" == change ]] && ! $ALLOW_MARKET_HOURS; then
      echo "  FAIL inside market hours (09:25-16:05 ET); pass --allow-market-hours only for an incident fix" >&2
      failures=$((failures+1))
    else
      warn "inside market hours (09:25-16:05 ET)"
    fi
  else
    ok "outside market hours"
  fi

  [[ "$(gateway_drift)" == drift ]] && warn "running ib-gateway differs from compose; apply with: deploy.sh gateway"

  (( failures == 0 )) || die "preflight failed ($failures problem(s)); nothing was changed"
}

acquire_lock() {
  exec 9>"/tmp/strategy-lab-deploy.lock"
  flock -n 9 || die "another deploy.sh run holds the lock"
}

pull_main() {
  local before after
  before="$(git rev-parse --short HEAD)"
  git pull -q --ff-only origin main
  after="$(git rev-parse --short HEAD)"
  if [[ "$before" == "$after" ]]; then
    echo "  host already at $after"
  else
    echo "  $before -> $after"
    git --no-pager log --oneline "$before..$after" | sed 's/^/    /'
  fi
}

# --------------------------------------------------------------- verify

verify_bot() {
  step "Verifying trading-bot"
  local failed=0 m logs deadline
  wait_healthy "$BOT" 240 || failed=1
  deadline=$((SECONDS + 180))
  while :; do
    logs="$(docker logs "$BOT" 2>&1 || true)"
    local all=1
    for m in "${REQUIRED_MARKERS[@]}"; do grep -qF "$m" <<<"$logs" || all=0; done
    (( all )) || (( SECONDS >= deadline )) && break
    sleep 5
  done
  for m in "${REQUIRED_MARKERS[@]}"; do
    if grep -qF "$m" <<<"$logs"; then ok "log: $m"; else echo "  FAIL log missing: $m" >&2; failed=1; fi
  done
  for m in "${OPTIONAL_MARKERS[@]}"; do
    if grep -qF "$m" <<<"$logs"; then ok "log: $m"; else warn "log missing: $m (expected when dashboard publishing is enabled)"; fi
  done
  local n
  n="$(grep -cE 'Traceback|- (ERROR|CRITICAL) -' <<<"$logs" || true)"
  (( n == 0 )) && ok "no ERROR/Traceback lines" || warn "$n ERROR/Traceback line(s); review: docker logs $BOT 2>&1 | grep -E 'Traceback|ERROR|CRITICAL'"
  if curl -fsS -o /dev/null http://127.0.0.1:8080/api/health; then ok "/api/health 200"; else echo "  FAIL /api/health" >&2; failed=1; fi

  if (( failed )); then
    echo; docker logs --tail 40 "$BOT" 2>&1 | cut -c1-200 >&2
    return 1
  fi
}

# --------------------------------------------------------------- actions

do_status() {
  step "Code"
  git fetch -q origin main 2>/dev/null || warn "git fetch failed"
  echo "  host $(git rev-parse --abbrev-ref HEAD)@$(git rev-parse --short HEAD), origin/main $(git rev-parse --short origin/main), $(git rev-list --count HEAD..origin/main) behind"
  step "Containers"
  "${COMPOSE[@]}" ps --format '  {{.Name}}  {{.Status}}'
  echo "  bot image built $(docker image inspect -f '{{.Created}}' "$BOT_IMAGE:local" 2>/dev/null | cut -c1-19 || echo '?')"
  [[ "$(gateway_drift)" == drift ]] && warn "running ib-gateway differs from compose"
  step "Health"
  echo "  /api/health   $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health || true)"
  echo "  /health/ready $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health/ready || true)  (503 is normal outside warm-up/trading)"
  if command -v systemctl >/dev/null; then
    step "Legacy systemd units and timers (must be inactive and disabled)"
    for u in "${LEGACY_UNITS[@]}" "${LEGACY_TIMERS[@]}"; do echo "  $u $(systemctl is-active "$u" 2>/dev/null || true)/$(systemctl is-enabled "$u" 2>/dev/null || true)"; done
  fi
  step "Recent bot milestones"
  docker logs "$BOT" 2>&1 | grep -E 'Connected to IB|RemoteDataPublisher started|Trading loop started|sleeping until|Warm-up|ORB levels|ERROR|CRITICAL' | tail -8 | cut -c1-180 | sed 's/^/  /' || true
  step "Resources"
  free -h | sed -n '1,3p' | sed 's/^/  /'
  df -h / | tail -1 | sed 's/^/  /'
  step "Recent deploys"
  tail -5 "$DEPLOY_LOG" 2>/dev/null | sed 's/^/  /' || echo "  (none recorded)"
}

do_bot() {
  acquire_lock
  preflight change
  step "Pulling main"
  pull_main
  if [[ "$(gateway_drift)" == drift ]]; then
    warn "ib-gateway config changed in this pull; 'bot' will not touch it. Run 'deploy.sh gateway' to apply."
  fi
  confirm "Rebuild and recreate trading-bot at $(git rev-parse --short HEAD)?"

  step "Building trading-bot image"
  if docker image inspect "$BOT_IMAGE:local" >/dev/null 2>&1; then
    docker tag "$BOT_IMAGE:local" "$BOT_IMAGE:previous"
    ok "saved current image as $BOT_IMAGE:previous"
  fi
  "${COMPOSE[@]}" build trading-bot || { record bot build-failed; die "build failed; production untouched"; }

  step "Recreating trading-bot (gateway session untouched)"
  "${COMPOSE[@]}" up -d --no-deps --no-build --force-recreate trading-bot
  if verify_bot; then
    record bot ok
    echo; echo "Deploy OK at $(git rev-parse --short HEAD)."
  else
    record bot verify-failed
    die "verification failed. Investigate, or restore the previous image: bash $SCRIPT_DIR/deploy.sh rollback-bot"
  fi
}

do_gateway() {
  acquire_lock
  preflight change
  step "Pulling main"
  pull_main
  confirm "Recreate ib-gateway (IB session restarts, ~2 min) and then trading-bot?"

  step "Pulling gateway image"
  "${COMPOSE[@]}" pull ib-gateway
  step "Building trading-bot image"
  docker image inspect "$BOT_IMAGE:local" >/dev/null 2>&1 && docker tag "$BOT_IMAGE:local" "$BOT_IMAGE:previous"
  "${COMPOSE[@]}" build trading-bot || { record gateway build-failed; die "build failed; production untouched"; }

  step "Recreating ib-gateway"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate ib-gateway
  if ! wait_healthy "$GW" 420; then
    docker logs --tail 40 "$GW" 2>&1 | grep -v -i password | cut -c1-200 >&2
    record gateway gateway-unhealthy
    die "ib-gateway did not become healthy (login/2FA?). Check: docker logs $GW"
  fi
  step "Recreating trading-bot against the new gateway"
  "${COMPOSE[@]}" up -d --no-deps --no-build --force-recreate trading-bot
  if verify_bot; then
    record gateway ok
    echo; echo "Gateway + bot OK at $(git rev-parse --short HEAD)."
  else
    record gateway verify-failed
    die "bot verification failed after gateway recreate"
  fi
}

do_rollback_bot() {
  acquire_lock
  docker image inspect "$BOT_IMAGE:previous" >/dev/null 2>&1 || die "no $BOT_IMAGE:previous image to roll back to"
  preflight change
  confirm "Replace the running trading-bot with $BOT_IMAGE:previous (built $(docker image inspect -f '{{.Created}}' "$BOT_IMAGE:previous" | cut -c1-19))?"
  step "Restoring previous image"
  docker tag "$BOT_IMAGE:previous" "$BOT_IMAGE:local"
  "${COMPOSE[@]}" up -d --no-deps --no-build --force-recreate trading-bot
  if verify_bot; then
    record rollback-bot ok "note=host-checkout-still-at-HEAD"
    echo; echo "Rolled back. The host checkout is still at $(git rev-parse --short HEAD): revert the bad change on main,"
    echo "then run 'deploy.sh bot' so the code on disk and the running image match again."
  else
    record rollback-bot verify-failed
    die "rollback verification failed"
  fi
}

case "$ACTION" in
  status) do_status ;;
  check) preflight check; echo; echo "Preflight OK." ;;
  bot) do_bot ;;
  gateway) do_gateway ;;
  rollback-bot) do_rollback_bot ;;
esac
