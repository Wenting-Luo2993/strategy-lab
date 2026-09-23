"""Tests for Task 0.1: Shared Project Structure Setup."""

import sys
import importlib


def test_vibe_common_import():
    """Test that vibe.common can be imported."""
    import vibe.common
    assert vibe.common is not None


def test_all_subdirectories_importable():
    """Test that all subdirectories can be imported."""
    submodules = [
        "vibe.common.models",
        "vibe.common.execution",
        "vibe.common.data",
        "vibe.common.clock",
        "vibe.common.strategies",
        "vibe.common.indicators",
        "vibe.common.risk",
        "vibe.common.validation",
        "vibe.common.utils",
    ]

    for module_name in submodules:
        module = importlib.import_module(module_name)
        assert module is not None, f"Failed to import {module_name}"


def test_no_circular_dependencies():
    """Test that modules can be imported without circular dependency errors."""
    # Import all modules and verify no import errors
    modules_to_test = [
        "vibe.common.models.bar",
        "vibe.common.models.order",
        "vibe.common.models.position",
        "vibe.common.models.trade",
        "vibe.common.models.signal",
        "vibe.common.models.account",
        "vibe.common.execution.base",
        "vibe.common.data.base",
        "vibe.common.clock.base",
        "vibe.common.clock.live_clock",
        "vibe.common.clock.market_hours",
    ]

    for module_name in modules_to_test:
        try:
            module = importlib.import_module(module_name)
            assert module is not None
        except ImportError as e:
            raise AssertionError(f"Circular dependency or import error in {module_name}: {e}")


def test_imports_from_entry_points():
    """Test importing from multiple entry points."""
    import vibe

    original_common = getattr(vibe, "common", None)
    original_modules = {
        name: module
        for name, module in sys.modules.items()
        if name.startswith("vibe.common")
    }
    try:
        for name in original_modules:
            del sys.modules[name]

        from vibe.common.models import Bar, Order, Trade
        from vibe.common.execution import ExecutionEngine
        from vibe.common.data import DataProvider
        from vibe.common.clock import Clock, LiveClock

        assert Bar is not None
        assert Order is not None
        assert Trade is not None
        assert ExecutionEngine is not None
        assert DataProvider is not None
        assert Clock is not None
        assert LiveClock is not None
    finally:
        for name in list(sys.modules):
            if name.startswith("vibe.common"):
                del sys.modules[name]
        sys.modules.update(original_modules)
        if original_common is not None:
            vibe.common = original_common
