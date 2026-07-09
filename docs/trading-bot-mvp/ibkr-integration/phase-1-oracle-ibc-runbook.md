# Phase 1 Oracle VM IBC Deployment Runbook

**Status**: In progress  
**Created**: 2026-07-08  
**Scope**: Deploy IB Gateway, IBC, and the trading bot smoke path on an Oracle Cloud VM using IBC-first automation.

---

## Objective

Phase 1 moves the validated local IBKR paper execution path onto Oracle Cloud. The first Oracle deployment uses IBC from the start, not manual Gateway login as the primary operating model.

Phase 1 is complete when a rebooted Oracle VM can start IBC + IB Gateway, the bot can connect to Gateway on localhost, live paper quotes work, and a tiny paper order can be submitted and reconciled from the cloud host.

---

## Current Baseline From Phase 0

Validated locally on 2026-07-08:

- IB Gateway paper API works on `127.0.0.1:4002`.
- Live API quotes work for `QQQ`, `GOOGL`, `AMZN`, and `TSLA`.
- Paper market buy/sell orders fill and record metrics.
- Non-marketable limit timeout/cancel path works.
- Adapter sets `tif="DAY"` explicitly to avoid Gateway/TWS order preset mutation.
- Startup/restart logic must reconcile positions and recent executions before assuming no fill occurred after ambiguous broker states.

## Current Oracle VM State

Prepared on 2026-07-08:

| Component | Current state |
| --- | --- |
| VM | Oracle Ubuntu 22.04.5 `x86_64`, `VM.Standard.E2.1.Micro`, 4 GB swap added and persisted. |
| IB Gateway | Stable Gateway installed at `/opt/ibkr/ibgateway`; IBC expects major version `1045`. |
| IBC | IBC 3.24.1 installed at `/opt/IBC`. |
| Display | `xvfb-ibkr.service` active on `:1`; `x11vnc-ibkr.service` active on localhost-only port `5901`. |
| IBC config | `/etc/ibkr/config.ini` exists, mode set to paper, API connection prompts set to accept, credentials entered directly on VM. |
| IBC env | `/etc/ibkr/ibc.env` exists, root-owned and group-readable by `ubuntu`, with Gateway/IBC paths and `DISPLAY=:1`. |
| IBC wrapper | `/opt/ibkr/start-ibc-gateway.sh` launches IBC with Oracle-specific environment. |
| systemd service | `ibc-gateway.service` exists, is enabled, and starts IBC/Gateway. |
| Repo checkout | `/opt/strategy-lab` cloned from GitHub; modified IB smoke/adapter/warmup files copied from local working tree. |
| Python | `/opt/strategy-lab/.venv` created; `ib_insync 0.9.86` imports; smoke-related files compile. |
| Existing bot container | Previous `trading-bot` Docker container stopped to free memory for Gateway validation. |
| API exposure | Gateway listens on `4002`; host firewall drops non-loopback traffic to `4001` and `4002`. |
| Current validation blocker | None for Phase 1 smoke. Readonly cloud smoke, paper market buy/sell, non-marketable limit cancel, and final reconciliation passed on 2026-07-09. |

Do not paste `IbLoginId` or `IbPassword` into shared logs or chat.

---

## Target Architecture

```text
Oracle VM
├── Ubuntu 22.04 or 24.04 LTS
├── Java 17 runtime
├── Xvfb display :1
├── Optional SSH-tunneled VNC/noVNC fallback
├── IB Gateway stable
├── IBC 3.24.1 or newer compatible release
├── systemd service: ibc-gateway
├── strategy-lab repo checkout
├── Python virtual environment
└── paper smoke validation commands
```

Gateway API binding remains local-only:

```text
Trading bot -> 127.0.0.1:4002 -> IB Gateway launched by IBC
```

Do not expose IB Gateway API port `4002` publicly.

---

## Required Inputs

The operator must provide these before executing VM commands:

| Input | Example | Notes |
| --- | --- | --- |
| Oracle VM public IP | `<ORACLE_PUBLIC_IP>` | Use SSH only; do not expose Gateway ports. |
| SSH user | `ubuntu` | Depends on image. |
| SSH private key path | `~/.ssh/oracle-trading-bot.pem` | Keep local permissions restricted. |
| VM architecture | `x86_64` or `aarch64` | Determines IB Gateway installer: Linux x64 or Linux ARM. |
| IBKR paper username | secret | Do not store in git. |
| IBKR paper password | secret | Do not store in git. |
| IBKR paper account id | `DU...` | Used for account mismatch checks. |
| Discord webhook URL | secret, optional | Recommended for cloud alerting. |

Recommended Oracle shape:

- Prefer Ampere A1 with at least 2 OCPUs and 4-8 GB RAM if available.
- Avoid 1 GB micro VM for Gateway + Java + display + bot; it is likely too tight.

---

## Local SSH Setup

From the development machine:

```powershell
ssh -i <KEY_PATH> ubuntu@<ORACLE_PUBLIC_IP>
```

Optional SSH tunnel for fallback GUI access after VNC/noVNC is configured:

```powershell
ssh -i <KEY_PATH> -L 5901:127.0.0.1:5901 ubuntu@<ORACLE_PUBLIC_IP>
```

Only expose VNC through an SSH tunnel. Do not add public ingress rules for VNC.

---

## VM Base Packages

Run on the Oracle VM:

```bash
sudo apt-get update
sudo apt-get install -y \
  curl \
  unzip \
  git \
  ca-certificates \
  openjdk-17-jre \
  xvfb \
  xauth \
  x11-utils \
  x11vnc \
  python3 \
  python3-venv \
  python3-pip
```

Confirm Java 17:

```bash
java -version
```

---

## Directory Layout

Use root-owned application directories and a restricted secret directory:

```bash
sudo mkdir -p /opt/ibkr /opt/IBC /opt/strategy-lab /var/log/ibkr /etc/ibkr
sudo chown -R ubuntu:ubuntu /opt/ibkr /opt/IBC /opt/strategy-lab /var/log/ibkr
sudo chmod 700 /etc/ibkr
```

Suggested layout:

```text
/opt/ibkr/                  # IB Gateway install root
/opt/IBC/                   # IBC install root
/opt/strategy-lab/          # repo checkout
/var/log/ibkr/              # IBC/Gateway logs
/etc/ibkr/ibc.env           # secrets and runtime env, chmod 600
/etc/ibkr/config.ini        # IBC config, chmod 600
```

---

## Install IB Gateway Stable

Official stable Gateway page currently lists stable version `10.45.1h` and separate Linux installers for `x86_64` and `ARM64`.

Choose one installer based on VM architecture:

```bash
uname -m
```

For `x86_64`:

```bash
cd /tmp
curl -L -o ibgateway-stable.sh \
  https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
chmod +x ibgateway-stable.sh
./ibgateway-stable.sh
```

For `aarch64` / ARM64:

```bash
cd /tmp
curl -L -o ibgateway-stable.sh \
  https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-arm.sh
chmod +x ibgateway-stable.sh
./ibgateway-stable.sh
```

During install, choose a stable path and record it here:

```text
IB Gateway install path: ______________________________
```

If the installer requires GUI interaction, run it under the Xvfb/VNC fallback session.

---

## Install IBC

Use the latest compatible IBC release. At runbook creation, GitHub lists `3.24.1` as latest, with Java 17 targeting.

Download from:

```text
https://github.com/IbcAlpha/IBC/releases/latest
```

Install pattern:

```bash
cd /tmp
# Download the Linux/macOS IBC zip asset from the latest release page.
# The exact asset filename can change; verify it on the release page.
unzip <IBC_LINUX_ZIP> -d /tmp/ibc
cp -R /tmp/ibc/* /opt/IBC/
chmod +x /opt/IBC/*.sh /opt/IBC/scripts/*.sh 2>/dev/null || true
```

Record the installed version:

```bash
cat /opt/IBC/version 2>/dev/null || true
```

```text
IBC version: ______________________________
```

---

## IBC Secrets And Config

Current VM config already has this shape. For a clean rebuild, create `/etc/ibkr/ibc.env`:

```bash
sudo tee /etc/ibkr/ibc.env >/dev/null <<'EOF'
DISPLAY=:1
TWS_MAJOR_VRSN=1045
IBC_INI=/etc/ibkr/config.ini
TRADING_MODE=paper
TWOFA_TIMEOUT_ACTION=exit
IBC_PATH=/opt/IBC
TWS_PATH=/opt/ibkr
TWS_SETTINGS_PATH=/opt/ibkr/ibgateway
LOG_PATH=/var/log/ibkr
APP=GATEWAY
JAVA_PATH=
TWSUSERID=
TWSPASSWORD=
FIXUSERID=
FIXPASSWORD=
EOF
sudo chgrp ubuntu /etc/ibkr /etc/ibkr/ibc.env
sudo chmod 750 /etc/ibkr
sudo chmod 640 /etc/ibkr/ibc.env
```

Create `/etc/ibkr/config.ini` from the IBC sample config and edit only on the VM:

```bash
sudo cp /opt/IBC/config.ini /etc/ibkr/config.ini
sudo chgrp ubuntu /etc/ibkr/config.ini
sudo chmod 640 /etc/ibkr/config.ini
sudo nano /etc/ibkr/config.ini
```

Minimum values to set or verify in IBC config:

```ini
IbLoginId=<IBKR_PAPER_USERNAME>
IbPassword=<IBKR_PAPER_PASSWORD>
TradingMode=paper
ReadOnlyLogin=no
AcceptIncomingConnectionAction=accept
```

Recommended values to review in IBC config:

```ini
AutoRestartTime=04:00
ColdRestartTime=04:00
SaveTwsSettingsAt=04:15
ExistingSessionDetectedAction=primary
```

Exact setting names can vary by IBC release. Use the sample `config.ini` comments as source of truth.

Never commit `/etc/ibkr/ibc.env` or `/etc/ibkr/config.ini`.

Credential entry checkpoint:

```bash
sudo nano /etc/ibkr/config.ini
sudo grep -nE '^(TradingMode|AcceptIncomingConnectionAction|ExistingSessionDetectedAction)' /etc/ibkr/config.ini
sudo grep -n '^IbLoginId=__SET_ON_VM__$\|^IbPassword=__SET_ON_VM__$' /etc/ibkr/config.ini && echo 'Credentials still need to be set' || echo 'Credential placeholders were replaced'
```

Do not run commands that print the actual `IbLoginId` or `IbPassword` values into shared logs or chat.

---

## Xvfb Service

Create `/etc/systemd/system/xvfb-ibkr.service`:

```ini
[Unit]
Description=Xvfb display for IB Gateway
After=network-online.target

[Service]
User=ubuntu
Environment=DISPLAY=:1
ExecStart=/usr/bin/Xvfb :1 -screen 0 1280x800x24 -nolisten tcp
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb-ibkr.service
systemctl status xvfb-ibkr.service --no-pager
```

---

## Optional SSH-Tunneled VNC Fallback

Create `/etc/systemd/system/x11vnc-ibkr.service`:

```ini
[Unit]
Description=SSH-tunneled VNC access to IB Gateway display
After=xvfb-ibkr.service
Requires=xvfb-ibkr.service

[Service]
User=ubuntu
Environment=DISPLAY=:1
ExecStart=/usr/bin/x11vnc -display :1 -localhost -forever -shared -rfbport 5901
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now x11vnc-ibkr.service
systemctl status x11vnc-ibkr.service --no-pager
```

Connect from local machine with an SSH tunnel:

```powershell
ssh -i <KEY_PATH> -L 5901:127.0.0.1:5901 ubuntu@<ORACLE_PUBLIC_IP>
```

Then point a VNC client at `127.0.0.1:5901`.

---

## IBC Gateway systemd Service

On the Oracle VM, `/opt/IBC/gatewaystart.sh` assigns its own defaults for version and paths. Use the Oracle wrapper instead of modifying the upstream IBC script:

IBC's Linux Gateway path logic expects `${TWS_PATH}/ibgateway/${TWS_MAJOR_VRSN}`. The stable Gateway installer placed jars directly under `/opt/ibkr/ibgateway`, so create a versioned symlink:

```bash
ln -s /opt/ibkr/ibgateway /opt/ibkr/ibgateway/1045
```

```bash
cat > /opt/ibkr/start-ibc-gateway.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
set -a
source /etc/ibkr/ibc.env
set +a
export DISPLAY TWS_MAJOR_VRSN IBC_INI TRADING_MODE TWOFA_TIMEOUT_ACTION IBC_PATH TWS_PATH TWS_SETTINGS_PATH LOG_PATH APP JAVA_PATH TWSUSERID TWSPASSWORD FIXUSERID FIXPASSWORD
exec /opt/IBC/scripts/displaybannerandlaunch.sh
EOF
chmod +x /opt/ibkr/start-ibc-gateway.sh
```

Create `/etc/systemd/system/ibc-gateway.service`:

```ini
[Unit]
Description=IBC-managed IB Gateway paper session
After=network-online.target xvfb-ibkr.service
Requires=xvfb-ibkr.service

[Service]
User=ubuntu
WorkingDirectory=/opt/IBC
EnvironmentFile=/etc/ibkr/ibc.env
Environment=DISPLAY=:1
ExecStart=/opt/ibkr/start-ibc-gateway.sh
Restart=always
RestartSec=15
StandardOutput=append:/var/log/ibkr/ibc-gateway.log
StandardError=append:/var/log/ibkr/ibc-gateway.err

[Install]
WantedBy=multi-user.target
```

Validate it before enabling:

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/ibc-gateway.service
```

After credentials are entered directly on the VM, enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ibc-gateway.service
systemctl status ibc-gateway.service --no-pager
journalctl -u ibc-gateway.service -n 100 --no-pager
```

If IBC exits with `Invalid ColdRestartTime`, verify the value is `hh:mm`, for example `04:00`, not a day-of-week string.

If IBC exits with `can't find jars folder` or `Neither tws.vmoptions nor ibgateway.vmoptions could be found`, verify `TWS_PATH=/opt/ibkr` and `/opt/ibkr/ibgateway/1045` points to `/opt/ibkr/ibgateway`.

---

## Gateway API Settings

On the first successful Gateway login, use fallback VNC if needed and verify:

- API socket clients are enabled.
- Paper socket port is `4002`.
- Trusted IPs are restricted to `127.0.0.1`.
- API order precautions are set deliberately.
- Live market data subscriptions are visible in the paper session.
- Settings persist after Gateway restart.

Add host firewall protection while Gateway is binding broadly:

```bash
sudo iptables -C INPUT ! -i lo -p tcp --dport 4002 -j DROP 2>/dev/null || sudo iptables -I INPUT ! -i lo -p tcp --dport 4002 -j DROP
sudo iptables -C INPUT ! -i lo -p tcp --dport 4001 -j DROP 2>/dev/null || sudo iptables -I INPUT ! -i lo -p tcp --dport 4001 -j DROP
```

On first API connection, IBKR may require the paper trading disclaimer to be accepted. Use SSH-tunneled VNC and accept it manually; do not ask automation to accept legal disclaimers on the operator's behalf.

For paper order validation, verify the Gateway API setting is not in Read-Only mode. The readonly smoke can still pass while order/open-order API calls report `Error 321: The API interface is currently in Read-Only mode`.

If IBC supports API precautions configuration in `config.ini` for the installed version, prefer IBC-managed settings over manual Gateway UI changes.

---

## Deploy strategy-lab Repo

On the Oracle VM:

```bash
cd /opt/strategy-lab
git clone <REPO_URL> .
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r vibe/trading_bot/requirements.txt
pip install ib-insync
```

If the repo is private, use a deploy key or authenticated Git remote. Do not store personal tokens in shell history.

---

## Cloud Paper Environment

Create a VM-local `.env` or service environment file. Do not commit it.

```bash
cat > /opt/strategy-lab/.env <<'EOF'
BROKER_TYPE=interactive_brokers
BROKER_MODE=paper
IB_HOST=127.0.0.1
IB_PORT=4002
IB_CLIENT_ID=91
IB_ACCOUNT_ID=<PAPER_ACCOUNT_ID>
IB_EXCHANGE=SMART
IB_CURRENCY=USD
IB_MARKET_DATA_TYPE=1
HEALTH_CHECK_ENABLED=true
HEALTH_CHECK_SYMBOL=QQQ
IB_SMOKE_SYMBOLS=QQQ,GOOGL,AMZN,TSLA
OPERATIONAL_METRICS_DB=./data/local/operational_metrics.db
EOF
chmod 600 /opt/strategy-lab/.env
```

---

## Phase 1 Validation Commands

Run from `/opt/strategy-lab` with the Python virtual environment active.

Readonly validation:

```bash
source .venv/bin/activate
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --market-data-type live --symbols QQQ,GOOGL,AMZN,TSLA
```

Expected result:

- Connects to Gateway launched by IBC.
- Account id and balances are returned.
- Live quotes are returned for all four symbols.
- Disconnects cleanly.

Observed 2026-07-09 from Oracle VM:

- Connected to Gateway on `127.0.0.1:4002` with client id `91`.
- Account summary and existing QQQ paper position loaded.
- Live quotes returned for `QQQ`, `GOOGL`, `AMZN`, and `TSLA`.
- A second readonly smoke after disabling Gateway API Read-Only mode completed without `Error 321`; paper order validation still requires explicit approval before execution.

Paper order validation from Oracle VM requires explicit approval immediately before execution:

```bash
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --market-data-type live --symbols QQQ --order-symbol QQQ --quantity 1 --side buy --submit-order
```

Then flatten back to the pre-test baseline position, based on actual starting position and fills.

Observed 2026-07-09 from Oracle VM after explicit approval:

| Step | Result |
| --- | --- |
| Preflight | QQQ baseline position was `1.0`; live QQQ quote returned around `722.65`. |
| Market buy | `BUY 1 QQQ MARKET` filled at `722.65`; position moved to `2.0`; fill latency about `2840 ms`; commission `1.000003 USD`. |
| Market sell | `SELL 1 QQQ MARKET` filled at `722.61`; position returned to `1.0`; fill latency about `252 ms`; commission `1.015084 USD`. |
| Limit cancel | `BUY 1 QQQ LIMIT 650` submitted, timed out after 5 seconds with zero fill, and cancelled successfully. |
| Final reconciliation | QQQ position `1.0`, zero open orders, zero open trades. |

Memory after the order smoke remained tight but stable: `956 MiB` RAM total, about `257 MiB` available, `4.0 GiB` swap total with about `3.7 GiB` free.

Non-marketable limit/cancel validation:

```bash
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --market-data-type live --symbols QQQ --order-symbol QQQ --quantity 1 --side buy --order-type limit --limit-price <FAR_BELOW_BID> --fill-timeout 5 --cancel-on-timeout --submit-order
```

---

## Reboot Validation

After readonly validation succeeds once, reboot the VM:

```bash
sudo reboot
```

Reconnect and verify services:

```bash
systemctl status xvfb-ibkr.service --no-pager
systemctl status ibc-gateway.service --no-pager
journalctl -u ibc-gateway.service -n 100 --no-pager
```

Then rerun readonly validation:

```bash
cd /opt/strategy-lab
source .venv/bin/activate
python scripts/ib_paper_smoke.py --host 127.0.0.1 --port 4002 --client-id 91 --market-data-type live --symbols QQQ,GOOGL,AMZN,TSLA
```

---

## Failure Handling

| Failure | Response |
| --- | --- |
| Gateway does not start | Check `journalctl -u ibc-gateway.service`; verify `DISPLAY=:1`, Gateway path, and IBC command. |
| Login prompt is waiting | Use SSH-tunneled VNC to inspect; approve 2FA manually if needed. |
| API connection refused | Confirm Gateway is logged in, API socket is enabled, and port is `4002`. |
| Live quotes fail with `10089` | Confirm market data subscription is active in the paper session; restart Gateway after account setting changes. |
| Duplicate client id | Stop other scripts using the same client id or use a distinct diagnostic id. |
| Order status ambiguous | Query positions and recent executions before any new order; do not rely on local status alone. |
| VM reboot loses Gateway settings | Persist Gateway settings through IBC config or repeat UI configuration under VNC, then restart and retest. |

---

## Phase 1 Exit Criteria

- IBC starts IB Gateway on the Oracle VM without manual shell commands.
- Rebooting the Oracle VM brings back Xvfb and IBC/Gateway through systemd.
- Gateway API accepts localhost connections on `4002`.
- Readonly smoke test returns account summary, positions, and live quotes from the Oracle VM.
- A tiny paper order can be submitted, filled, recorded, and reconciled from the Oracle VM.
- A non-marketable limit order can be submitted, observed as open, timed out, and cancelled.
- Any 2FA or unexpected Gateway prompt is visible through SSH-tunneled fallback GUI.
- No IBKR credentials, Gateway credentials, or IBC secret config are committed to git.

---

## Handoff To Phase 2

Phase 2 starts after Phase 1 exit criteria pass. Carry forward these required behaviors:

- Bot startup must fail closed if IB Gateway is unavailable.
- Bot startup must validate account id, live quote access, and current positions.
- Bot restart must reconcile positions and recent executions before submitting any order.
- Paper/live mode must require explicit configuration and should default to paper.
