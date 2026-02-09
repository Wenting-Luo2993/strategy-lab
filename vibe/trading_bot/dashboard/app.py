"""Streamlit dashboard application for real-time trading monitoring."""

import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import time
import json
import logging

from vibe.trading_bot.dashboard.charts import ChartGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Streamlit page config
st.set_page_config(
    page_title="Trading Bot Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for styling
st.markdown(
    """
    <style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        margin: 10px 0;
    }
    .positive {
        color: #00c864;
    }
    .negative {
        color: #ff3b30;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_api_base_url() -> str:
    """Get API base URL from Streamlit secrets or config.

    Returns:
        API base URL
    """
    try:
        return st.secrets["api_url"]
    except (FileNotFoundError, KeyError):
        return "http://localhost:8000"


def make_api_request(
    endpoint: str, method: str = "GET", params: Optional[Dict] = None
) -> Optional[Dict[str, Any]]:
    """Make request to dashboard API.

    Args:
        endpoint: API endpoint (e.g., "/api/account")
        method: HTTP method (GET, POST, etc.)
        params: Query parameters

    Returns:
        Response JSON or None if request fails
    """
    try:
        url = f"{get_api_base_url()}{endpoint}"
        response = requests.request(method, url, params=params, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"API Error: {e}")
        logger.error(f"API request failed: {e}")
        return None


def format_currency(value: float, precision: int = 2) -> str:
    """Format value as currency.

    Args:
        value: Numeric value
        precision: Decimal places

    Returns:
        Formatted currency string
    """
    return f"${value:,.{precision}f}"


def format_percentage(value: float, precision: int = 2) -> str:
    """Format value as percentage.

    Args:
        value: Numeric value (0-100)
        precision: Decimal places

    Returns:
        Formatted percentage string
    """
    return f"{value:.{precision}f}%"


def display_account_summary() -> None:
    """Display account summary section."""
    st.subheader("Account Summary")

    account_data = make_api_request("/api/account")
    if not account_data:
        st.warning("Unable to fetch account data")
        return

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Cash Balance",
            format_currency(account_data["cash"]),
            delta=None,
        )

    with col2:
        st.metric(
            "Equity",
            format_currency(account_data["equity"]),
            delta=None,
        )

    with col3:
        pnl = account_data.get("total_pnl", 0)
        st.metric(
            "Total P&L",
            format_currency(pnl),
            delta=format_currency(pnl),
            delta_color="inverse" if pnl < 0 else "normal",
        )

    with col4:
        st.metric(
            "Total Trades",
            int(account_data["total_trades"]),
            delta=None,
        )


def display_performance_metrics() -> None:
    """Display performance metrics section."""
    st.subheader("Performance Metrics")

    metrics_data = make_api_request("/api/metrics/performance")
    if not metrics_data:
        st.warning("Unable to fetch performance metrics")
        return

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        win_rate = metrics_data.get("win_rate", 0)
        st.metric("Win Rate", format_percentage(win_rate), delta=None)

    with col2:
        sharpe = metrics_data.get("sharpe_ratio", 0)
        st.metric("Sharpe Ratio", f"{sharpe:.2f}", delta=None)

    with col3:
        drawdown = metrics_data.get("max_drawdown", 0)
        st.metric(
            "Max Drawdown",
            format_percentage(drawdown),
            delta=None,
            delta_color="inverse",
        )

    with col4:
        avg_duration = metrics_data.get("avg_trade_duration", 0)
        st.metric("Avg Trade Duration", f"{avg_duration:.0f} min", delta=None)


def display_health_status() -> None:
    """Display health status section."""
    st.subheader("System Health")

    health_data = make_api_request("/api/health")
    if not health_data:
        st.warning("Unable to fetch health status")
        return

    col1, col2, col3 = st.columns(3)

    with col1:
        status = health_data.get("status", "unknown").upper()
        status_color = "green" if status == "HEALTHY" else "red"
        st.metric("Status", status, delta=None)

    with col2:
        uptime = health_data.get("uptime_seconds", 0)
        hours = int(uptime / 3600)
        st.metric("Uptime", f"{hours}h", delta=None)

    with col3:
        errors = health_data.get("errors_last_hour", 0)
        st.metric("Errors (1h)", errors, delta=None, delta_color="inverse")

    # Additional health info
    col_db, col_ws = st.columns(2)
    with col_db:
        db_healthy = health_data.get("database_healthy", False)
        st.info(f"✓ Database: {'Healthy' if db_healthy else 'Down'}")

    with col_ws:
        ws_connected = health_data.get("websocket_connected", False)
        st.info(f"✓ WebSocket: {'Connected' if ws_connected else 'Disconnected'}")


def display_open_positions() -> None:
    """Display open positions table."""
    st.subheader("Open Positions")

    positions_data = make_api_request("/api/positions")
    if not positions_data:
        st.info("No open positions")
        return

    if not positions_data:
        st.info("No open positions")
        return

    # Convert to DataFrame for display
    df = pd.DataFrame(positions_data)

    # Format display columns
    display_df = df.copy()
    display_df["Entry Price"] = display_df["entry_price"].apply(lambda x: f"${x:.2f}")
    display_df["Current Price"] = display_df["current_price"].apply(lambda x: f"${x:.2f}")
    display_df["Unrealized P&L"] = display_df["unrealized_pnl"].apply(
        lambda x: f"${x:.2f}"
    )
    display_df["P&L %"] = display_df["unrealized_pnl_pct"].apply(
        lambda x: f"{x:.2f}%"
    )

    # Select columns to display
    display_columns = ["symbol", "quantity", "Entry Price", "Current Price", "Unrealized P&L", "P&L %"]
    st.dataframe(
        display_df[display_columns],
        use_container_width=True,
        hide_index=True,
    )


def display_trades_history() -> None:
    """Display trade history table."""
    st.subheader("Trade History")

    col1, col2 = st.columns([1, 1])
    with col1:
        limit = st.slider("Trades to show", min_value=10, max_value=500, value=50, step=10)
    with col2:
        status_filter = st.selectbox("Filter by status", ["all", "open", "closed"])

    params = {"limit": limit}
    if status_filter != "all":
        params["status"] = status_filter

    trades_data = make_api_request("/api/trades", params=params)
    if not trades_data:
        st.info("No trades found")
        return

    # Convert to DataFrame
    df = pd.DataFrame(trades_data)

    # Format display columns
    display_df = df.copy()
    display_df["Entry Price"] = display_df["entry_price"].apply(lambda x: f"${x:.2f}")
    display_df["Exit Price"] = display_df["exit_price"].apply(
        lambda x: f"${x:.2f}" if x else "N/A"
    )
    display_df["P&L"] = display_df["pnl"].apply(
        lambda x: f"${x:.2f}" if x else "N/A"
    )
    display_df["P&L %"] = display_df["pnl_pct"].apply(
        lambda x: f"{x:.2f}%" if x else "N/A"
    )

    # Prepare columns for display
    display_columns = ["symbol", "side", "quantity", "Entry Price", "Exit Price", "P&L", "P&L %", "status"]
    st.dataframe(
        display_df[display_columns],
        use_container_width=True,
        hide_index=True,
    )


def display_charts() -> None:
    """Display performance charts."""
    st.subheader("Performance Charts")

    # Fetch trades for charting
    trades_data = make_api_request("/api/trades", params={"limit": 1000})
    if not trades_data:
        st.warning("No data available for charts")
        return

    # Create tabs for different charts
    chart_tabs = st.tabs(["P&L Curve", "Trade Distribution", "Win Rate", "Drawdown"])

    with chart_tabs[0]:
        fig_pnl = ChartGenerator.create_pnl_chart(trades_data)
        st.plotly_chart(fig_pnl, use_container_width=True)

    with chart_tabs[1]:
        fig_dist = ChartGenerator.create_trade_distribution_chart(trades_data)
        st.plotly_chart(fig_dist, use_container_width=True)

    with chart_tabs[2]:
        fig_win = ChartGenerator.create_win_rate_chart(trades_data)
        st.plotly_chart(fig_win, use_container_width=True)

    with chart_tabs[3]:
        fig_dd = ChartGenerator.create_drawdown_chart(trades_data)
        st.plotly_chart(fig_dd, use_container_width=True)


def main() -> None:
    """Main Streamlit application."""
    # Title
    st.title("📊 Trading Bot Dashboard")

    # Sidebar configuration
    with st.sidebar:
        st.subheader("Settings")

        # API URL
        api_url = st.text_input("API URL", value=get_api_base_url())

        # Auto-refresh settings
        auto_refresh = st.checkbox("Auto-refresh", value=True)
        if auto_refresh:
            refresh_interval = st.slider(
                "Refresh interval (seconds)",
                min_value=1,
                max_value=60,
                value=5,
            )
        else:
            refresh_interval = None

        # Manual refresh button
        if st.button("🔄 Refresh Now"):
            st.rerun()

        st.divider()

        # Information
        st.info("This dashboard displays real-time trading bot metrics and performance data.")

    # Display main dashboard sections
    display_account_summary()
    st.divider()

    display_performance_metrics()
    st.divider()

    display_health_status()
    st.divider()

    # Positions and trades in columns
    col_pos, col_trades = st.columns([1, 1])
    with col_pos:
        display_open_positions()
    with col_trades:
        display_trades_history()

    st.divider()

    # Charts
    display_charts()

    # Auto-refresh timer
    if auto_refresh and refresh_interval:
        placeholder = st.empty()
        countdown = refresh_interval
        while countdown > 0:
            placeholder.info(f"⏱️ Auto-refresh in {countdown}s")
            time.sleep(1)
            countdown -= 1
        st.rerun()


if __name__ == "__main__":
    main()
