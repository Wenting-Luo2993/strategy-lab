import sys
from pathlib import Path
import pytest

"""Pytest configuration utilities and shared fixtures."""

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.utils import MockRiskManager, build_three_market_data  # noqa: E402
from src.core.trade_manager import TradeManager  # noqa: E402


@pytest.fixture(scope="session")
def market_data_sets():
    """Provide pre-generated market data slices for bull, bear, and sideways regimes."""
    return build_three_market_data()


@pytest.fixture()
def risk_manager():
    return MockRiskManager()


@pytest.fixture()
def trade_manager(risk_manager):
    return TradeManager(risk_manager=risk_manager, initial_capital=10000)
