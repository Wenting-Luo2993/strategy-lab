import sys
from pathlib import Path
import os
import json
import subprocess
import pytest
import pandas as pd

"""Pytest configuration utilities and shared fixtures."""

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.utils import MockRiskManager, build_three_market_data  # noqa: E402
from src.core.trade_manager import TradeManager  # noqa: E402
from src.utils.snapshot_core import compare_snapshots, hash_config  # noqa: E402
from src.utils.snapshot_utils import normalize_numeric  # noqa: E402


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


def pytest_addoption(parser):
    group = parser.getgroup("snapshots")
    group.addoption("--update-snapshots", action="store_true", dest="update_snapshots", help="Regenerate existing snapshots")
    group.addoption("--auto-create-snapshots", action="store_true", dest="auto_create_snapshots", help="Create missing snapshots")
    group.addoption("--snapshot-visualize", action="store_true", dest="snapshot_visualize", help="Generate HTML diff artifacts for mismatches")
    group.addoption("--snapshot-prune", action="store_true", dest="snapshot_prune", help="List stale snapshot files not touched this session")


def _bool_env(name: str) -> bool:
    val = os.getenv(name)
    if val is None:
        return False
    return val.lower() in {"1", "true", "yes", "on"}


def _get_commit_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "UNKNOWN"


def pytest_configure(config):
    config._snapshot_touched = set()


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    prune_flag = config.getoption("snapshot_prune") or _bool_env("SNAPSHOT_PRUNE")
    if not prune_flag:
        return
    snapshot_root = Path(os.getenv("SNAPSHOT_ROOT", "python/tests/snapshots")).resolve()
    if not snapshot_root.exists():
        session.config.warn("C1", f"Snapshot root {snapshot_root} does not exist for prune listing.")
        return
    all_snapshot_files = list(snapshot_root.glob("*.csv"))
    touched = getattr(config, "_snapshot_touched", set())
    stale = [p for p in all_snapshot_files if p.name not in touched]
    if stale:
        print("\n[Snapshot Prune] Stale snapshot files (not touched this run):")
        for p in stale:
            print(f"  - {p}")
    else:
        print("\n[Snapshot Prune] No stale snapshot files detected.")


@pytest.fixture
def assert_snapshot(request):
    """Return a function to assert a DataFrame matches (or creates/updates) a stored snapshot.

    Environment variable overrides (take precedence over CLI flags):
      SNAPSHOT_UPDATE=1, SNAPSHOT_AUTO_CREATE=1, SNAPSHOT_VISUALIZE=1
      SNAPSHOT_ROOT=custom/path
    """
    # Flags should be re-evaluated each invocation to reflect monkeypatched env during test runtime.
    def flag_update():
        return request.config.getoption("update_snapshots") or _bool_env("SNAPSHOT_UPDATE")
    def flag_auto_create():
        return request.config.getoption("auto_create_snapshots") or _bool_env("SNAPSHOT_AUTO_CREATE")
    def flag_visualize():
        return request.config.getoption("snapshot_visualize") or _bool_env("SNAPSHOT_VISUALIZE")
    def _snapshot_root():
        return Path(os.getenv("SNAPSHOT_ROOT", "python/tests/snapshots")).resolve()
    snapshot_root = _snapshot_root()
    snapshot_root.mkdir(parents=True, exist_ok=True)

    def _assert(df: pd.DataFrame, name: str, kind: str = "signals", strategy_config: dict | None = None, risk_config: dict | None = None, extra_config: dict | None = None):
        # Recompute snapshot root each invocation to honor runtime env changes
        snapshot_root_local = _snapshot_root()
        snapshot_root_local.mkdir(parents=True, exist_ok=True)
        # Normalize numeric for stable comparisons
        work_df = normalize_numeric(df.copy())
        filename = f"{kind}__{name}.snapshot.csv"
        path = snapshot_root_local / filename
        metadata_path = snapshot_root_local / f"{kind}__{name}.metadata.json"
        # Compute config hash for traceability
        cfgs = [c for c in (strategy_config, risk_config, extra_config) if c]
        config_hash = hash_config(*cfgs) if cfgs else None
        # Create if missing
        if not path.exists():
            if flag_auto_create():
                work_df.to_csv(path, index=True)
                meta = {
                    "name": name,
                    "kind": kind,
                    "created_at": request.node.startdir if hasattr(request.node, "startdir") else None,
                    "commit": _get_commit_hash(),
                    "config_hash": config_hash,
                    "rows": len(work_df),
                }
                metadata_path.write_text(json.dumps(meta, indent=2))
                request.config._snapshot_touched.add(filename)
                return
            raise AssertionError(f"Snapshot missing: {path}. Run with --auto-create-snapshots or set SNAPSHOT_AUTO_CREATE=1 to create.")
        # Load expected
        expected_df = pd.read_csv(path, index_col=0, parse_dates=True)
        diff = compare_snapshots(expected_df, work_df)
        request.config._snapshot_touched.add(filename)
        if diff.is_equal:
            return
        if flag_update():
            work_df.to_csv(path, index=True)
            # Update metadata commit & hash
            meta = {
                "name": name,
                "kind": kind,
                "updated_at": request.node.startdir if hasattr(request.node, "startdir") else None,
                "commit": _get_commit_hash(),
                "config_hash": config_hash,
                "rows": len(work_df),
            }
            metadata_path.write_text(json.dumps(meta, indent=2))
            return
        # Visualization artifact if requested
        if flag_visualize():
            html_dir = snapshot_root_local / "html"
            html_dir.mkdir(exist_ok=True)
            html_path = html_dir / f"{kind}__{name}.diff.html"
            rows = diff.differing_cells
            html_parts = ["<html><body>", f"<h3>Snapshot Diff: {kind}/{name}</h3>"]
            html_parts.append(f"<p>Rows expected={diff.expected_rows} actual={diff.actual_rows}</p>")
            if rows:
                html_parts.append("<table border='1'><tr><th>RowIndex</th><th>Column</th><th>Expected</th><th>Actual</th><th>AbsDiff</th></tr>")
                for r in rows[:50]:
                    html_parts.append(f"<tr><td>{r['row_index']}</td><td>{r['column']}</td><td>{r['expected']}</td><td>{r['actual']}</td><td>{r['abs_diff']}</td></tr>")
                html_parts.append("</table>")
            else:
                html_parts.append("<p>No differing rows beyond tolerance.</p>")
            html_parts.append("</body></html>")
            html_path.write_text("\n".join(html_parts))
        report = f"Snapshot mismatch for {kind}/{name}. Run with --update-snapshots to regenerate."
        raise AssertionError(report)

    return _assert
