---
name: deploy-trading-bot
description: Deploy, restart, health-check, stop, migrate or roll back the IBKR paper-trading Docker stack on the Oracle VM or a Windows/WSL2 PC; upgrade IB Gateway or rotate a production secret. Use for production operations, host cutovers, "deploy to this PC", "stop the VM bot", "restart the bot", "is the bot healthy", "upgrade IB Gateway" or "roll back".
---

# Deploy the trading bot (Oracle VM, Docker Compose)

Production is **one active Docker Compose project, `strategy-lab`**, originally on the Oracle VM at
`/opt/strategy-lab`, defined by `deploy/ibkr-docker/docker-compose.paper.yml`:

| Container | Role |
|---|---|
| `strategy-lab-ib-gateway` | Pinned `ghcr.io/gnzsnz/ib-gateway` (IB Gateway + IBC, paper). No published ports. |
| `strategy-lab-trading-bot` | Bot built from `vibe/trading_bot/Dockerfile`; API on `127.0.0.1:8080`. |

Every routine operation goes through **`deploy/ibkr-docker/deploy.sh`**. It preflights,
pulls `main`, rebuilds, recreates, verifies log markers and `/api/health`, and appends to
`deploy/ibkr-docker/deploys.log` on the VM. Full runbook: `deploy/ibkr-docker/README.md`.
Design history: `docs/trading-bot-mvp/ibkr-integration/phase-4-dockerized-deployment-runbook.md`.

For connection failures or missing IB data, use
[`gateway-troubleshooting.md`](../../../docs/trading-bot-mvp/ibkr-integration/gateway-troubleshooting.md)
before restarting or deploying. Preserve timestamped gateway and bot evidence,
distinguish liveness from broker/data readiness, and update its incident register.
Do not infer that a recurring failure has the same cause as a previous incident.

## Hard rules

1. **Code reaches production only through `main`.** Commit and push to `main` first; the VM
   only ever runs `git pull --ff-only origin main` (done by `deploy.sh`). Never edit tracked
   files on the VM and never deploy a feature branch.
2. **Use `deploy.sh`, not hand-typed commands.** Do not use `systemctl` (legacy units
   `trading-bot-phase2`/`ibc-gateway` and the `trading-bot-phase2-start.timer` are
   rollback-only and must stay disabled; the timer `Requires=ibc-gateway` and starts a
   competing IB login at 13:20 UTC on weekdays),
   `docker compose restart|down|up`, `docker run`, or `vibe/trading_bot/docker-compose.yml`
   (local development only).
3. **Never print, read, `cat`, `grep` or echo secrets.** Files under
   `deploy/ibkr-docker/secrets/` are entered by the user through the helper scripts' hidden
   prompts. Show at most a length / last-4 fingerprint.
4. **Stay outside market hours** (Mon–Fri 09:25–16:05 ET). `deploy.sh` refuses changes then
   unless `--allow-market-hours` is passed; use that only for an incident fix the user approved.
5. **Ask the user before any production-changing command** (`bot`, `gateway`,
   `rollback-bot`, `stop`, `recover-dashboard-metrics`, the secret helpers).
   Plain `status` and `check` are read-only and always safe.
   `status --watch-connections --record-connections` additionally writes rotating
   metadata logs; ask before enabling recording. It is a foreground watcher, not
   an installed service, and must not be reported as running unless verified.
6. **Agents have no SSH key to the VM.** Open (or reuse) a terminal canvas, ask the user to
   SSH in as the deploy user (`ubuntu`) and `cd /opt/strategy-lab`, then send commands into
   that terminal. Run `deploy.sh` **without** `sudo`; only the two credential helpers use sudo.
   Let the user type passwords and secrets themselves.

## Task → command

Run from `/opt/strategy-lab` on the VM. `D=deploy/ibkr-docker`.

| Task | Command(s) |
|---|---|
| Is production healthy? | `bash $D/deploy.sh status` |
| Watch Gateway API peers without logging into IB | `bash $D/deploy.sh status --watch-connections` (foreground; Ctrl+C stops) |
| Dry-run preflight | `bash $D/deploy.sh check` |
| Ship a code change (bot code, rulesets, config, Dockerfile) | merge to `main` → `bash $D/deploy.sh bot` |
| Bump the IB Gateway image / change gateway settings | edit tag+digest in compose → `main` → `bash $D/deploy.sh gateway` |
| Change the IBKR username/password | `sudo bash $D/import-ibkr-credentials.sh --prompt` → `bash $D/deploy.sh gateway` |
| Rotate a bot secret (Supabase, Discord, …) | `sudo bash $D/rotate-env-key.sh <VAR_NAME>` → `bash $D/deploy.sh bot` |
| Bad bot deploy | `bash $D/deploy.sh rollback-bot`, then revert on `main` and `deploy.sh bot` |
| Pause the stack without deleting the host or data | With approval: `bash $D/deploy.sh stop` |
| Retry dashboard metric identity conflicts | After approved schema repair, backup and bot deployment: `bash $D/deploy.sh recover-dashboard-metrics` |
| Docker stack itself broken | `sudo bash $D/rollback-to-systemd.sh` (last resort; tell the user) |
| Fresh host | README "Fresh Host Setup", finishing with `deploy.sh check` then `deploy.sh gateway` |

`deploy.sh` actions: `status`, `check`, `bot`, `gateway`, `rollback-bot`,
`recover-dashboard-metrics`, `stop`.
Options: `--yes` (skip the prompt; required without a TTY), `--allow-market-hours`.
`status --watch-connections` is read-only and repeats a metadata-only socket,
JVM and resource-counter audit every 60 seconds. It creates no additional service
or IB session; errors are explicit. Peer allowlisting does not verify client IDs
or broker-side sessions and cannot establish the cause of `10197`. It never
disconnects a peer or changes orders. Do not leave an unattended watcher running
without an operator request.

`recover-dashboard-metrics` is a production-changing maintenance action, not a
restart. First verify the operator applied
`docs/trading-bot-mvp/dashboards/supabase-metric-identity.sql`, all publishers use
`metric_id`, and a consistent outbox backup exists. Ask for explicit approval.
The harness preflights, previews eligible latest dead letters and appends
immutable successors under a checked plan. It never deletes failure history or
changes orders. "Metric recovery queued" does not mean published: verify the
successors and remote readback after the existing publisher drains them.

## Workflow

### PC host cutover

Use the README's Windows/WSL2 procedure, the same production Compose file and
a separate clean `main` checkout on Linux storage as a non-root deploy user.
Do not deploy this research feature worktree or use the local-development
Compose file. Only one host may own the paper login at a time.

Before an approved `deploy.sh stop`, obtain a fresh operator/broker review of
positions and working orders. Stopping a process is not flattening exposure or
cancelling broker-held protection. Stop the bot first, then the Gateway; the
harness verifies both are stopped and preserves containers, volumes, secrets
and the Oracle instance. With `unless-stopped`, an explicitly stopped container
stays stopped across daemon restarts; a later approved deploy can resume it.

Transfer all durable bot data consistently while the source is stopped,
including SQLite WAL/SHM files. Private credentials are copied or entered by the
operator, never read by the agent or placed in chat/Git. Do not start a fresh
execution journal against inherited orders. Verify source stopped before PC
`gateway`, and PC stopped before any VM resumption. Leave the selected five
symbols and risk caps unchanged for the shakedown; ten-symbol expansion requires
reviewed symbols, capital limits and a separate configuration identity.

1. **Land the change on `main`.** If `deploy/ibkr-docker/**` or the Dockerfile changed,
   confirm the `deploy-guardrails` workflow is green.
2. **Check market hours** (`TZ=America/New_York date`). If inside, stop and ask.
3. **Read-only first:** in the user's terminal run `deploy.sh status`, then `deploy.sh check`.
   Fix any preflight failure before continuing; never work around one.
4. **Ask the user** to approve the specific action (`bot` / `gateway` / `rollback-bot` / `stop`).
5. **Run it**; answer its confirmation prompt only with the user's approval.
6. **Read the result.** `deploy.sh` exits non-zero on any failed check. Do not declare
   success unless it printed `Deploy OK`, `Gateway + bot OK`, `Rolled back` or
   (for stop only) `Stack stopped`. A stopped stack is not healthy or deployed.
7. **Report** using the template below.

### What "healthy" means

- Both containers `healthy`.
- Bot log contains `Connected to IB at ib-gateway:4004` and `Trading loop started`
  (plus `Dashboard RemoteDataPublisher started` when dashboard publishing is enabled).
- `/api/health` = 200. `/health/ready` = 503 outside warm-up/trading is **normal**.
- Gateway log shows the IBC login succeeded and `Read-Only API checkbox is now set to: false`.

### Failure handling

| Symptom | Action |
|---|---|
| Preflight: legacy systemd unit active or legacy timer enabled | Ask the user; `sudo systemctl disable --now trading-bot-phase2-start.timer` and `sudo systemctl stop <unit>` only with approval |
| Bot log: IB requests time out, gateway log shows "Re-login is required" | Follow the troubleshooting guide and preserve the timeline first. Check for an active legacy gateway/enabled timer, but do not assume it explains an earlier re-login. Choose recovery only after classifying the failure. |
| Preflight: foreign container running | Identify it with `docker ps`; stop it only with approval |
| Preflight: host has local changes | Show `git status`; never `git reset`/`checkout` on the VM without the user |
| `ib-gateway` unhealthy | `docker logs --tail 80 strategy-lab-ib-gateway` (never show password lines); usually 2FA, credentials or a competing IBKR session |
| Bot verification failed | Show `docker logs --tail 80 strategy-lab-trading-bot`; offer `rollback-bot` |
| Low disk | `docker image prune -f` (with approval) |
| Memory pressure | VM has 1 GB RAM + 4 GB swap; report `free -h`; do not add services |

## Report template

```
Deploy: <action> at <short sha> (<n> new commits: …)
Result: OK | FAILED at <step>
Health: gateway <status>, bot <status>, /api/health <code>
Markers: IB connected ✓, trading loop ✓, publisher ✓/–
Next scheduled event: warm-up <date> 09:25 ET
Follow-ups: …
```
