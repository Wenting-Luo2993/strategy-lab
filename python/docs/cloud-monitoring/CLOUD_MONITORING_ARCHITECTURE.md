# Cloud Deployment & Real-Time Monitoring Architecture

**Document Version**: 1.0
**Last Updated**: January 10, 2026
**Purpose**: Architecture design for deploying the trading bot to cloud infrastructure with real-time monitoring capabilities

---

## 📋 Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Overview](#system-overview)
3. [Architecture Components](#architecture-components)
4. [Deployment Options](#deployment-options)
5. [Real-Time Monitoring Strategy](#real-time-monitoring-strategy)
6. [Implementation Roadmap](#implementation-roadmap)
7. [Cost Analysis](#cost-analysis)
8. [Security Considerations](#security-considerations)
9. [Disaster Recovery](#disaster-recovery)

---

## Executive Summary

This document outlines a cloud-native architecture for deploying the Strategy Lab trading bot with comprehensive real-time monitoring capabilities. The design supports:

- **24/7 automated trading** during market hours
- **Real-time log streaming** accessible from any device
- **Live trade monitoring** via web dashboard
- **Alerting and notifications** for critical events
- **Multi-cloud support** (Oracle Cloud, AWS, GCP, Azure)
- **Cost-optimized** deployment using free/minimal tier services

### Key Features
✅ Containerized deployment with auto-restart
✅ Centralized log aggregation and search
✅ Real-time trade execution dashboard
✅ Mobile-friendly monitoring interface
✅ Automated alerts (email, SMS, Slack)
✅ Performance metrics and analytics
✅ Minimal operational overhead

---

## System Overview

### Current Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Strategy Lab Bot                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  orchestrator_main.py                                       │
│  ├─ Market hours detection (US Eastern)                    │
│  ├─ Script lifecycle management                            │
│  └─ Graceful shutdown handling                             │
│                                                             │
│  Trading Scripts (test_finnhub_orchestrator.py, etc.)       │
│  ├─ Finnhub WebSocket connection                           │
│  ├─ ORB Strategy execution                                 │
│  ├─ Risk management                                        │
│  └─ Trade execution                                        │
│                                                             │
│  Logging System                                             │
│  ├─ File-based logs (logs/ directory)                      │
│  ├─ Selective console output                               │
│  └─ StrategyLabLogger with metadata                        │
│                                                             │
│  Results & Data                                             │
│  ├─ Trade history (results/backtest/)                      │
│  ├─ Performance metrics (CSV files)                        │
│  └─ Data cache (data_cache/)                               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Proposed Cloud Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           Cloud Provider                                 │
│                  (Oracle Cloud / AWS / GCP / Azure)                      │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────┐        │
│  │              Compute Layer                                 │        │
│  │  ┌──────────────────────────────────────────────┐          │        │
│  │  │ Container Instance / VM                      │          │        │
│  │  │  ├─ Docker Container (strategy-lab:latest)   │          │        │
│  │  │  │  ├─ orchestrator_main.py (main process)   │          │        │
│  │  │  │  ├─ Trading scripts                       │          │        │
│  │  │  │  └─ Log shipping sidecar                  │          │        │
│  │  │  └─ Health check endpoint                    │          │        │
│  │  └──────────────────────────────────────────────┘          │        │
│  └────────────────────────────────────────────────────────────┘        │
│                           ↓                                             │
│  ┌────────────────────────────────────────────────────────────┐        │
│  │              Monitoring & Observability Layer              │        │
│  │                                                            │        │
│  │  ┌─────────────────────┐  ┌─────────────────────┐         │        │
│  │  │   Log Aggregation   │  │   Metrics Storage   │         │        │
│  │  │   ─────────────     │  │   ──────────────    │         │        │
│  │  │   • CloudWatch      │  │   • Prometheus      │         │        │
│  │  │   • Elasticsearch   │  │   • InfluxDB        │         │        │
│  │  │   • Loki            │  │   • TimescaleDB     │         │        │
│  │  └─────────────────────┘  └─────────────────────┘         │        │
│  │                                                            │        │
│  │  ┌──────────────────────────────────────────────┐         │        │
│  │  │   Time-Series Database                       │         │        │
│  │  │   ─────────────────────                      │         │        │
│  │  │   • Trade executions (timestamped)           │         │        │
│  │  │   • P&L snapshots                            │         │        │
│  │  │   • Strategy signals                         │         │        │
│  │  │   • Performance metrics                      │         │        │
│  │  └──────────────────────────────────────────────┘         │        │
│  └────────────────────────────────────────────────────────────┘        │
│                           ↓                                             │
│  ┌────────────────────────────────────────────────────────────┐        │
│  │              Visualization & Access Layer                  │        │
│  │                                                            │        │
│  │  ┌─────────────────┐  ┌──────────────┐  ┌──────────────┐  │        │
│  │  │  Web Dashboard  │  │  Log Viewer  │  │   Alerting   │  │        │
│  │  │  ─────────────  │  │  ──────────  │  │   ────────   │  │        │
│  │  │  • Grafana      │  │  • Kibana    │  │  • PagerDuty │  │        │
│  │  │  • Custom Flask │  │  • Grafana   │  │  • Slack     │  │        │
│  │  │  • Streamlit    │  │  • CloudLogs │  │  • Email     │  │        │
│  │  └─────────────────┘  └──────────────┘  └──────────────┘  │        │
│  └────────────────────────────────────────────────────────────┘        │
│                           ↓                                             │
│  ┌────────────────────────────────────────────────────────────┐        │
│  │              Storage & Backup Layer                        │        │
│  │  ┌──────────────────────────────────────────────┐          │        │
│  │  │  Object Storage (S3/GCS/OCI Object Storage)  │          │        │
│  │  │  ├─ Archived logs (> 7 days)                 │          │        │
│  │  │  ├─ Trade history exports                    │          │        │
│  │  │  ├─ Daily performance reports                │          │        │
│  │  │  └─ Configuration backups                    │          │        │
│  │  └──────────────────────────────────────────────┘          │        │
│  └────────────────────────────────────────────────────────────┘        │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                           ↓
           ┌───────────────────────────────┐
           │   External Integrations       │
           ├───────────────────────────────┤
           │  • Finnhub WebSocket          │
           │  • Google Drive (backups)     │
           │  • Email/SMS providers        │
           │  • Slack/Discord webhooks     │
           └───────────────────────────────┘
                           ↑
           ┌───────────────────────────────┐
           │   Your Devices (Access)       │
           ├───────────────────────────────┤
           │  • Web browser (dashboard)    │
           │  • Mobile app/browser         │
           │  • Slack/email notifications  │
           │  • SSH terminal (debugging)   │
           └───────────────────────────────┘
```

---

## 💰 Zero-Cost Architecture (100% Free)

**Yes! You can run everything for FREE.** Here's the complete setup:

```
┌────────────────────────────────────────────────────────────────┐
│                  Oracle Cloud Always Free Tier                 │
│                         (FREE FOREVER)                         │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ARM Compute Instance (4 OCPUs, 24 GB RAM - FREE)             │
│  ┌──────────────────────────────────────────────────┐        │
│  │  Docker Containers:                              │        │
│  │  ├─ strategy-lab:latest (trading bot)            │        │
│  │  ├─ grafana/grafana:latest (dashboard) - FREE    │        │
│  │  └─ grafana/loki:latest (logs) - FREE            │        │
│  └──────────────────────────────────────────────────┘        │
│                                                                │
│  Block Storage: 200 GB (FREE)                                 │
│  ├─ SQLite database (trades.db) - NO HOSTING COST            │
│  └─ Log files                                                 │
│                                                                │
│  Object Storage: 10 GB (FREE)                                 │
│  └─ Daily database backups                                    │
│                                                                │
└────────────────────────────────────────────────────────────────┘
            ↓ (Outbound HTTPS - FREE)
┌────────────────────────────────────────────────────────────────┐
│                   Free External Services                       │
├────────────────────────────────────────────────────────────────┤
│  • Finnhub WebSocket (free tier: 60 calls/min)                │
│  • Slack Webhooks (FREE - unlimited)                          │
│  • Discord Webhooks (FREE - unlimited)                        │
│  • Gmail SMTP (FREE - 500 emails/day)                         │
│  • Google Drive (FREE - 15 GB storage)                        │
└────────────────────────────────────────────────────────────────┘
            ↑
┌────────────────────────────────────────────────────────────────┐
│                Your Devices (Access from anywhere)             │
├────────────────────────────────────────────────────────────────┤
│  • Web Browser → http://your-oracle-ip:3000 (Grafana)        │
│  • Slack App → Real-time notifications                        │
│  • Email → Daily summaries and alerts                         │
│  • SSH Terminal → Debug access (if needed)                    │
└────────────────────────────────────────────────────────────────┘
```

### What You Get (All FREE):
✅ **24/7 Trading Bot** running in Oracle Cloud
✅ **Real-time Dashboard** (Grafana) accessible from browser/phone
✅ **Log Search & Viewing** (Loki) - last 30 days
✅ **Trade Database** (SQLite) - unlimited history
✅ **Automated Backups** to Oracle Object Storage
✅ **Slack/Discord Alerts** - instant notifications
✅ **Email Alerts** - daily summaries
✅ **99.9% Uptime** with auto-restart

### Total Monthly Cost: **$0** 🎉

### Free Tier Limits to Know:
- Oracle Cloud: **Always Free** (not a trial!) - 4 ARM cores, 24 GB RAM, 200 GB storage
- Grafana Cloud Alternative: Free tier = 10k series metrics, 50 GB logs, 50 GB traces (optional)
- Slack: Unlimited free webhooks
- Gmail SMTP: 500 emails/day (more than enough)
- Finnhub: 60 API calls/minute (free tier)

---

## Architecture Components

### 1. Compute Layer

#### Option A: Container Instance (Recommended)
- **Service**: Oracle Container Instances, AWS ECS Fargate, GCP Cloud Run
- **Configuration**:
  - 1-2 vCPUs, 2-4 GB RAM
  - Auto-restart on failure
  - Health checks every 30s
  - Environment variables for secrets

**Pros**:
- Serverless (no VM management)
- Built-in auto-scaling
- Pay-per-use pricing
- Easier updates (just push new image)

**Cons**:
- Less control over infrastructure
- Limited debugging capabilities

#### Option B: Virtual Machine
- **Service**: Oracle Always Free Compute, AWS EC2, GCP Compute Engine
- **Configuration**:
  - Ubuntu 22.04 LTS
  - Docker + Docker Compose
  - Systemd service for auto-restart
  - SSH access for debugging

**Pros**:
- Full control
- Easy debugging (SSH access)
- Can run multiple services
- Persistent storage included

**Cons**:
- Requires OS management
- Manual scaling
- More maintenance overhead

### 2. Logging Infrastructure

#### Structured Logging Enhancement

**Current State**: File-based logs with custom StrategyLabLogger

**Proposed Enhancement**:
```python
# Add structured logging with JSON output
import structlog

logger = structlog.get_logger()
logger.info("trade_executed",
    symbol="AAPL",
    side="BUY",
    quantity=100,
    price=150.25,
    strategy="ORB",
    timestamp=datetime.now().isoformat()
)
```

#### Log Aggregation Options

##### Option 1: Grafana Loki (⭐ FREE & Recommended)
- **100% Free and open-source**
- Prometheus-like log aggregation
- Lower resource requirements than ELK
- Integrates perfectly with Grafana
- Runs on Oracle Cloud Always Free tier

**Resource Requirements**:
- Minimum: 512 MB RAM (fits in free tier!)
- Storage: 5-10 GB for 30 days retention
- **Monthly Cost: $0**

**Implementation**:
```bash
# Add to docker-compose.yml
services:
  loki:
    image: grafana/loki:latest
    ports:
      - "3100:3100"
    volumes:
      - loki_data:/loki
    command: -config.file=/etc/loki/local-config.yaml
```

##### Option 2: Cloud-Native (FREE Tiers Available)
- **Oracle Cloud**: Logging Service (FREE: 10 GB/month in Always Free)
- **AWS**: CloudWatch Logs (FREE tier: 5 GB/month)
- **GCP**: Cloud Logging (FREE tier: 50 GB/month)
- **Grafana Cloud**: FREE tier (50 GB logs/month)

**Best for**: If you prefer managed service over self-hosting
**Monthly Cost**: $0 (within free tiers)

##### Option 3: ELK Stack (Free but Resource-Heavy)
- Self-hosted on separate VM/container
- Full-text search capabilities
- Advanced visualization
- Free and open-source

**Resource Requirements**:
- Minimum: 2 GB RAM (may require paid tier)
- Recommended: Separate 2 GB instance
- **Monthly Cost**: $0 if fits in free tier, otherwise $10-15/month

### 3. Trade Data Storage

#### Time-Series Database for Trade History

**Options**:

##### Option A: SQLite (⭐ FREE & Simplest)
```python
# No server required! Just a file.
# Already works in your workspace - zero setup
import sqlite3

conn = sqlite3.connect('/data/trades.db')
# That's it! No hosting, no cost, no configuration.
```

**Pros**:
- **100% FREE** - no server hosting costs
- Zero configuration
- Perfect for single-instance bot
- Easy backups (just copy the file)
- Included in Python standard library
- **Recommended for starting cheap!**

**Cons**:
- Single-writer (fine for one bot)
- Less query optimization than PostgreSQL

**Monthly Cost**: $0

##### Option B: TimescaleDB (PostgreSQL extension)
```sql
CREATE TABLE trades (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity DECIMAL NOT NULL,
    price DECIMAL NOT NULL,
    strategy TEXT,
    pnl DECIMAL,
    metadata JSONB
);

SELECT create_hypertable('trades', 'time');
```

**Pros**:
- SQL interface (familiar)
- Excellent compression
- Free and open-source
- Easy to query and analyze

##### Option B: SQLite with Cloud Sync
```python
# Simple, file-based, sync to cloud storage
import sqlite3

conn = sqlite3.connect('/data/trades.db')
# Periodic backup to S3/GCS/OCI Object Storage
```

**Pros**:
- No additional infrastructure
- Simple and reliable
- Easy backups to object storage

##### Option C: Cloud-Native Database
- **Oracle**: Autonomous JSON Database (free tier)
- **AWS**: DynamoDB (free tier: 25 GB)
- **GCP**: Firestore (free tier: 1 GB)

**Pros**:
- Fully managed
- Auto-scaling
- Built-in backups

### 4. Real-Time Dashboard

#### Dashboard Technology Options

##### Option 1: Grafana (⭐ FREE & Recommended)
- **100% Free and open-source**
- **Features**:
  - Real-time metrics visualization
  - Log viewing (with Loki)
  - Alerting
  - Mobile-friendly
  - Beautiful dashboards out-of-the-box
  - **Runs perfectly on Oracle Cloud Always Free tier**

**Deployment**:
```yaml
# docker-compose.yml addition
services:
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=your_secure_password
    volumes:
      - grafana_data:/var/lib/grafana
```

**Dashboard Panels**:
- Active trades (current positions)
- P&L chart (real-time)
- Win rate and statistics
- Recent log messages
- System health metrics

##### Option 2: Streamlit (Python-based)
```python
# dashboard.py
import streamlit as st
import pandas as pd
from datetime import datetime

st.title("Strategy Lab Live Dashboard")

# Auto-refresh every 10 seconds
st.set_page_config(page_title="Trading Bot", layout="wide")

col1, col2, col3 = st.columns(3)
col1.metric("Total P&L", "$1,234.56", "+5.6%")
col2.metric("Active Trades", "3", "-1")
col3.metric("Win Rate", "68%", "+2%")

# Recent trades table
st.subheader("Recent Trades")
trades_df = load_recent_trades()  # From database
st.dataframe(trades_df, use_container_width=True)

# Live logs
st.subheader("Live Logs")
logs = load_recent_logs()  # Last 100 lines
st.text_area("Logs", logs, height=300)
```

**Deployment**:
```bash
# Run as separate service
streamlit run dashboard.py --server.port 8501
```

**Pros**:
- Python-native (easy to integrate)
- Rapid development
- Interactive widgets
- Beautiful UI out-of-the-box

##### Option 3: Custom Flask + React
- Full control over UI/UX
- WebSocket for real-time updates
- More development effort required

### 5. Alerting System

#### Multi-Channel Alerting

**Alert Types**:
1. **Critical**: System down, connection lost, exception crash
2. **Warning**: High drawdown, unusual trade frequency, API errors
3. **Info**: Market open/close, daily summary, performance milestones

**Notification Channels** (All FREE!):

##### Email (⭐ FREE via Gmail SMTP)
**Monthly Cost**: $0 (500 emails/day limit)
**Setup**: Just use your Gmail account with app password
```python
# src/utils/alerts.py
import smtplib
from email.message import EmailMessage

def send_email_alert(subject, body):
    msg = EmailMessage()
    msg['Subject'] = f"[Trading Bot] {subject}"
    msg['From'] = "alerts@your-bot.com"
    msg['To'] = "your-email@example.com"
    msg.set_content(body)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASSWORD'))
        smtp.send_message(msg)
```

##### Slack Webhook (⭐ 100% FREE)
**Monthly Cost**: $0 (unlimited webhooks)
**Setup**: Create webhook in Slack workspace settings (2 minutes)

```python
import requests

def send_slack_alert(message, level="info"):
    webhook_url = os.getenv('SLACK_WEBHOOK_URL')
    color = {"info": "#36a64f", "warning": "#ff9800", "critical": "#f44336"}

    payload = {
        "attachments": [{
            "color": color.get(level, "#808080"),
            "title": "Trading Bot Alert",
            "text": message,
            "ts": int(time.time())
        }]
    }
    requests.post(webhook_url, json=payload)
```

##### Discord Webhook (⭐ FREE Alternative to SMS)
**Monthly Cost**: $0 (unlimited webhooks)
**Setup**: Similar to Slack, works great on mobile

```python
import requests

def send_discord_alert(message):
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    payload = {
        "content": f"🤖 **Trading Bot Alert**\n{message}"
    }
    requests.post(webhook_url, json=payload)
```

##### SMS (via Twilio - PAID, ~$0.0075/message)
**Only use if you need SMS**. Discord/Slack on mobile is free!

```python
from twilio.rest import Client

def send_sms_alert(message):
    client = Client(
        os.getenv('TWILIO_ACCOUNT_SID'),
        os.getenv('TWILIO_AUTH_TOKEN')
    )
    client.messages.create(
        body=message,
        from_=os.getenv('TWILIO_PHONE'),
        to=os.getenv('YOUR_PHONE')
    )
```

##### PagerDuty (for on-call management)
```python
import pypd

def trigger_pagerduty_incident(description):
    pypd.api_key = os.getenv('PAGERDUTY_API_KEY')
    pypd.Event.create(data={
        'routing_key': os.getenv('PAGERDUTY_ROUTING_KEY'),
        'event_action': 'trigger',
        'payload': {
            'summary': description,
            'severity': 'critical',
            'source': 'trading-bot'
        }
    })
```

---

## Deployment Options

### Option 1: Oracle Cloud Always Free ⭐ (100% FREE - Recommended)

**Why Oracle Cloud?**
- **Truly FREE forever** (not a trial, no credit card expiration)
- **Most generous free tier** of any cloud provider
- Perfect for production trading bot
- Everything you need is included in Always Free tier

**Free Tier Resources**:
- 2 AMD Compute VMs (1/8 OCPU + 1 GB RAM each)
- 4 ARM Ampere A1 cores + 24 GB RAM (shared)
- 200 GB Block Storage
- 10 GB Object Storage
- 10 GB Logging

**Architecture**:
```
VM 1 (ARM, 2 cores + 12 GB RAM):
├─ Trading bot container
├─ Grafana dashboard
└─ Loki log aggregation

VM 2 (AMD, 1 GB RAM) OR Object Storage:
└─ Backup and archive storage

OCI Logging Service:
└─ Real-time log streaming (10 GB/month free)
```

**Monthly Cost**: $0 (within free tier)

### Option 2: AWS (Pay-as-you-go)

**Services Used**:
- EC2 t3.micro instance (or Lambda + ECS Fargate)
- CloudWatch Logs
- RDS PostgreSQL (db.t3.micro)
- S3 for backups
- CloudWatch Alarms

**Architecture**:
```
ECS Fargate (0.25 vCPU, 0.5 GB):
└─ Trading bot container (auto-restart)

CloudWatch Logs:
├─ Real-time log streaming
└─ Log retention (7-30 days)

RDS PostgreSQL (db.t3.micro):
└─ Trade history and metrics

S3 Standard:
└─ Long-term backup storage

CloudWatch + SNS:
└─ Alerting (email, SMS)
```

**Estimated Monthly Cost**: $15-25
- EC2/Fargate: ~$10
- RDS: ~$10
- CloudWatch/S3: ~$2-5

### Option 3: Google Cloud Platform

**Services Used**:
- Cloud Run (containerized app)
- Cloud Logging
- Cloud SQL (PostgreSQL)
- Cloud Storage
- Cloud Monitoring

**Architecture**:
```
Cloud Run:
├─ Serverless container execution
└─ Auto-scaling (min 1 instance during market hours)

Cloud Logging:
└─ 50 GB/month free tier

Cloud SQL PostgreSQL:
└─ db-f1-micro instance

Cloud Storage:
└─ Backup and archive
```

**Estimated Monthly Cost**: $20-30
- Cloud Run: ~$10-15 (always-on instance)
- Cloud SQL: ~$10
- Logging/Storage: ~$2-5

### Option 4: Grafana Cloud (100% FREE Managed Alternative)

**Perfect if you don't want to manage infrastructure at all!**

**Setup**:
- Run trading bot anywhere (Oracle Cloud, local, AWS)
- Send logs to Grafana Cloud (managed Loki)
- Access Grafana Cloud dashboard (managed)
- **Everything is managed for you**

**Free Tier Includes**:
- 50 GB logs/month
- 10k metrics series
- 3 users
- 14-day retention
- **More than enough for trading bot!**

**Architecture**:
```
Your Bot (Oracle Cloud Free or Local):
└─ Trading bot container
    └─ Grafana Agent (FREE log shipper)
         ↓ (HTTPS to Grafana Cloud)
Grafana Cloud (FREE Tier):
├─ Managed Loki (logs)
├─ Managed Prometheus (metrics)
├─ Managed Grafana (dashboards)
└─ Alerting (email, Slack, Discord)
```

**Monthly Cost**: **$0** 🎉
**Link**: https://grafana.com/products/cloud/

### Option 5: Hybrid (Local Bot + Free Cloud Monitoring)

**Setup**:
- Run trading bot locally/on-premises (your PC/home server)
- Stream logs and metrics to Grafana Cloud (free)
- Access cloud dashboard remotely

**Benefits**:
- **$0 cost** - use existing hardware
- Lower latency to local network
- Still get professional cloud monitoring
- No Oracle Cloud account needed

**Monthly Cost**: $0 (using Grafana Cloud free tier)

---

## Real-Time Monitoring Strategy

### Implementation Plan

#### Phase 1: Enhanced Logging (Week 1)

**Objective**: Add structured logging and log shipping to cloud

**Tasks**:
1. **Update logger to support JSON output**
   ```python
   # src/utils/logger.py - add JSON formatter
   import json

   class JSONFormatter(logging.Formatter):
       def format(self, record):
           log_obj = {
               'timestamp': datetime.utcnow().isoformat(),
               'level': record.levelname,
               'logger': record.name,
               'message': record.getMessage(),
               'module': record.module,
               'function': record.funcName,
               'line': record.lineno
           }
           # Add custom fields
           if hasattr(record, 'trade_data'):
               log_obj['trade'] = record.trade_data
           if hasattr(record, 'meta'):
               log_obj['meta'] = record.meta
           return json.dumps(log_obj)
   ```

2. **Add log shipping sidecar**
   ```bash
   # Install Fluent Bit (lightweight log forwarder)
   # docker-compose.yml
   services:
     trading-bot:
       # ... existing config
       logging:
         driver: "fluentd"
         options:
           fluentd-address: localhost:24224

     fluent-bit:
       image: fluent/fluent-bit:latest
       volumes:
         - ./fluent-bit.conf:/fluent-bit/etc/fluent-bit.conf
       ports:
         - "24224:24224"
   ```

3. **Configure cloud log destination**
   ```conf
   # fluent-bit.conf
   [INPUT]
       Name forward
       Port 24224

   [OUTPUT]
       Name cloudwatch_logs
       Match *
       region us-east-1
       log_group_name /strategy-lab/trading-bot
       log_stream_name ${HOSTNAME}
       auto_create_group true
   ```

**Deliverables**:
- ✅ JSON-formatted logs
- ✅ Cloud log streaming
- ✅ Searchable logs in cloud console

#### Phase 2: Trade Database (Week 2)

**Objective**: Store trades in queryable database

**Tasks**:
1. **Set up TimescaleDB or SQLite**
   ```python
   # src/data/trade_store.py
   import sqlite3
   from datetime import datetime

   class TradeStore:
       def __init__(self, db_path='data/trades.db'):
           self.conn = sqlite3.connect(db_path, check_same_thread=False)
           self._create_tables()

       def _create_tables(self):
           self.conn.execute('''
               CREATE TABLE IF NOT EXISTS trades (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   timestamp TEXT NOT NULL,
                   symbol TEXT NOT NULL,
                   side TEXT NOT NULL,
                   quantity REAL NOT NULL,
                   price REAL NOT NULL,
                   strategy TEXT,
                   pnl REAL,
                   metadata TEXT
               )
           ''')
           self.conn.execute('''
               CREATE INDEX IF NOT EXISTS idx_trades_timestamp
               ON trades(timestamp)
           ''')
           self.conn.commit()

       def record_trade(self, symbol, side, quantity, price, strategy, metadata=None):
           self.conn.execute('''
               INSERT INTO trades (timestamp, symbol, side, quantity, price, strategy, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)
           ''', (datetime.utcnow().isoformat(), symbol, side, quantity, price, strategy,
                 json.dumps(metadata) if metadata else None))
           self.conn.commit()

       def get_recent_trades(self, limit=100):
           cursor = self.conn.execute('''
               SELECT * FROM trades
               ORDER BY timestamp DESC
               LIMIT ?
           ''', (limit,))
           return cursor.fetchall()
   ```

2. **Integrate with trading scripts**
   ```python
   # In trading script
   from src.data.trade_store import TradeStore

   trade_store = TradeStore()

   # When trade executes
   trade_store.record_trade(
       symbol='AAPL',
       side='BUY',
       quantity=100,
       price=150.25,
       strategy='ORB',
       metadata={'entry_signal': 'breakout', 'stop_loss': 148.50}
   )
   ```

3. **Set up cloud backup sync**
   ```python
   # scripts/sync_trades_to_cloud.py
   import schedule
   import time

   def backup_to_cloud():
       # Copy trades.db to S3/GCS/OCI Object Storage
       upload_to_cloud('data/trades.db', 'backups/trades_{}.db'.format(
           datetime.now().strftime('%Y%m%d_%H%M%S')
       ))

   schedule.every().day.at("16:30").do(backup_to_cloud)  # After market close

   while True:
       schedule.run_pending()
       time.sleep(60)
   ```

**Deliverables**:
- ✅ Trade history database
- ✅ Automated cloud backups
- ✅ Query API for dashboard

#### Phase 3: Dashboard Setup (Week 3)

**Objective**: Deploy web dashboard for real-time monitoring

**Implementation Choice**: Grafana (recommended) or Streamlit

##### Grafana Setup

1. **Deploy Grafana container**
   ```yaml
   # docker-compose.yml
   services:
     grafana:
       image: grafana/grafana:latest
       container_name: grafana
       ports:
         - "3000:3000"
       environment:
         - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
         - GF_INSTALL_PLUGINS=grafana-clock-panel,grafana-simple-json-datasource
       volumes:
         - grafana_data:/var/lib/grafana
         - ./grafana/dashboards:/etc/grafana/provisioning/dashboards
         - ./grafana/datasources:/etc/grafana/provisioning/datasources
       restart: unless-stopped

   volumes:
     grafana_data:
   ```

2. **Create datasource config**
   ```yaml
   # grafana/datasources/sqlite.yaml
   apiVersion: 1
   datasources:
     - name: Trades
       type: frser-sqlite-datasource
       access: proxy
       jsonData:
         path: /data/trades.db
   ```

3. **Build dashboard JSON**
   ```json
   {
     "dashboard": {
       "title": "Trading Bot Live Monitor",
       "panels": [
         {
           "title": "Today's P&L",
           "type": "stat",
           "targets": [{
             "rawSql": "SELECT SUM(pnl) FROM trades WHERE date(timestamp) = date('now')"
           }]
         },
         {
           "title": "Trades Over Time",
           "type": "graph",
           "targets": [{
             "rawSql": "SELECT timestamp, price FROM trades ORDER BY timestamp"
           }]
         },
         {
           "title": "Recent Trades",
           "type": "table",
           "targets": [{
             "rawSql": "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20"
           }]
         }
       ]
     }
   }
   ```

4. **Access remotely via reverse proxy** (nginx or Cloudflare Tunnel)

##### Streamlit Alternative

```python
# dashboard_app.py
import streamlit as st
import pandas as pd
import plotly.express as px
from src.data.trade_store import TradeStore

st.set_page_config(page_title="Trading Bot Dashboard", layout="wide")

# Auto-refresh every 30 seconds
st.title("🤖 Strategy Lab Trading Bot")

# Metrics row
col1, col2, col3, col4 = st.columns(4)

trade_store = TradeStore()
trades = trade_store.get_recent_trades(1000)
df = pd.DataFrame(trades, columns=['id', 'timestamp', 'symbol', 'side',
                                    'quantity', 'price', 'strategy', 'pnl', 'metadata'])

# Calculate metrics
today_pnl = df[df['timestamp'].str.startswith(datetime.now().strftime('%Y-%m-%d'))]['pnl'].sum()
total_trades = len(df)
win_rate = (df['pnl'] > 0).sum() / len(df) * 100 if len(df) > 0 else 0

col1.metric("Today's P&L", f"${today_pnl:.2f}", f"{today_pnl/10000*100:.2f}%")
col2.metric("Total Trades", total_trades)
col3.metric("Win Rate", f"{win_rate:.1f}%")
col4.metric("Bot Status", "🟢 Running")

# Charts
st.subheader("P&L Over Time")
fig = px.line(df, x='timestamp', y='pnl', title='Cumulative P&L')
st.plotly_chart(fig, use_container_width=True)

# Recent trades table
st.subheader("Recent Trades")
st.dataframe(df.head(20), use_container_width=True)

# Live logs
with st.expander("View Live Logs"):
    log_file = 'logs/trading_bot.log'
    with open(log_file, 'r') as f:
        logs = f.readlines()[-100:]  # Last 100 lines
    st.code(''.join(logs))

# Auto-refresh
time.sleep(30)
st.rerun()
```

**Deployment**:
```bash
streamlit run dashboard_app.py --server.port 8501 --server.address 0.0.0.0
```

**Deliverables**:
- ✅ Real-time dashboard
- ✅ Mobile-accessible interface
- ✅ Trade and P&L visualization

#### Phase 4: Alerting Integration (Week 4)

**Objective**: Set up automated alerts for critical events

**Tasks**:
1. **Create alerting module**
   ```python
   # src/utils/alerting.py
   from enum import Enum
   import os

   class AlertLevel(Enum):
       INFO = "info"
       WARNING = "warning"
       CRITICAL = "critical"

   class AlertManager:
       def __init__(self):
           self.email_enabled = os.getenv('ENABLE_EMAIL_ALERTS', 'false') == 'true'
           self.slack_enabled = os.getenv('ENABLE_SLACK_ALERTS', 'false') == 'true'
           self.sms_enabled = os.getenv('ENABLE_SMS_ALERTS', 'false') == 'true'

       def send_alert(self, message, level=AlertLevel.INFO):
           if level == AlertLevel.CRITICAL:
               # Always send critical alerts via all enabled channels
               if self.email_enabled:
                   self._send_email(message)
               if self.slack_enabled:
                   self._send_slack(message, level)
               if self.sms_enabled:
                   self._send_sms(message)
           elif level == AlertLevel.WARNING:
               # Send warnings via Slack/email only
               if self.email_enabled:
                   self._send_email(message)
               if self.slack_enabled:
                   self._send_slack(message, level)
           else:
               # Info alerts via Slack only
               if self.slack_enabled:
                   self._send_slack(message, level)

       # ... implementation of _send_email, _send_slack, _send_sms
   ```

2. **Integrate into orchestrator**
   ```python
   # orchestrator_main.py - add alerting
   from src.utils.alerting import AlertManager, AlertLevel

   alert_manager = AlertManager()

   # On market open
   alert_manager.send_alert("🔔 Market opened. Bot started.", AlertLevel.INFO)

   # On error
   try:
       # ... trading logic
   except Exception as e:
       alert_manager.send_alert(f"🚨 Critical error: {str(e)}", AlertLevel.CRITICAL)

   # On market close
   daily_summary = f"📊 Market closed. Trades: {num_trades}, P&L: ${pnl:.2f}"
   alert_manager.send_alert(daily_summary, AlertLevel.INFO)
   ```

3. **Configure alert rules**
   ```yaml
   # config/alerts.yaml
   alerts:
     drawdown_threshold: -500  # Alert if daily loss > $500
     trade_frequency_max: 50   # Alert if more than 50 trades/day
     connection_timeout: 300   # Alert if no data for 5 minutes

     channels:
       email:
         enabled: true
         recipients:
           - your-email@example.com
       slack:
         enabled: true
         webhook: ${SLACK_WEBHOOK_URL}
       sms:
         enabled: false  # Only for critical
         phone: ${YOUR_PHONE_NUMBER}
   ```

**Deliverables**:
- ✅ Multi-channel alerting
- ✅ Configurable alert rules
- ✅ Daily summary reports

---

## Implementation Roadmap

### Timeline: 4-6 Weeks

#### Week 1: Foundation & Cloud Setup
- [ ] Choose cloud provider (Oracle/AWS/GCP)
- [ ] Set up cloud account and billing alerts
- [ ] Create VM/container instance
- [ ] Deploy Docker image to cloud
- [ ] Verify bot runs successfully 24/7
- [ ] Set up basic health monitoring

#### Week 2: Logging Infrastructure
- [ ] Implement JSON structured logging
- [ ] Deploy log shipping (Fluent Bit/CloudWatch agent)
- [ ] Configure cloud log destination
- [ ] Test log search and filtering
- [ ] Set up log retention policies (7-30 days)

#### Week 3: Trade Data & Storage
- [ ] Implement trade database (SQLite/TimescaleDB)
- [ ] Integrate trade recording into bot
- [ ] Set up automated cloud backups
- [ ] Create trade query API
- [ ] Test data integrity and recovery

#### Week 4: Dashboard Development
- [ ] Deploy Grafana or Streamlit
- [ ] Create main monitoring dashboard
- [ ] Add real-time metrics panels
- [ ] Configure auto-refresh
- [ ] Set up secure remote access (HTTPS, auth)

#### Week 5: Alerting & Notifications
- [ ] Implement alerting module
- [ ] Configure Slack/email/SMS channels
- [ ] Define alert rules and thresholds
- [ ] Test critical alert delivery
- [ ] Set up daily summary reports

#### Week 6: Testing & Optimization
- [ ] Load testing (simulate market day)
- [ ] Disaster recovery drill (kill instance, verify auto-restart)
- [ ] Mobile access testing
- [ ] Performance optimization
- [ ] Documentation and runbook creation

---

## Cost Analysis

### Oracle Cloud Always Free (Recommended Start)

| Component | Resource | Cost |
|-----------|----------|------|
| Compute | ARM A1 (2 cores, 12 GB RAM) | $0 |
| Storage | 200 GB Block Storage | $0 |
| Logging | 10 GB/month | $0 |
| Object Storage | 10 GB | $0 |
| Bandwidth | 10 TB/month | $0 |
| **Total** | | **$0/month** |

**Limitations**:
- Subject to Always Free tier limits
- No SLA guarantees
- Limited to specific regions

### AWS (Small Production)

| Component | Service | Monthly Cost |
|-----------|---------|--------------|
| Compute | ECS Fargate (0.25 vCPU, 0.5 GB, always-on) | $10 |
| Database | RDS PostgreSQL (db.t3.micro) | $12 |
| Logging | CloudWatch Logs (5 GB/month) | $2.50 |
| Storage | S3 Standard (10 GB) | $0.23 |
| Monitoring | CloudWatch metrics + alarms | $1 |
| Bandwidth | Minimal outbound (<1 GB) | $0.09 |
| **Total** | | **~$26/month** |

### Google Cloud Platform

| Component | Service | Monthly Cost |
|-----------|---------|--------------|
| Compute | Cloud Run (always-on 1 instance) | $12 |
| Database | Cloud SQL PostgreSQL (db-f1-micro) | $10 |
| Logging | Cloud Logging (within free tier) | $0 |
| Storage | Cloud Storage (10 GB) | $0.26 |
| Monitoring | Cloud Monitoring (basic) | $0 |
| **Total** | | **~$22/month** |

### Hybrid (Local Bot + Cloud Monitoring)

| Component | Service | Monthly Cost |
|-----------|---------|--------------|
| Compute | Local machine (no cloud compute) | $0 |
| Monitoring | Grafana Cloud (free tier) | $0 |
| Logging | Grafana Loki (free tier, 50 GB) | $0 |
| Alerts | Email (SMTP) | $0 |
| Backup | Google Drive (15 GB free) | $0 |
| **Total** | | **$0/month** |

**Best for**: Testing, learning, or if you have reliable local infrastructure

---

## Security Considerations

### 1. Secrets Management

**Never hardcode credentials!**

```bash
# Use environment variables
export FINNHUB_API_KEY="your_key_here"
export DATABASE_URL="postgresql://user:pass@host/db"

# Or use cloud secrets manager
# AWS Secrets Manager, GCP Secret Manager, OCI Vault
```

**Docker secrets**:
```yaml
# docker-compose.yml
services:
  trading-bot:
    environment:
      - FINNHUB_API_KEY=${FINNHUB_API_KEY}
    secrets:
      - db_password

secrets:
  db_password:
    file: ./secrets/db_password.txt
```

### 2. Network Security

- **Restrict inbound traffic**: Only allow HTTPS (443) and SSH (22) from your IP
- **Use VPN**: Connect to dashboard via VPN or Cloudflare Tunnel
- **Enable firewall**: Cloud provider firewall + instance firewall (ufw/iptables)

```bash
# Example: Oracle Cloud security list
# Allow inbound:
# - 443 (HTTPS) from 0.0.0.0/0 (for dashboard)
# - 22 (SSH) from YOUR_IP/32 only
# - Deny all other inbound

# Allow outbound:
# - 443 (HTTPS) for Finnhub, APIs
# - 53 (DNS)
# - Deny all other outbound
```

### 3. Authentication

**Dashboard authentication**:
```python
# Grafana: Built-in user management
# Streamlit: Use streamlit-authenticator

import streamlit as st
import streamlit_authenticator as stauth

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

name, authentication_status, username = authenticator.login('Login', 'main')

if authentication_status:
    # Show dashboard
    st.write(f'Welcome {name}')
else:
    st.error('Username/password is incorrect')
```

### 4. Audit Logging

Track all access and changes:
```python
# src/utils/audit.py
import logging

audit_logger = logging.getLogger('audit')

def log_access(user, action, resource):
    audit_logger.info(f"User {user} performed {action} on {resource}")

# Usage:
log_access('admin', 'VIEW_DASHBOARD', 'trades_table')
log_access('admin', 'MANUAL_TRADE', 'AAPL_BUY')
```

### 5. Rate Limiting

Protect APIs from abuse:
```python
from flask_limiter import Limiter

limiter = Limiter(
    app,
    key_func=lambda: request.remote_addr,
    default_limits=["200 per day", "50 per hour"]
)

@app.route("/api/trades")
@limiter.limit("10 per minute")
def get_trades():
    # ...
```

---

## Disaster Recovery

### Backup Strategy

#### 1. Database Backups
- **Frequency**: Daily after market close (4:30 PM ET)
- **Retention**: 30 days
- **Location**: Cloud object storage (S3/GCS/OCI)

```bash
# Automated backup script
#!/bin/bash
DATE=$(date +%Y%m%d)
sqlite3 /data/trades.db ".backup /tmp/trades_${DATE}.db"
# Upload to cloud
aws s3 cp /tmp/trades_${DATE}.db s3://strategy-lab-backups/trades_${DATE}.db
# Keep local copy for 7 days
find /backups -name "trades_*.db" -mtime +7 -delete
```

#### 2. Configuration Backups
- **Frequency**: On every change
- **Location**: Git repository (private)

```bash
# Commit and push configs
git add config/
git commit -m "Update trading parameters"
git push origin main
```

#### 3. Log Archival
- **Frequency**: Weekly
- **Retention**: 90 days in cold storage
- **Location**: Compressed archives in object storage

```bash
# Archive old logs
tar -czf logs_$(date +%Y%m%d).tar.gz logs/
aws s3 cp logs_$(date +%Y%m%d).tar.gz s3://strategy-lab-backups/archives/
```

### Recovery Procedures

#### Scenario 1: Instance Failure

**Detection**: Health check fails, alerting fires

**Recovery**:
1. Cloud auto-restart should handle (wait 2-5 minutes)
2. If not recovered, manually restart instance via console
3. Verify bot reconnects and resumes trading
4. Check trade integrity (no duplicate orders)

**RTO (Recovery Time Objective)**: 5 minutes
**RPO (Recovery Point Objective)**: 0 (no data loss, logs buffered)

#### Scenario 2: Database Corruption

**Detection**: SQLite errors, query failures

**Recovery**:
1. Stop trading bot
2. Restore from latest backup:
   ```bash
   aws s3 cp s3://strategy-lab-backups/trades_$(date -d yesterday +%Y%m%d).db /data/trades.db
   ```
3. Replay today's trades from logs (if needed)
4. Restart bot

**RTO**: 15 minutes
**RPO**: 1 day (last backup)

#### Scenario 3: Cloud Provider Outage

**Detection**: Cannot access dashboard or instance

**Recovery**:
1. Check cloud provider status page
2. If multi-hour outage, switch to backup provider:
   - Pull Docker image from registry
   - Deploy to secondary cloud (pre-configured)
   - Update DNS/access URLs
3. Resume monitoring

**RTO**: 30-60 minutes
**RPO**: Depends on log shipping lag (typically < 1 minute)

### High Availability Options (Advanced)

#### Multi-Region Deployment
```
Primary Region (us-east-1):
├─ Main trading bot instance
└─ Hot standby (stopped)

Secondary Region (us-west-2):
└─ Cold standby (image ready)

Failover:
If primary unhealthy > 5 minutes:
  → Start hot standby in same region
  → If region down, deploy to secondary
```

#### Active-Passive Setup
- **Active**: Primary instance running
- **Passive**: Standby instance monitoring (not trading)
- **Failover**: Manual or automated (Route53 health checks + Lambda)

---

## Next Steps

### Immediate Actions (This Week)

1. **Choose cloud provider**
   - Recommended: Oracle Cloud (free) for testing
   - Consider: AWS/GCP if you need better SLA

2. **Set up basic deployment**
   - Follow existing [ORACLE_CLOUD_DEPLOYMENT.md](ORACLE_CLOUD_DEPLOYMENT.md) or [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)
   - Verify bot runs successfully

3. **Enable basic logging**
   - Configure cloud logging service
   - Test log viewing in cloud console

### Month 1 Goals

- ✅ Bot running 24/7 in cloud
- ✅ Logs searchable in cloud dashboard
- ✅ Basic trade database operational
- ✅ Email alerts for critical events
- ✅ Can access logs remotely

### Month 2 Goals

- ✅ Real-time dashboard deployed (Grafana/Streamlit)
- ✅ Multi-channel alerting (Slack + Email)
- ✅ Automated backups to cloud storage
- ✅ Mobile-friendly monitoring
- ✅ Performance metrics tracked

### Month 3+ Goals

- ✅ Advanced analytics and reporting
- ✅ Multi-strategy support
- ✅ High availability setup
- ✅ Cost optimization
- ✅ Integration with additional data sources

---

## Appendix

### A. Reference Architecture Diagrams

*See detailed diagrams in [architecture-diagrams/](./architecture-diagrams/) folder (to be created)*

### B. Configuration Templates

#### docker-compose.yml (Full Stack)
```yaml
version: '3.8'

services:
  trading-bot:
    build:
      context: ..
      dockerfile: python/Dockerfile
    container_name: strategy-lab-bot
    restart: unless-stopped
    environment:
      - FINNHUB_API_KEY=${FINNHUB_API_KEY}
      - LOG_LEVEL=INFO
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    depends_on:
      - timescaledb
    logging:
      driver: fluentd
      options:
        fluentd-address: localhost:24224
        tag: trading-bot

  timescaledb:
    image: timescale/timescaledb:latest-pg14
    container_name: timescaledb
    restart: unless-stopped
    environment:
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      - POSTGRES_DB=trading
    volumes:
      - db_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
      - GF_INSTALL_PLUGINS=grafana-clock-panel
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./grafana/datasources:/etc/grafana/provisioning/datasources

  loki:
    image: grafana/loki:latest
    container_name: loki
    restart: unless-stopped
    ports:
      - "3100:3100"
    volumes:
      - loki_data:/loki
      - ./loki-config.yaml:/etc/loki/local-config.yaml

  fluent-bit:
    image: fluent/fluent-bit:latest
    container_name: fluent-bit
    restart: unless-stopped
    ports:
      - "24224:24224"
    volumes:
      - ./fluent-bit.conf:/fluent-bit/etc/fluent-bit.conf
      - ./logs:/logs:ro

volumes:
  db_data:
  grafana_data:
  loki_data:
```

#### .env.template
```bash
# API Keys
FINNHUB_API_KEY=your_finnhub_key_here

# Database
DB_PASSWORD=your_secure_db_password
DATABASE_URL=postgresql://postgres:${DB_PASSWORD}@timescaledb:5432/trading

# Grafana
GRAFANA_PASSWORD=your_secure_grafana_password

# Alerts
ENABLE_EMAIL_ALERTS=true
EMAIL_USER=alerts@yourdomain.com
EMAIL_PASSWORD=your_email_app_password

ENABLE_SLACK_ALERTS=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

ENABLE_SMS_ALERTS=false
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_PHONE=+15551234567
YOUR_PHONE=+15559876543

# Cloud Storage
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
S3_BACKUP_BUCKET=strategy-lab-backups

# Google Drive (optional)
GOOGLE_SERVICE_ACCOUNT_KEY=path/to/service-account-key.json
GOOGLE_DRIVE_ROOT_FOLDER_ID=your_folder_id
```

### C. Useful Commands

```bash
# View live logs
docker logs -f strategy-lab-bot

# Access Grafana
open http://localhost:3000
# Default credentials: admin / your_password

# Backup database
docker exec timescaledb pg_dump -U postgres trading > backup_$(date +%Y%m%d).sql

# Restore database
docker exec -i timescaledb psql -U postgres trading < backup_20260110.sql

# SSH to cloud instance
ssh -i ~/.ssh/id_rsa ubuntu@your-instance-ip

# Check container stats
docker stats

# Restart everything
docker-compose down && docker-compose up -d

# View Slack webhook test
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"Test alert from trading bot"}' \
  $SLACK_WEBHOOK_URL
```

### D. Troubleshooting Guide

| Issue | Possible Cause | Solution |
|-------|---------------|----------|
| Bot not starting | Missing env variables | Check `.env` file, ensure all required vars set |
| Cannot access dashboard | Firewall blocking port | Open port 3000 in cloud security group |
| Logs not appearing | Log shipper not running | Restart fluent-bit container |
| Alerts not sending | Wrong webhook URL | Verify SLACK_WEBHOOK_URL in .env |
| High latency | Insufficient resources | Upgrade instance size or optimize queries |
| Database connection errors | TimescaleDB not ready | Check `docker ps`, ensure DB is healthy |

### E. Additional Resources

- [Oracle Cloud Documentation](https://docs.oracle.com/en-us/iaas/Content/home.htm)
- [Grafana Documentation](https://grafana.com/docs/)
- [Docker Compose Reference](https://docs.docker.com/compose/)
- [TimescaleDB Guides](https://docs.timescale.com/)
- [Fluent Bit Documentation](https://docs.fluentbit.io/)

---

**Document Maintainer**: Strategy Lab Team
**Last Review**: January 10, 2026
**Next Review**: April 10, 2026 (quarterly)

---

## Feedback & Updates

This is a living document. Please submit updates via:
- GitHub Issues
- Pull Requests
- Team discussions

For questions or clarifications, contact the infrastructure team.
