"""Plotly chart generators for dashboard visualization."""

from typing import List, Dict, Any, Optional
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd


class ChartGenerator:
    """Generates interactive charts for trading dashboard."""

    @staticmethod
    def create_pnl_chart(trades: List[Dict[str, Any]]) -> go.Figure:
        """Create cumulative P&L chart.

        Args:
            trades: List of trade dictionaries with pnl field

        Returns:
            Plotly figure object
        """
        if not trades:
            # Return empty chart
            fig = go.Figure()
            fig.add_annotation(
                text="No trades data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        # Filter closed trades and calculate cumulative P&L
        closed_trades = [t for t in trades if t.get("status") == "closed" and t.get("pnl")]
        if not closed_trades:
            fig = go.Figure()
            fig.add_annotation(
                text="No closed trades data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        # Sort by exit time
        sorted_trades = sorted(
            closed_trades,
            key=lambda t: datetime.fromisoformat(t.get("exit_time", t.get("entry_time"))),
        )

        # Calculate cumulative P&L
        cumulative_pnl = []
        timestamps = []
        cumsum = 0

        for trade in sorted_trades:
            cumsum += trade.get("pnl", 0)
            cumulative_pnl.append(cumsum)
            exit_time = trade.get("exit_time") or trade.get("entry_time")
            timestamps.append(exit_time)

        # Create figure
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=cumulative_pnl,
                mode="lines",
                name="Cumulative P&L",
                line=dict(color="rgba(0, 200, 100, 0.8)", width=2),
                fill="tozeroy",
                fillcolor="rgba(0, 200, 100, 0.1)",
                hovertemplate="<b>%{x}</b><br>P&L: $%{y:.2f}<extra></extra>",
            )
        )

        fig.update_layout(
            title="Cumulative P&L Over Time",
            xaxis_title="Time",
            yaxis_title="Cumulative P&L ($)",
            hovermode="x unified",
            template="plotly_dark",
            height=400,
        )

        return fig

    @staticmethod
    def create_trade_distribution_chart(trades: List[Dict[str, Any]]) -> go.Figure:
        """Create trade distribution by symbol.

        Args:
            trades: List of trade dictionaries

        Returns:
            Plotly figure object
        """
        if not trades:
            fig = go.Figure()
            fig.add_annotation(
                text="No trades data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        # Count trades by symbol
        symbol_counts = {}
        for trade in trades:
            symbol = trade.get("symbol", "UNKNOWN")
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

        symbols = list(symbol_counts.keys())
        counts = list(symbol_counts.values())

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=symbols,
                y=counts,
                marker=dict(color="rgba(100, 150, 255, 0.8)"),
                hovertemplate="<b>%{x}</b><br>Trades: %{y}<extra></extra>",
            )
        )

        fig.update_layout(
            title="Trade Distribution by Symbol",
            xaxis_title="Symbol",
            yaxis_title="Number of Trades",
            hovermode="x",
            template="plotly_dark",
            height=400,
        )

        return fig

    @staticmethod
    def create_win_rate_chart(trades: List[Dict[str, Any]]) -> go.Figure:
        """Create win rate pie chart.

        Args:
            trades: List of trade dictionaries with pnl field

        Returns:
            Plotly figure object
        """
        closed_trades = [t for t in trades if t.get("status") == "closed" and t.get("pnl")]

        if not closed_trades:
            fig = go.Figure()
            fig.add_annotation(
                text="No closed trades data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        winning = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
        losing = sum(1 for t in closed_trades if t.get("pnl", 0) < 0)
        breakeven = sum(1 for t in closed_trades if t.get("pnl", 0) == 0)

        labels = []
        values = []
        if winning > 0:
            labels.append("Winning")
            values.append(winning)
        if losing > 0:
            labels.append("Losing")
            values.append(losing)
        if breakeven > 0:
            labels.append("Break-Even")
            values.append(breakeven)

        fig = go.Figure(
            data=[
                go.Pie(
                    labels=labels,
                    values=values,
                    marker=dict(colors=["#00C864", "#FF3B30", "#FFB800"]),
                    hovertemplate="<b>%{label}</b><br>Trades: %{value}<extra></extra>",
                )
            ]
        )

        fig.update_layout(
            title=f"Win Rate: {winning}/{len(closed_trades)} ({winning/len(closed_trades)*100:.1f}%)",
            template="plotly_dark",
            height=400,
        )

        return fig

    @staticmethod
    def create_drawdown_chart(trades: List[Dict[str, Any]]) -> go.Figure:
        """Create maximum drawdown chart.

        Args:
            trades: List of trade dictionaries with pnl field

        Returns:
            Plotly figure object
        """
        closed_trades = [t for t in trades if t.get("status") == "closed" and t.get("pnl")]

        if not closed_trades:
            fig = go.Figure()
            fig.add_annotation(
                text="No closed trades data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        # Sort by exit time
        sorted_trades = sorted(
            closed_trades,
            key=lambda t: datetime.fromisoformat(t.get("exit_time", t.get("entry_time"))),
        )

        # Calculate running maximum and drawdown
        cumulative_pnl = 0
        running_max = 0
        drawdowns = []
        timestamps = []

        for trade in sorted_trades:
            cumulative_pnl += trade.get("pnl", 0)
            running_max = max(running_max, cumulative_pnl)
            drawdown = running_max - cumulative_pnl
            drawdowns.append(drawdown)
            exit_time = trade.get("exit_time") or trade.get("entry_time")
            timestamps.append(exit_time)

        max_drawdown = max(drawdowns) if drawdowns else 0

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=drawdowns,
                mode="lines",
                name="Drawdown",
                line=dict(color="rgba(255, 100, 100, 0.8)", width=2),
                fill="tozeroy",
                fillcolor="rgba(255, 100, 100, 0.1)",
                hovertemplate="<b>%{x}</b><br>Drawdown: $%{y:.2f}<extra></extra>",
            )
        )

        fig.update_layout(
            title=f"Drawdown Over Time (Max: ${max_drawdown:.2f})",
            xaxis_title="Time",
            yaxis_title="Drawdown ($)",
            hovermode="x unified",
            template="plotly_dark",
            height=400,
        )

        return fig

    @staticmethod
    def create_pnl_by_symbol_chart(trades: List[Dict[str, Any]]) -> go.Figure:
        """Create P&L by symbol bar chart.

        Args:
            trades: List of trade dictionaries with pnl field

        Returns:
            Plotly figure object
        """
        closed_trades = [t for t in trades if t.get("status") == "closed" and t.get("pnl")]

        if not closed_trades:
            fig = go.Figure()
            fig.add_annotation(
                text="No closed trades data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        # Aggregate P&L by symbol
        symbol_pnl = {}
        for trade in closed_trades:
            symbol = trade.get("symbol", "UNKNOWN")
            pnl = trade.get("pnl", 0)
            symbol_pnl[symbol] = symbol_pnl.get(symbol, 0) + pnl

        symbols = list(symbol_pnl.keys())
        pnls = list(symbol_pnl.values())

        # Color based on positive/negative P&L
        colors = ["rgba(0, 200, 100, 0.8)" if p > 0 else "rgba(255, 100, 100, 0.8)" for p in pnls]

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=symbols,
                y=pnls,
                marker=dict(color=colors),
                hovertemplate="<b>%{x}</b><br>P&L: $%{y:.2f}<extra></extra>",
            )
        )

        fig.update_layout(
            title="Total P&L by Symbol",
            xaxis_title="Symbol",
            yaxis_title="Total P&L ($)",
            hovermode="x",
            template="plotly_dark",
            height=400,
        )

        return fig

    @staticmethod
    def create_monthly_performance_chart(trades: List[Dict[str, Any]]) -> go.Figure:
        """Create monthly performance summary chart.

        Args:
            trades: List of trade dictionaries

        Returns:
            Plotly figure object
        """
        closed_trades = [t for t in trades if t.get("status") == "closed" and t.get("pnl")]

        if not closed_trades:
            fig = go.Figure()
            fig.add_annotation(
                text="No closed trades data available",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        # Group trades by month
        monthly_data = {}
        for trade in closed_trades:
            exit_time = trade.get("exit_time") or trade.get("entry_time")
            try:
                dt = datetime.fromisoformat(exit_time)
                month_key = dt.strftime("%Y-%m")
                if month_key not in monthly_data:
                    monthly_data[month_key] = {"pnl": 0, "trades": 0}
                monthly_data[month_key]["pnl"] += trade.get("pnl", 0)
                monthly_data[month_key]["trades"] += 1
            except (ValueError, TypeError):
                pass

        months = sorted(monthly_data.keys())
        pnls = [monthly_data[m]["pnl"] for m in months]
        trade_counts = [monthly_data[m]["trades"] for m in months]

        colors = ["rgba(0, 200, 100, 0.8)" if p > 0 else "rgba(255, 100, 100, 0.8)" for p in pnls]

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=months,
                y=pnls,
                marker=dict(color=colors),
                hovertemplate="<b>%{x}</b><br>P&L: $%{y:.2f}<br>Trades: "
                + "<extra></extra>",
            )
        )

        fig.update_layout(
            title="Monthly P&L Performance",
            xaxis_title="Month",
            yaxis_title="P&L ($)",
            hovermode="x",
            template="plotly_dark",
            height=400,
        )

        return fig
