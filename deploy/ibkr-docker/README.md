# IBKR Docker Paper Stack

Phase 4 scaffold for running IB Gateway/IBC and the trading bot under Docker Compose.

This is not the production path yet. Production remains on the Oracle VM systemd services until Phase 3 recovery runbooks, watchdogs, log rotation, and multi-session burn-in are complete.

## Layout

```text
deploy/ibkr-docker/
├── docker-compose.paper.yml
├── ib-gateway/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── healthcheck.sh
└── secrets/
    ├── ibc.env.example
    └── trading-bot.env.example
```

Runtime inputs are intentionally git-ignored:

- `runtime/ibgateway/` - operator-provided IB Gateway installation/settings path.
- `runtime/IBC/` - operator-provided IBC installation path.
- `secrets/config.ini` - real IBC config with credentials.
- `secrets/ibc.env` - real IBC runtime environment.
- `secrets/trading-bot.env` - real bot runtime environment.
- `data/`, `logs/`, `state/` - persistent outputs.

## Start

From the repository root:

```bash
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml config
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml build
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml up -d ib-gateway
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml up -d trading-bot
```

Health checks:

```bash
docker compose -f deploy/ibkr-docker/docker-compose.paper.yml ps
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/health/ready
```

The IB API port is not published to the host. The trading bot shares the `ib-gateway` network namespace and connects to `127.0.0.1:4002`.

## Dashboard Publisher

The Supabase remote data publisher is part of the `trading-bot` process. When `DASHBOARD__ENABLED=true`, `DASHBOARD__REMOTE_PROVIDER=supabase`, and the Supabase URL/service key are configured, the orchestrator starts the in-process `RemoteDataPublisher` worker and drains the local dashboard outbox from `/app/data/local/publish_outbox.db`.

Do not add a separate publisher container for the first Phase 4 shadow stack. A standalone publisher can be introduced later if we add a dedicated worker entrypoint and intentionally share the outbox volume between services.
