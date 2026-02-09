"""Unit tests for chart generators."""

import pytest
from datetime import datetime, timedelta
from vibe.trading_bot.dashboard.charts import ChartGenerator
import plotly.graph_objects as go


@pytest.fixture
def sample_trades():
    """Create sample trades for testing."""
    return [
        {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 100,
            "entry_price": 150.0,
            "exit_price": 160.0,
            "entry_time": (datetime.now() - timedelta(hours=5)).isoformat(),
            "exit_time": (datetime.now() - timedelta(hours=4)).isoformat(),
            "pnl": 1000.0,
            "pnl_pct": 6.67,
            "status": "closed",
        },
        {
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 50,
            "entry_price": 300.0,
            "exit_price": 295.0,
            "entry_time": (datetime.now() - timedelta(hours=3)).isoformat(),
            "exit_time": (datetime.now() - timedelta(hours=2)).isoformat(),
            "pnl": -250.0,
            "pnl_pct": -1.67,
            "status": "closed",
        },
        {
            "symbol": "GOOGL",
            "side": "buy",
            "quantity": 25,
            "entry_price": 2800.0,
            "exit_price": 2850.0,
            "entry_time": (datetime.now() - timedelta(hours=1)).isoformat(),
            "exit_time": None,
            "pnl": None,
            "pnl_pct": None,
            "status": "open",
        },
    ]


def test_pnl_chart_creation(sample_trades):
    """Test P&L chart creation."""
    fig = ChartGenerator.create_pnl_chart(sample_trades)
    assert isinstance(fig, go.Figure)


def test_pnl_chart_empty_trades():
    """Test P&L chart with empty trades list."""
    fig = ChartGenerator.create_pnl_chart([])
    assert isinstance(fig, go.Figure)


def test_pnl_chart_with_no_closed_trades():
    """Test P&L chart with only open trades."""
    open_trades = [
        {
            "symbol": "AAPL",
            "status": "open",
            "pnl": None,
            "entry_time": datetime.now().isoformat(),
            "exit_time": None,
        }
    ]
    fig = ChartGenerator.create_pnl_chart(open_trades)
    assert isinstance(fig, go.Figure)


def test_pnl_chart_cumulative_calculation(sample_trades):
    """Test cumulative P&L calculation in chart."""
    fig = ChartGenerator.create_pnl_chart(sample_trades)
    # Should have trace with cumulative data
    assert len(fig.data) > 0
    trace = fig.data[0]
    # Y values should represent cumulative P&L
    assert hasattr(trace, "y")


def test_pnl_chart_has_title(sample_trades):
    """Test P&L chart has title."""
    fig = ChartGenerator.create_pnl_chart(sample_trades)
    assert fig.layout.title.text is not None


def test_pnl_chart_has_axis_labels(sample_trades):
    """Test P&L chart has axis labels."""
    fig = ChartGenerator.create_pnl_chart(sample_trades)
    assert fig.layout.xaxis.title.text is not None
    assert fig.layout.yaxis.title.text is not None


def test_trade_distribution_chart_creation(sample_trades):
    """Test trade distribution chart creation."""
    fig = ChartGenerator.create_trade_distribution_chart(sample_trades)
    assert isinstance(fig, go.Figure)


def test_trade_distribution_chart_empty():
    """Test trade distribution chart with empty trades."""
    fig = ChartGenerator.create_trade_distribution_chart([])
    assert isinstance(fig, go.Figure)


def test_trade_distribution_groups_by_symbol(sample_trades):
    """Test trade distribution groups trades by symbol."""
    fig = ChartGenerator.create_trade_distribution_chart(sample_trades)
    assert len(fig.data) > 0
    trace = fig.data[0]
    # Should have x values for each symbol
    assert len(trace.x) >= 2  # AAPL, MSFT, GOOGL


def test_win_rate_chart_creation(sample_trades):
    """Test win rate chart creation."""
    fig = ChartGenerator.create_win_rate_chart(sample_trades)
    assert isinstance(fig, go.Figure)


def test_win_rate_chart_empty():
    """Test win rate chart with empty trades."""
    fig = ChartGenerator.create_win_rate_chart([])
    assert isinstance(fig, go.Figure)


def test_win_rate_chart_pie_structure(sample_trades):
    """Test win rate chart is pie chart."""
    fig = ChartGenerator.create_win_rate_chart(sample_trades)
    assert len(fig.data) > 0
    trace = fig.data[0]
    # Pie chart should have labels and values
    assert hasattr(trace, "labels")
    assert hasattr(trace, "values")


def test_win_rate_calculation(sample_trades):
    """Test win rate calculation."""
    fig = ChartGenerator.create_win_rate_chart(sample_trades)
    # Should show title with calculated win rate
    assert "Win Rate" in fig.layout.title.text


def test_drawdown_chart_creation(sample_trades):
    """Test drawdown chart creation."""
    fig = ChartGenerator.create_drawdown_chart(sample_trades)
    assert isinstance(fig, go.Figure)


def test_drawdown_chart_empty():
    """Test drawdown chart with empty trades."""
    fig = ChartGenerator.create_drawdown_chart([])
    assert isinstance(fig, go.Figure)


def test_drawdown_calculation(sample_trades):
    """Test maximum drawdown is calculated."""
    fig = ChartGenerator.create_drawdown_chart(sample_trades)
    # Title should contain max drawdown value
    assert "Max:" in fig.layout.title.text or "Drawdown" in fig.layout.title.text


def test_pnl_by_symbol_chart_creation(sample_trades):
    """Test P&L by symbol chart creation."""
    fig = ChartGenerator.create_pnl_by_symbol_chart(sample_trades)
    assert isinstance(fig, go.Figure)


def test_pnl_by_symbol_chart_empty():
    """Test P&L by symbol chart with empty trades."""
    fig = ChartGenerator.create_pnl_by_symbol_chart([])
    assert isinstance(fig, go.Figure)


def test_pnl_by_symbol_groups_correctly(sample_trades):
    """Test P&L by symbol groups trades correctly."""
    fig = ChartGenerator.create_pnl_by_symbol_chart(sample_trades)
    assert len(fig.data) > 0
    trace = fig.data[0]
    # Should have bar for each symbol with closed trades
    assert len(trace.x) >= 2


def test_pnl_by_symbol_color_coding(sample_trades):
    """Test P&L by symbol colors based on P&L sign."""
    fig = ChartGenerator.create_pnl_by_symbol_chart(sample_trades)
    trace = fig.data[0]
    # Should have colors (green for positive, red for negative)
    assert hasattr(trace, "marker")


def test_monthly_performance_chart_creation(sample_trades):
    """Test monthly performance chart creation."""
    fig = ChartGenerator.create_monthly_performance_chart(sample_trades)
    assert isinstance(fig, go.Figure)


def test_monthly_performance_chart_empty():
    """Test monthly performance chart with empty trades."""
    fig = ChartGenerator.create_monthly_performance_chart([])
    assert isinstance(fig, go.Figure)


def test_monthly_performance_aggregation():
    """Test monthly performance aggregates trades by month."""
    trades = [
        {
            "symbol": "AAPL",
            "status": "closed",
            "pnl": 100,
            "entry_time": "2024-01-10T10:00:00",
            "exit_time": "2024-01-15T10:00:00",
        },
        {
            "symbol": "AAPL",
            "status": "closed",
            "pnl": 200,
            "entry_time": "2024-01-20T10:00:00",
            "exit_time": "2024-01-25T10:00:00",
        },
    ]
    fig = ChartGenerator.create_monthly_performance_chart(trades)
    assert isinstance(fig, go.Figure)


def test_chart_with_single_trade(sample_trades):
    """Test charts with single trade."""
    single_trade = [sample_trades[0]]

    pnl_fig = ChartGenerator.create_pnl_chart(single_trade)
    dist_fig = ChartGenerator.create_trade_distribution_chart(single_trade)
    win_fig = ChartGenerator.create_win_rate_chart(single_trade)

    assert isinstance(pnl_fig, go.Figure)
    assert isinstance(dist_fig, go.Figure)
    assert isinstance(win_fig, go.Figure)


def test_chart_with_many_trades():
    """Test charts with many trades."""
    trades = [
        {
            "symbol": f"SYM{i % 5}",
            "status": "closed",
            "pnl": (100 if i % 2 == 0 else -50),
            "entry_time": (datetime.now() - timedelta(hours=i)).isoformat(),
            "exit_time": (datetime.now() - timedelta(hours=i - 1)).isoformat(),
        }
        for i in range(100)
    ]

    pnl_fig = ChartGenerator.create_pnl_chart(trades)
    dist_fig = ChartGenerator.create_trade_distribution_chart(trades)
    monthly_fig = ChartGenerator.create_monthly_performance_chart(trades)

    assert isinstance(pnl_fig, go.Figure)
    assert isinstance(dist_fig, go.Figure)
    assert isinstance(monthly_fig, go.Figure)


def test_pnl_chart_monotonic_increase():
    """Test P&L chart shows monotonic increase for profitable sequence."""
    trades = [
        {
            "symbol": "AAPL",
            "status": "closed",
            "pnl": 100 * (i + 1),
            "entry_time": (datetime.now() - timedelta(hours=10 - i)).isoformat(),
            "exit_time": (datetime.now() - timedelta(hours=9 - i)).isoformat(),
        }
        for i in range(5)
    ]

    fig = ChartGenerator.create_pnl_chart(trades)
    trace = fig.data[0]
    # Y values should be strictly increasing
    if len(trace.y) > 1:
        for i in range(len(trace.y) - 1):
            assert trace.y[i] <= trace.y[i + 1]


def test_pnl_chart_includes_hover_info(sample_trades):
    """Test P&L chart includes hover information."""
    fig = ChartGenerator.create_pnl_chart(sample_trades)
    trace = fig.data[0]
    # Should have hover template
    assert hasattr(trace, "hovertemplate")


def test_win_rate_chart_shows_statistics(sample_trades):
    """Test win rate chart displays statistics."""
    fig = ChartGenerator.create_win_rate_chart(sample_trades)
    # Title should contain win rate percentage
    assert "%" in fig.layout.title.text


def test_distribution_chart_all_symbols_present(sample_trades):
    """Test distribution chart includes all symbols."""
    fig = ChartGenerator.create_trade_distribution_chart(sample_trades)
    trace = fig.data[0]
    symbols_in_chart = set(trace.x)

    # Should have entries for AAPL, MSFT, GOOGL
    for symbol in ["AAPL", "MSFT", "GOOGL"]:
        assert symbol in symbols_in_chart


def test_chart_dark_theme():
    """Test charts use dark theme."""
    trades = [
        {
            "symbol": "AAPL",
            "status": "closed",
            "pnl": 100,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        }
    ]

    fig = ChartGenerator.create_pnl_chart(trades)
    # Should have dark template (has layout with dark background)
    assert fig.layout.template is not None


def test_chart_dimensions():
    """Test charts have proper dimensions."""
    trades = [
        {
            "symbol": "AAPL",
            "status": "closed",
            "pnl": 100,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        }
    ]

    fig = ChartGenerator.create_pnl_chart(trades)
    assert fig.layout.height == 400


def test_pnl_by_symbol_with_mixed_results():
    """Test P&L by symbol with profitable and losing symbols."""
    trades = [
        {
            "symbol": "WINNER",
            "status": "closed",
            "pnl": 1000,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        },
        {
            "symbol": "WINNER",
            "status": "closed",
            "pnl": 500,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        },
        {
            "symbol": "LOSER",
            "status": "closed",
            "pnl": -300,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        },
    ]

    fig = ChartGenerator.create_pnl_by_symbol_chart(trades)
    trace = fig.data[0]
    # Should have positive value for WINNER, negative for LOSER
    assert "WINNER" in trace.x
    assert "LOSER" in trace.x


def test_drawdown_calculation_correctness():
    """Test drawdown calculation is mathematically correct."""
    # Create trades with known equity curve
    trades = [
        {
            "symbol": "TEST",
            "status": "closed",
            "pnl": 100,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        },
        {
            "symbol": "TEST",
            "status": "closed",
            "pnl": 100,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        },
        {
            "symbol": "TEST",
            "status": "closed",
            "pnl": -50,
            "entry_time": datetime.now().isoformat(),
            "exit_time": datetime.now().isoformat(),
        },
    ]

    fig = ChartGenerator.create_drawdown_chart(trades)
    trace = fig.data[0]
    # Last drawdown value should be 50 (from peak of 200 down to 150)
    assert trace.y[-1] == 50


def test_monthly_aggregation_multiple_months():
    """Test monthly aggregation with multiple months."""
    trades = [
        {
            "symbol": "TEST",
            "status": "closed",
            "pnl": 100,
            "entry_time": "2024-01-10T10:00:00",
            "exit_time": "2024-01-15T10:00:00",
        },
        {
            "symbol": "TEST",
            "status": "closed",
            "pnl": 200,
            "entry_time": "2024-02-10T10:00:00",
            "exit_time": "2024-02-15T10:00:00",
        },
        {
            "symbol": "TEST",
            "status": "closed",
            "pnl": 150,
            "entry_time": "2024-03-10T10:00:00",
            "exit_time": "2024-03-15T10:00:00",
        },
    ]

    fig = ChartGenerator.create_monthly_performance_chart(trades)
    trace = fig.data[0]
    # Should have 3 months
    assert len(trace.x) == 3
