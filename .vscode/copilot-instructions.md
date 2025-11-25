# GitHub Copilot Instructions for Strategy Lab

## Python Code Guidelines

### File Path Resolution

**ALWAYS** use `resolve_workspace_path` from `src.utils.workspace` when constructing file paths in Python code.

- **DO**: `state_path = resolve_workspace_path(f"data_cache/{filename}")`
- **DON'T**: `state_path = Path("data_cache") / filename`

**Rationale**: `resolve_workspace_path` ensures consistent path resolution relative to the Python workspace root, regardless of where the script is executed from. This prevents path resolution errors when running code from different directories.

**Example**:

```python
from src.utils.workspace import resolve_workspace_path

# Resolve relative paths against workspace root
cache_path = resolve_workspace_path("data_cache")
config_path = resolve_workspace_path("config/settings.json")
results_path = resolve_workspace_path("results/backtest")
```

### Cache File Safety

**NEVER** suggest removing cache files (`*_rolling_cache.parquet` or `*_indicators.pkl`) to force recalculation!

- **DON'T**: `Remove-Item data_cache\AAPL_5m.parquet`
- **DON'T**: Delete cache files to trigger fresh fetches

**Rationale**: Cache files contain historical data beyond the max_lookback_days window (default 59 days). Deleting them results in **permanent data loss** of older historical data that cannot be re-fetched from data sources like Yahoo Finance.

**Safe Testing Alternatives**:

- Use a different symbol for testing
- Copy cache files before testing and restore after
- Test with cache-only mode or mock data
- Use dedicated test cache directories

## General Guidelines

- Follow existing code patterns and conventions in the repository
- Write clear, concise docstrings for all public functions
- Use type hints where appropriate
- Keep functions focused and single-purpose
- Add tests for new functionality
