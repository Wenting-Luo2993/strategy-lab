# IBKR Paper Trading Stack (Docker Compose)

This is the production deployment for the paper-trading bot. It runs two containers on one host:

| Service | Image | Role |
|---------|-------|------|
| `ib-gateway` | `ghcr.io/gnzsnz/ib-gateway:10.50.1f` (pinned by digest) | IB Gateway + IBC + Xvfb, auto-login, paper API |
| `trading-bot` | built from `vibe/trading_bot/Dockerfile` | Strategy, broker client, in-process Supabase `RemoteDataPublisher`, health API |

The bot connects to `ib-gateway:4004` over the Compose network. `4004` is the image's socat relay to the in-container paper API on `4002`. No IB port is published to the host. The bot health API is published on `127.0.0.1:8080` only.

Every host and every coding agent deploys the same way: merge to `main`, then run [`deploy.sh`](deploy.sh) on the host as the deploy user (no sudo). It preflights, pulls `main`, rebuilds, recreates, verifies and logs to `deploys.log`. Do not hand-type `docker compose`/`systemctl` commands for routine operations, and do not hand-install IB Gateway, IBC or a Python venv on a host. Coding agents follow the [`deploy-trading-bot` skill](../../.claude/skills/deploy-trading-bot/SKILL.md).

| Task | Command (from `/opt/strategy-lab`) |
|------|-------------------------------------|
| Health report (read-only) | `bash deploy/ibkr-docker/deploy.sh status` |
| Preflight only | `bash deploy/ibkr-docker/deploy.sh check` |
| Ship a code change | `bash deploy/ibkr-docker/deploy.sh bot` |
| Gateway image/settings or IBKR password change | `bash deploy/ibkr-docker/deploy.sh gateway` |
| Undo the last bot deploy | `bash deploy/ibkr-docker/deploy.sh rollback-bot` |

`deploy.sh` refuses to change anything Mon–Fri 09:25–16:05 ET unless `--allow-market-hours` is given, and prompts before changing production unless `--yes` is given.

## Layout

```text
deploy/ibkr-docker/
├── docker-compose.paper.yml
├── deploy.sh                      # status | check | bot | gateway | rollback-bot
├── import-ibkr-credentials.sh      # IBKR login -> secrets/, never echoes values
├── cutover-systemd-to-docker.sh   # one-time migration from the legacy systemd stack
├── rollback-to-systemd.sh         # undo the cutover
├── rotate-env-key.sh              # swap one secret in secrets/trading-bot.env
└── secrets/
    ├── ib-gateway.env.example
    └── trading-bot.env.example
```

These paths are git-ignored and exist only on the host:

| Path | Contents | Owner / mode |
|------|----------|--------------|
| `secrets/ib-gateway.env` | `TWS_USERID` | deploy user, `600` |
| `secrets/tws_password` | IBKR password, no trailing newline | uid `1000`, `400` |
| `secrets/trading-bot.env` | bot secrets and dashboard settings | deploy user, `600` |
| `data/trading-bot/` | bot SQLite DBs (`/app/data`) | uid `1000` |
| `logs/trading-bot/` | bot file logs (`/app/logs`) | uid `1000` |
| `state/tws_settings/` | IB Gateway settings (`jts.ini`) | uid `1000` |
| `deploys.log` | one line per `deploy.sh` change: time, action, result, sha, user | deploy user |

Both images run as uid `1000`. That is why the container-read paths are chowned to `1000`, whatever that uid is called on the host.

## Fresh Host Setup

Prerequisites are Docker Engine with the Compose v2 plugin, a checkout at `/opt/strategy-lab`, and a deploy user in the `docker` group. A 1 GB VM needs swap: IB Gateway's JVM uses up to 768 MB of heap.

```bash
cd /opt/strategy-lab/deploy/ibkr-docker
sudo bash import-ibkr-credentials.sh --prompt          # hidden prompts
cp secrets/trading-bot.env.example secrets/trading-bot.env && chmod 600 secrets/trading-bot.env
sudo bash rotate-env-key.sh DASHBOARD__SUPABASE_SERVICE_KEY   # repeat for each secret
sudo mkdir -p data/trading-bot logs/trading-bot state/tws_settings
sudo chown -R 1000:1000 data/trading-bot logs/trading-bot state/tws_settings
bash deploy.sh check
bash deploy.sh gateway        # first start: brings up ib-gateway, then the bot
```

## Health Checks

```bash
cd /opt/strategy-lab
bash deploy/ibkr-docker/deploy.sh status
```

`status` is read-only. It shows the host sha vs `origin/main`, container health, `/api/health` and `/health/ready` codes (`503` from `/health/ready` is normal outside warm-up and trading), legacy systemd unit and timer state, recent bot milestones, memory/disk and the last deploys.

A healthy start logs `Connected to IB at ib-gateway:4004`, `Dashboard RemoteDataPublisher started` and `Trading loop started`. The gateway is healthy once its API port `4002` accepts connections, which takes about 1–2 minutes after start while IBC logs in.

IB Gateway restarts itself daily at 04:00 UTC (`AUTO_RESTART_TIME` / `TWS_COLD_RESTART`). The bot reconnects; that window is outside US market hours.

## Deploying Code Changes

Run this after changes are merged to `main`:

```bash
cd /opt/strategy-lab
bash deploy/ibkr-docker/deploy.sh bot
```

`bot` does, in order:

1. Preflight. It requires a non-root docker user; a clean `main` checkout with no commits that are missing from `origin/main`; no legacy systemd units running and no legacy timer enabled (`trading-bot-phase2-start.timer` starts the old `ibc-gateway`, which takes the IBKR login from Docker), no foreign containers running; all secrets present; a valid compose config; at least 2 GB free disk; and the time outside market hours.
2. `git pull --ff-only origin main`, then lists the new commits.
3. Asks for confirmation.
4. Tags the current image `strategy-lab-trading-bot:previous`, then builds the new one.
5. Recreates only `trading-bot` (`--no-deps`), so the gateway session stays logged in.
6. Verifies the container is healthy, the log contains `Connected to IB at ib-gateway:4004` and `Trading loop started`, and `/api/health` returns 200.
7. Records the result in `deploys.log`.

If verification fails, `deploy.sh rollback-bot` restores the `:previous` image. Then revert the bad change on `main` and run `bot` again so the checkout and the running image match. The script also warns when the pulled compose file changes the gateway, which `bot` does not apply. Reclaim disk occasionally with `docker image prune -f`.

Do not use `docker compose restart`: it neither rebuilds the image nor re-reads `env_file`.

## Upgrading IB Gateway

1. Choose a `gnzsnz/ib-gateway` stable tag. Get its digest with `docker buildx imagetools inspect ghcr.io/gnzsnz/ib-gateway:<tag>`.
2. Update the `image:` tag and digest in `docker-compose.paper.yml` and merge to `main`.
3. Outside market hours, on the host: `bash deploy/ibkr-docker/deploy.sh gateway`. It pulls `main` and the new image, recreates `ib-gateway`, and waits up to 7 minutes for it to be healthy. It then recreates `trading-bot` against the new gateway and verifies it.

## Rotating a Secret

`rotate-env-key.sh` swaps one `KEY=value` line in `secrets/trading-bot.env` without echoing the secret. It:

1. backs up the file;
2. reads the new value at a hidden prompt;
3. shows the length and last 4 characters of the old and new values;
4. asks for confirmation, then replaces the line atomically.

The running container keeps the old value until it is recreated, so finish with `deploy.sh bot` as the deploy user. `docker compose restart` would not re-read `env_file`.

```bash
sudo bash deploy/ibkr-docker/rotate-env-key.sh DASHBOARD__SUPABASE_SERVICE_KEY      # bot write key
sudo bash deploy/ibkr-docker/rotate-env-key.sh OPERATIONAL_METRICS__SUPABASE_ANON_KEY
bash deploy/ibkr-docker/deploy.sh bot
```

The rotation script leaves `.bak.*` copies of the env file in `secrets/`. Delete them once the new key is confirmed working.

To change the IBKR password, rerun `sudo bash deploy/ibkr-docker/import-ibkr-credentials.sh --prompt`. Then run `bash deploy/ibkr-docker/deploy.sh gateway`.

`DASHBOARD__SUPABASE_SERVICE_KEY` is the bot's server-side write key; the browser dashboard uses only the anon key in its own config. Research publishing keys (`RESEARCH_SUPABASE_*`) live only in the research workstation's `.env` and are not deployed with the bot. Keep research and live-trading service keys separate; the script warns if two `*KEY` values match.

## Dashboard Publisher

The Supabase `RemoteDataPublisher` runs inside the `trading-bot` process. It starts when all of these hold:

- `DASHBOARD__ENABLED=true`
- `DASHBOARD__REMOTE_PROVIDER=supabase`
- the Supabase URL and service key are set.

It drains the local outbox at `/app/data/local/publish_outbox.db`. There is no separate publisher container.

## VNC (manual GUI fallback)

VNC is off. To look at the Gateway GUI temporarily:

1. Add `VNC_SERVER_PASSWORD` to `secrets/ib-gateway.env`.
2. Add a local override publishing `127.0.0.1:5900:5900` on `ib-gateway`.
3. Recreate the gateway, then connect through an SSH tunnel.

Remove both afterwards.

## Legacy systemd Stack (rollback only)

Before November 2026 the Oracle VM ran natively under systemd:

| Unit | Role |
|------|------|
| `xvfb-ibkr` | Virtual display |
| `ibc-gateway` | IBC + IB Gateway from `/opt/ibkr` and `/opt/IBC` |
| `x11vnc-ibkr` | VNC fallback |
| `trading-bot-phase2` | `.venv` bot with `EnvironmentFile=/etc/strategy-lab/trading-bot-phase2.env` |

`cutover-systemd-to-docker.sh` does the migration:

- stops and disables these units, keeping the unit files;
- backs up `/opt/strategy-lab/data` to `/var/backups/strategy-lab/`;
- copies the data into `data/trading-bot/`;
- copies the systemd env file to `secrets/trading-bot.env`;
- starts Compose.

`rollback-to-systemd.sh` reverses it, optionally copying Docker-era bot data back first. Never run both stacks at once: they compete for one IB login and port 8080. Once the Docker stack has proven stable, the units and `/opt/ibkr`, `/opt/IBC`, `/etc/ibkr` and `/etc/strategy-lab` can be removed.
