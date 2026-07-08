# Phase 0 Local IBKR Paper Burn-In Runbook

**Status**: Draft  
**Created**: 2026-07-06  
**Scope**: Prepare and run local Interactive Brokers paper-trading validation before Oracle Cloud IBC deployment.

---

## Objective

Phase 0 proves that the trading bot's IBKR execution path is repeatable before adding Oracle Cloud, IBC, systemd, or Docker. The goal is to validate broker connectivity, market data, account access, order submission behavior, fill telemetry, and fail-closed health checks in the simplest environment first.

No live trading is in scope for this phase.

---

## Current Assumptions

- Local IB Gateway paper port is `4002`.
- Initial quote validation symbols are `QQQ`, `GOOGL`, `AMZN`, and `TSLA`.
- Paper order tests require explicit operator approval before running.
- Partial-fill exploration should use paper trading only and should be run with a symbol/order setup chosen immediately before the test.
- Cloud warmup should verify broker health only when `broker_type=interactive_brokers`.

---

## Code Prepared For Phase 0

- `scripts/ib_paper_smoke.py` supports multi-symbol readonly quote checks.
- `scripts/ib_paper_smoke.py` defaults to IB Gateway paper port `4002`.
- `scripts/ib_paper_smoke.py` supports explicit `--order-symbol`, `--order-type`, `--limit-price`, and `--cancel-on-timeout` options for controlled paper-order testing.
- `WarmupPhaseManager` performs an IB-only readonly broker health check when `broker_type=interactive_brokers` and broker health checks are enabled.
- `BrokerSettings` defaults IB Gateway paper configuration to port `4002` and exposes broker health-check controls.

---

## Environment Configuration

Use local `.env` or shell variables for paper validation:

```bash
BROKER_TYPE=interactive_brokers
BROKER_MODE=paper
IB_HOST=127.0.0.1
IB_PORT=4002
IB_CLIENT_ID=91
IB_ACCOUNT_ID=<paper-account-id>
IB_EXCHANGE=SMART
IB_CURRENCY=USD
IB_MARKET_DATA_TYPE=live
HEALTH_CHECK_ENABLED=true
HEALTH_CHECK_SYMBOL=QQQ
IB_SMOKE_SYMBOLS=QQQ,GOOGL,AMZN,TSLA
IB_SMOKE_ORDER_SYMBOL=QQQ
IB_SMOKE_QUANTITY=1
OPERATIONAL_METRICS_DB=./data/local/operational_metrics.db
```

Use distinct IB client ids for separate tools:

- Smoke script: `91`
- Bot runtime: `1`
- Manual diagnostics: another unused id, such as `92`

---

## Preflight Checklist

Before starting Gateway:

- Confirm dependencies are installed, including `ib-insync`.
- Confirm no other local process is already using the same IB client id.
- Confirm the test is paper mode, not live mode.
- Confirm order tests are disabled unless the command includes `--submit-order` intentionally.

When ready for live local validation, start IB Gateway or TWS and log into the paper account. For Gateway paper mode, confirm:

- API socket port is `4002`.
- API socket clients are enabled.
- Trusted clients are restricted to `127.0.0.1` where possible.
- Any first-time API prompt is accepted only for the expected client id.

---

## Readonly Smoke Test

Run this first. It should not submit orders.

```powershell
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --symbols QQQ,GOOGL,AMZN,TSLA
```

If IB returns error `10089` for live API market data but says delayed data is available, rerun readonly validation with delayed data:

```powershell
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --market-data-type delayed --symbols QQQ,GOOGL,AMZN,TSLA
```

Expected result:

- Connects to IB Gateway.
- Logs account summary.
- Logs market data for all four symbols.
- Disconnects cleanly.
- Exits with code `0`.

Validated local result on 2026-07-07:

- Gateway connection succeeded on `127.0.0.1:4002` with client id `91`.
- Account id `DUQ886014` was returned with net liquidation, cash, and buying power.
- Live market data returned IB error `10089` for `QQQ`, indicating API live-data entitlement was not active for that request.
- Delayed market data succeeded for `QQQ`, `GOOGL`, `AMZN`, and `TSLA`.

Follow-up result on 2026-07-07 after enabling the realtime data subscription:

- Live market data still returned IB error `10089` for `QQQ` through the API session.
- Delayed market data still succeeded for `QQQ` as a control check.
- Account connectivity and account balances remained healthy.

Follow-up result on 2026-07-07 after additional settings propagation/retry:

- The direct `ib_insync` connectivity script returned `ticker.marketDataType == 1` for `QQQ` with live bid/ask/last values.
- The smoke script then succeeded in live mode for `QQQ`, `GOOGL`, `AMZN`, and `TSLA`.
- The earlier `10089` behavior appears to have been an IBKR entitlement/session propagation issue, not a code-path difference that prevents live quotes.

Failure response:

- If connection fails, verify Gateway is running, paper login is complete, API access is enabled, port is `4002`, and client id is unique.
- If account data fails, verify account permissions and paper/live mode.
- If live market data returns error `10089`, validate the API subscription/market-data settings in IBKR and use `--market-data-type delayed` only for readonly connectivity validation.
- If market data fails for one symbol, retry with `QQQ` only to distinguish symbol-specific data from session failure.

---

## Tiny Paper Order Smoke Test

Run only after readonly checks pass and the operator explicitly approves a paper order.

Marketable paper-order validation should run during regular market hours. After hours, market orders and marketable limit orders can queue, reject, or depend on outside-RTH settings that the current adapter does not expose. Readonly connectivity checks can run outside market hours, but Phase 0 order-fill validation should use regular-hours behavior.

### Paper Order Scenario Matrix

Use small order sizes and keep the account close to flat after each scenario.

| Scenario | Purpose | Timing | Suggested Order |
| --- | --- | --- | --- |
| Market buy, liquid symbol | Validate submit, fill, and metrics recording | Regular market hours only | `BUY 1 QQQ MARKET` |
| Market sell / flatten | Validate sell path and return position to prior state | Regular market hours only | `SELL 1 QQQ MARKET` |
| Marketable limit buy | Validate limit order conversion and fill path | Regular market hours only | `BUY 1 QQQ LIMIT` slightly above ask |
| Marketable limit sell / flatten | Validate marketable limit sell path | Regular market hours only | `SELL 1 QQQ LIMIT` slightly below bid |
| Non-marketable limit plus cancel | Validate open-order status, timeout, and cancel path | Regular market hours preferred; acceptable after hours only as an explicit cancel-path test | `BUY 1 QQQ LIMIT` far below bid with `--cancel-on-timeout` |
| Position reconciliation | Confirm broker positions match expected state after fills/cancels | After each order scenario | `get_positions()` through the smoke flow or a diagnostic command |

Partial fills are not a Phase 0 gate. IB paper fills are not a reliable partial-fill simulator, and forcing partial fills usually requires noisy order sizes or less-liquid symbols. Validate timeout/status/cancel behavior in Phase 0, then add mocked adapter tests for partial-fill state handling separately.

### Recommended First Order Sequence

Run this sequence during regular market hours:

1. `BUY 1 QQQ MARKET`
2. Confirm fill metrics are recorded.
3. `SELL 1 QQQ MARKET`
4. Confirm the position is back to the prior state.
5. `BUY 1 QQQ LIMIT` far below bid with timeout and cancel.
6. Confirm the order is cancelled and no unintended position was opened.

The first order requires explicit approval immediately before execution:

```text
BUY 1 QQQ MARKET on IB paper account
```

After that fills, run the matching sell to flatten unless the operator asks to pause.

### Validated Paper Order Result: 2026-07-08

Market-hours paper validation was run against IB Gateway paper on `127.0.0.1:4002` with client id `91`.

Preflight:

- Live quotes succeeded for `QQQ`, `GOOGL`, `AMZN`, and `TSLA`.
- Starting QQQ position was `1` share.

Order results:

| Scenario | Result |
| --- | --- |
| `BUY 1 QQQ MARKET` initial attempt | Gateway reported error `10349` because TIF was applied from an order preset; the client saw a cancelled status, but the order later appeared in execution reconciliation as filled at `706.44`. |
| Adapter fix | IB order mapping now sets `tif="DAY"` explicitly for market, limit, and stop orders. |
| `BUY 1 QQQ MARKET` retry | Filled `1` share at `706.21`; fill metrics recorded. |
| `SELL 2 QQQ MARKET` | Filled `2` shares at `706.12`; returned QQQ position to the pre-validation baseline of `1` share. |
| `BUY 1 QQQ LIMIT 650` with `--cancel-on-timeout` | Submitted, remained open with `filled=0`, timed out, and cancelled successfully. |
| Final readonly check | QQQ position confirmed at `1` share; live QQQ quote succeeded. |

Operational lesson:

- Do not assume an apparent API-side `Cancelled`/error state means no fill occurred until executions and positions have been reconciled. Phase 1 cloud warmup/restart logic must query broker positions and recent executions before allowing new orders after any broker disconnect or order-status ambiguity.
- Explicit order fields matter. Gateway/TWS presets can mutate API orders unless required fields such as TIF are set by the adapter.

```powershell
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --symbols QQQ,GOOGL,AMZN,TSLA --order-symbol QQQ --quantity 1 --side buy --submit-order
```

Expected result:

- Quotes all configured symbols.
- Submits one paper market order for `QQQ`.
- Waits for a fill.
- Records fill metrics locally.
- Logs expected price, actual price, slippage, slippage bps, and latency.

---

## Partial-Fill Exploration

Partial fills are not guaranteed, especially for highly liquid symbols. Do not try to force this with live trading or large unintended exposure. For paper testing, use an explicitly selected less-liquid symbol and a limit order chosen at test time.

Template command:

```powershell
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --symbols <SYMBOL> --order-symbol <SYMBOL> --quantity <QTY> --side buy --order-type limit --limit-price <PRICE> --fill-timeout 30 --cancel-on-timeout --submit-order
```

Expected result for a partial or slow fill scenario:

- If fully filled before timeout, fill metrics are recorded.
- If not fully filled before timeout, the latest order status is logged.
- With `--cancel-on-timeout`, the paper order is cancelled after timeout.

Before executing this test, choose the symbol, quantity, side, and limit price deliberately from current paper market context.

---

## Warmup Broker Health Check

When the bot is configured for IB execution, warmup now performs a readonly broker health check:

- Connect to IB Gateway.
- Read account summary.
- Validate configured account id when provided.
- Request a market data quote for `health_check_symbol` or the first active strategy symbol.
- Read current positions.
- Disconnect the health-check client.

This check runs only when:

```bash
BROKER_TYPE=interactive_brokers
HEALTH_CHECK_ENABLED=true
```

For mock/backtest mode, warmup skips the broker health check.

---

## Phase 0 Exit Criteria

- Readonly smoke succeeds for `QQQ`, `GOOGL`, `AMZN`, and `TSLA`.
- At least one tiny paper order succeeds and records fill metrics.
- Timeout or non-fill behavior logs enough order status to diagnose the issue.
- Warmup fails closed when IB Gateway is unavailable and `broker_type=interactive_brokers`.
- Mock/backtest behavior remains unaffected.
- No live-trading configuration is used.

---

## Validation Commands

Commands that do not require IB Gateway:

```powershell
python -m py_compile vibe/trading_bot/config/settings.py vibe/trading_bot/core/phases/warmup.py scripts/ib_paper_smoke.py
python -m pytest vibe/tests/trading_bot/test_brokers.py
```

Commands that require IB Gateway paper login:

```powershell
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --symbols QQQ,GOOGL,AMZN,TSLA
```

Order-submitting commands require explicit approval immediately before execution.
