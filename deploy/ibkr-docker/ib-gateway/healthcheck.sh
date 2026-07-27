#!/usr/bin/env bash
set -euo pipefail

port="${IB_GATEWAY_PORT:-4002}"
timeout 2 bash -c "</dev/tcp/127.0.0.1/${port}"
