# IBKR Paper Trading Stack (Docker Compose)

This is the production deployment for the paper-trading bot. It runs two containers on one host:

| Service | Image | Role |
|---------|-------|------|
| `ib-gateway` | `ghcr.io/gnzsnz/ib-gateway:10.50.1f` (pinned by digest) | IB Gateway + IBC + Xvfb, auto-login, paper API |
| `trading-bot` | built from `vibe/trading_bot/Dockerfile` | Strategy, broker client, in-process Supabase `RemoteDataPublisher`, health API |

The bot connects to `ib-gateway:4004` over the Compose network. `4004` is the image's socat relay to the in-container paper API on `4002`. No IB port is published to the host. The bot health API is published on `127.0.0.1:8080` only.

Every host and every coding agent deploys the same way: `git pull` and then `docker compose`. Do not hand-install IB Gateway, IBC or a Python venv on a host.

## Layout

```text
deploy/ibkr-docker/
├── docker-compose.paper.yml
├── import-ibkr-credentials.sh     # IBKR login -> secrets/, never echoes values
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

Both images run as uid `1000`. That is why the container-read paths are chowned to `1000`, whatever that uid is called on the host.

## Fresh Host Setup

Prerequisites are Docker Engine with the Compose v2 plugin, a checkout at `/opt/strategy-lab`, and a deploy user in the `docker` group. A 1 GB VM needs swap: IB Gateway's JVM uses up to 768 MB of heap.

```bash
cd /opt/strategy-lab/deploy/ibkr-docker
sudo bash import-ibkr-credentials.sh --prompt          # hidden prompts
cp secrets/trading-bot.env.example secrets/trading-bot.env && chmod 600 secrets/trading-bot.env
sudo bash rotate-env-key.sh DASHBOARD__SUPABASE_SERVICE_KEY   # repeat for each secret, answer N to recreate
sudo mkdir -p data/trading-bot logs/trading-bot state/tws_settings
sudo chown -R 1000:1000 data/trading-bot logs/trading-bot state/tws_settings
docker compose -f docker-compose.paper.yml up -d --build
```

## Health Checks

```bash
cd /opt/strategy-lab
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml ps        # both services "healthy"
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/health/ready
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml logs --since 10m trading-bot \
  | grep -iE 'Connected to IB|RemoteDataPublisher|Trading loop|error'
```

A healthy start logs `Connected to IB at ib-gateway:4004`, `Dashboard RemoteDataPublisher started` and `Trading loop started`. The gateway is healthy once its API port `4002` accepts connections, which takes about 1–2 minutes after start while IBC logs in.

IB Gateway restarts itself daily at 04:00 UTC (`AUTO_RESTART_TIME` / `TWS_COLD_RESTART`). The bot reconnects; that window is outside US market hours.

## Deploying Code Changes

Run this after changes are merged to `main`. Use `build` plus `up -d`, not `docker compose restart`: `restart` neither rebuilds the image nor re-reads `env_file`.

```bash
cd /opt/strategy-lab
git fetch origin && git checkout main && git pull --ff-only
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml build trading-bot
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml up -d trading-bot
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml logs --since 5m trading-bot | grep -iE 'Connected to IB|RemoteDataPublisher|ruleset|error'
```

`up -d trading-bot` recreates only the bot; the gateway session stays logged in. Avoid deploying during market hours (09:25–16:05 ET) unless it is an incident fix. Reclaim disk occasionally with `docker image prune -f`.

## Upgrading IB Gateway

1. Choose a `gnzsnz/ib-gateway` stable tag. Get its digest with `docker buildx imagetools inspect ghcr.io/gnzsnz/ib-gateway:<tag>`.
2. Update the `image:` tag and digest in `docker-compose.paper.yml` and merge to `main`.
3. Outside market hours, on the host: `git pull`, then `docker compose -f deploy/ibkr-docker/docker-compose.paper.yml up -d`. The gateway is recreated, and the bot reconnects once it is healthy.

## Rotating a Secret

`rotate-env-key.sh` swaps one `KEY=value` line in `secrets/trading-bot.env` without echoing the secret. It:

1. backs up the file;
2. reads the new value at a hidden prompt;
3. shows the length and last 4 characters of the old and new values;
4. asks for confirmation, then replaces the line atomically;
5. offers to recreate the `trading-bot` container.

The recreate is required because `docker compose restart` does not re-read `env_file`.

```bash
sudo bash deploy/ibkr-docker/rotate-env-key.sh DASHBOARD__SUPABASE_SERVICE_KEY      # bot write key
sudo bash deploy/ibkr-docker/rotate-env-key.sh OPERATIONAL_METRICS__SUPABASE_ANON_KEY
```

The rotation script leaves `.bak.*` copies of the env file in `secrets/`. Delete them once the new key is confirmed working.

To change the IBKR password, rerun `sudo bash deploy/ibkr-docker/import-ibkr-credentials.sh --prompt`. Then run `docker compose -f deploy/ibkr-docker/docker-compose.paper.yml up -d --force-recreate ib-gateway`.

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
