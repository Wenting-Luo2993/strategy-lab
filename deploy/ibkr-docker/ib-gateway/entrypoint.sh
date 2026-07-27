#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:1}"
export TWS_MAJOR_VRSN="${TWS_MAJOR_VRSN:-1045}"
export IBC_INI="${IBC_INI:-/etc/ibkr/config.ini}"
export TRADING_MODE="${TRADING_MODE:-paper}"
export TWOFA_TIMEOUT_ACTION="${TWOFA_TIMEOUT_ACTION:-exit}"
export IBC_PATH="${IBC_PATH:-/opt/IBC}"
export TWS_PATH="${TWS_PATH:-/opt/ibkr}"
export TWS_SETTINGS_PATH="${TWS_SETTINGS_PATH:-/opt/ibkr/ibgateway}"
export LOG_PATH="${LOG_PATH:-/var/log/ibkr}"
export APP="${APP:-GATEWAY}"
export JAVA_PATH="${JAVA_PATH:-}"
export TWSUSERID="${TWSUSERID:-}"
export TWSPASSWORD="${TWSPASSWORD:-}"
export FIXUSERID="${FIXUSERID:-}"
export FIXPASSWORD="${FIXPASSWORD:-}"

mkdir -p "$LOG_PATH" "$TWS_PATH" "$TWS_SETTINGS_PATH"

if [ -d "$TWS_SETTINGS_PATH" ] && [ ! -e "$TWS_SETTINGS_PATH/$TWS_MAJOR_VRSN" ]; then
    ln -s "$TWS_SETTINGS_PATH" "$TWS_SETTINGS_PATH/$TWS_MAJOR_VRSN" 2>/dev/null || true
fi

Xvfb "$DISPLAY" -screen 0 "${XVFB_SCREEN:-1280x800x24}" -nolisten tcp >"$LOG_PATH/xvfb.log" 2>"$LOG_PATH/xvfb.err" &

if [ "${ENABLE_VNC:-false}" = "true" ]; then
    x11vnc -display "$DISPLAY" -localhost -forever -shared -rfbport 5901 >"$LOG_PATH/x11vnc.log" 2>"$LOG_PATH/x11vnc.err" &
fi

exec "$IBC_PATH/scripts/displaybannerandlaunch.sh"
