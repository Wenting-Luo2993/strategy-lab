# Phase 4 Dockerized Deployment Runbook

**Status:** Started 2026-07-27<br>
**Scope:** Define and validate a Docker Compose deployment for the IBKR paper stack without cutting over production from the current Oracle VM/systemd services.

---

## Objective

Package the paper trading stack into two operational containers while preserving the current security and recovery model:

- `ib-gateway`: IB Gateway + IBC + Xvfb + optional localhost-only VNC fallback.
- `trading-bot`: Strategy orchestration, IB broker adapter, health API, SQLite stores, Discord notifications, and the in-process Supabase dashboard publisher worker.

The first Phase 4 milestone is a local/VM shadow deployment that can start Gateway, expose the API only inside the Compose boundary, start the bot after Gateway is API-ready, and preserve data/logs across restarts. Production remains on the existing systemd services until Phase 3 recovery runbooks and multi-session burn-in are complete.

The remote data publisher does not need its own container for this milestone. The current application starts `RemoteDataPublisher` inside the orchestrator when dashboard publishing is enabled and Supabase credentials are configured. Splitting it into a standalone worker would require a dedicated entrypoint plus a deliberately shared outbox volume; that is useful later, but it is not required for the first Docker shadow stack.

---

## Key Design Decision

The `trading-bot` service uses `network_mode: "service:ib-gateway"` in the Phase 4 scaffold. This makes the bot share the Gateway container's network namespace, so the bot can keep connecting to `127.0.0.1:4002` just like it does on the VM today.

Benefits:

- Keeps IB Gateway API binding effectively localhost-only from the container pair's point of view.
- Avoids exposing `4002` on the Docker host.
- Avoids adding Docker bridge CIDRs to Gateway trusted IP settings during the first containerization pass.

Tradeoff:

- Ports for both containers are published from the `ib-gateway` service definition. For example, the bot health API on `8080` is published by the `ib-gateway` service because both processes share the same network namespace.

---

## Files

Phase 4 scaffold:

```text
deploy/ibkr-docker/
├── README.md
├── docker-compose.paper.yml
├── ib-gateway/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── healthcheck.sh
├── secrets/
│   ├── ibc.env.example
│   └── trading-bot.env.example
└── .gitignore
```

Ignored runtime paths under `deploy/ibkr-docker/`:

- `runtime/` for operator-provided IB Gateway and IBC installs.
- `secrets/*.env` and `secrets/config.ini` for real credentials/config.
- `data/`, `logs/`, and `state/` for persistent local outputs.

---

## Prepare Runtime Inputs

Do not commit IBKR credentials, IBC config, or installed Gateway binaries. On a Phase 4 test host, populate:

```text
deploy/ibkr-docker/runtime/ibgateway/   # IB Gateway installation/settings path
deploy/ibkr-docker/runtime/IBC/         # IBC installation path
deploy/ibkr-docker/secrets/config.ini   # IBC config with credentials, chmod 600
deploy/ibkr-docker/secrets/ibc.env      # IBC environment, chmod 600
deploy/ibkr-docker/secrets/trading-bot.env
```

Start by copying the checked-in examples:

```bash
cd deploy/ibkr-docker
cp secrets/ibc.env.example secrets/ibc.env
cp secrets/trading-bot.env.example secrets/trading-bot.env
chmod 600 secrets/ibc.env secrets/trading-bot.env
```

Then edit the copied files on the deployment host. Do not paste secret values into chat or logs.

---

## Build And Start Shadow Stack

From repo root:

```bash
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml config
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml build
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml up -d ib-gateway
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml ps
```

After Gateway is healthy, start the bot:

```bash
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml up -d trading-bot
```

Health checks:

```bash
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml ps
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/health/ready
```

IB API port `4002` is intentionally not published to the Docker host. Use an exec probe if needed:

```bash
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml exec ib-gateway bash -lc 'timeout 2 bash -c "</dev/tcp/127.0.0.1/4002" && echo open'
```

---

## Validation Gates

Do not use this stack for production paper trading until these gates pass:

- `docker compose config` succeeds with no secret values printed.
- `ib-gateway` health check reports healthy after cold start.
- VNC fallback is reachable only through localhost/SSH tunnel when enabled.
- `trading-bot` starts only after Gateway API readiness.
- Bot health API reports `alive` and `ready`.
- Read-only IB probe confirms expected paper account ID, positions, and open orders.
- A tiny paper order smoke test can submit, fill, and reconcile to the expected final position.
- Stage 5 dashboard validation passes with Supabase publishing enabled.
- Logs show `Dashboard RemoteDataPublisher started` inside the `trading-bot` container when Supabase service-key publishing is configured.
- Container restart test preserves Gateway settings, local SQLite files, logs, and dashboard outbox state.

---

## Production Cutover Rules

Phase 4 cutover is blocked until:

- Phase 3 restart/reconciliation runbook exists and has been exercised.
- Gateway authentication prompt handling and operator alerting are proven.
- Log rotation is configured for Gateway, IBC, and bot container logs.
- Multi-session paper burn-in shows stable Gateway, bot, dashboard publisher, and final reconciliation.
- The old VM/systemd path has a rollback plan and current config/data backup.

---

## Current Status

Started on 2026-07-27:

- Added Docker Compose paper-stack scaffold under `deploy/ibkr-docker/`.
- Kept production on VM/systemd.
- Preserved current bot-to-Gateway localhost contract through shared container network namespace.
