# Phase 4 Dockerized Deployment Runbook

**Status:** Production since 2026-11-08 (cut over from systemd on the Oracle VM)<br>
**Scope:** Run the IBKR paper stack as a Docker Compose project so deploys are identical across hosts, operators and coding agents, and the stack can move to another cloud with only Docker installed.

Day-to-day procedures (fresh host setup, deploys, secret rotation, gateway upgrades, VNC fallback, rollback) live in [`deploy/ibkr-docker/README.md`](../../../deploy/ibkr-docker/README.md). This document records the design and the cutover evidence.

---

## Architecture

Compose project `strategy-lab` (`deploy/ibkr-docker/docker-compose.paper.yml`) with two services on the default bridge network:

| Service | Image | Role |
|---|---|---|
| `ib-gateway` | `ghcr.io/gnzsnz/ib-gateway:10.50.1f`, pinned by digest | IB Gateway + IBC + Xvfb + socat. Logs in with credentials from `secrets/`, restarts daily at 04:00 UTC. |
| `trading-bot` | `strategy-lab-trading-bot:local`, built from `vibe/trading_bot/Dockerfile` | Strategy orchestrator, IB broker adapter, health API, SQLite stores, Discord notifications and the in-process Supabase `RemoteDataPublisher`. |

Key decisions:

- **Maintained gateway image instead of a homemade one.** `gnzsnz/ib-gateway` is widely used, bundles IBC and is configured entirely through environment variables. We pin both the tag and the digest, so an upgrade is a deliberate one-line change (see the README's "Upgrading IB Gateway").
- **Compose network, not a shared network namespace.** Inside the gateway container, socat relays the paper API port `4002` to `4004`. The bot connects to `ib-gateway:4004`. Because socat connects from `127.0.0.1`, Gateway's trusted-IP list needs no Docker CIDRs. No IB port is published to the host.
- **Gateway healthcheck probes `4002`, not `4004`.** socat listens on `4004` immediately, so only `4002` shows that Gateway has logged in and the API is up. The bot `depends_on` the gateway being healthy.
- **Secrets stay out of images and compose.** The username is in `secrets/ib-gateway.env`. The password is a Docker secret file (`secrets/tws_password`, uid 1000, mode 400) read through `TWS_PASSWORD_FILE`. Bot secrets are in `secrets/trading-bot.env`. All are git-ignored.
- **Non-secret runtime settings live in compose `environment`**, mirroring the former systemd unit. Compose `environment` overrides `env_file`, so dashboard keys are kept only in the env file to avoid silent drift.
- **Persistent state is bind-mounted** under `deploy/ibkr-docker/`: `data/trading-bot` → `/app/data`, `logs/trading-bot` → `/app/logs`, `state/tws_settings` → Gateway settings. Container logs use json-file rotation (20 MB × 5).
- **Bot health API** is published on `127.0.0.1:8080` only.
- **`RemoteDataPublisher` stays in-process** in `trading-bot`. A standalone worker would need its own entrypoint and a shared outbox volume. It isn't needed yet.

---

## Scripts

| Script | Purpose |
|---|---|
| `import-ibkr-credentials.sh` | Writes `secrets/ib-gateway.env` and `secrets/tws_password` from `/etc/ibkr/config.ini`, or from hidden prompts with `--prompt`. It prints only lengths. |
| `cutover-systemd-to-docker.sh` | Pre-pulls and builds while systemd is still running, asks for confirmation, then stops and disables the four systemd units. It backs up `/opt/strategy-lab/data`, copies bot data into the Docker volume, installs the bot env, runs `up -d` and waits for the bot to be healthy. |
| `rollback-to-systemd.sh` | Runs `compose down`, optionally copies Docker bot data back, then re-enables the systemd units. |
| `rotate-env-key.sh` | Rotates one key in `secrets/trading-bot.env` with a backup, a last-4-character check and a confirmation, then recreates the bot. |

---

## Cutover Evidence (2026-11-08, Oracle VM)

- VM checked out `main` at `d7f8057`. Stale containers and images from an earlier shadow attempt were removed.
- `cutover-systemd-to-docker.sh` completed. Data backup: `/var/backups/strategy-lab/data-20261108-024555.tgz`.
- Gateway: IBC login completed on the first attempt, then `Read-Only API checkbox is now set to: false`. The container is healthy.
- Bot: `Connected to IB at ib-gateway:4004`, `Reconciled 1 durable execution publish events`, `Dashboard RemoteDataPublisher started`, outbox pruning OK, `Trading loop started`. Weekend sleep until the 2026-11-09 14:25 UTC warm-up. No errors.
- `/api/health` returns 200. `/health/ready` returns 503 by design until the warm-up sets the heartbeat (`core/phases/warmup.py`).
- Memory on the 1 GB VM: 501 MiB used, 303 MiB available, 488 MiB of 4 GiB swap used.

---

## Remaining Validation

- [ ] First trading session under Docker (2026-11-09): warm-up readiness turns green, ORB levels and order notifications fire, and the end-of-day Supabase publish succeeds.
- [ ] Gateway survives its daily 04:00 UTC restart, and the bot reconnects through the Compose DNS name.
- [ ] VM reboot test: both containers return via `restart: unless-stopped` and the bot reconnects.
- [ ] After a few stable sessions, remove legacy artifacts (`deploy/ibkr-docker/runtime/`, old `secrets/config.ini`/`ibc.env`, the systemd units, `/opt/ibkr`, `/opt/IBC`, `/etc/ibkr`) and the secret `.bak` files.

---

## Rollback

```bash
cd /opt/strategy-lab
sudo bash deploy/ibkr-docker/rollback-to-systemd.sh
```

The systemd unit files are kept, only disabled. Secrets rotated while running under Docker are **not** copied back to `/etc/strategy-lab/`; update them there before rolling back.
