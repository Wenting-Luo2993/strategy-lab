"""Plain-text rendering of the robustness, power and dollar summary blocks."""

from __future__ import annotations

from typing import Any, Mapping


def _f(value: Any, digits: int = 4, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.{digits}f}{suffix}"


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _usd_ci(block: Mapping[str, Any] | None, key: str = "mean_usd") -> str:
    if not block:
        return "n/a"
    return (
        f"${_f(block.get(key), 2)} [{_f(block.get('ci_low_usd'), 2)}, "
        f"{_f(block.get('ci_high_usd'), 2)}] (n={block.get('n', block.get('n_sessions'))})"
    )


def power_lines(power: Mapping[str, Any] | None, unit: str = "R") -> list[str]:
    if not power:
        return ["Power: n/a"]
    lines = [
        f"Power (se={_f(power['se_r'])}{unit}, alpha={power['alpha']:.3f}, "
        f"deflation={power['deflation']}):",
        f"  {'edge':>8}  {'non-zero':>9}  {'deflated':>9}  {'supported':>9}  {'deflated':>9}",
    ]
    for row in power["rows"]:
        lines.append(
            f"  {_f(row['edge_r'], 4):>8}  {_pct(row['power_nonzero']):>9}  "
            f"{_pct(row['power_nonzero_deflated']):>9}  {_pct(row['power_supported']):>9}  "
            f"{_pct(row['power_supported_deflated']):>9}"
        )
    if power.get("excluded_edge_r") is not None:
        lines.append(
            f"  One-sided {1 - power['alpha'] / 2:.1%}: true edges >= "
            f"{_f(power['excluded_edge_r'])}{unit} excluded "
            f"(deflated: >= {_f(power['excluded_edge_deflated_r'])}{unit})"
        )
    return lines


def robustness_lines(rob: Mapping[str, Any] | None) -> list[str]:
    if not rob:
        return []
    days = ", ".join(f"top{k}={_f(v)}" for k, v in rob["mean_without_top_days_r"].items())
    trades = ", ".join(f"top{k}={_f(v)}" for k, v in rob["mean_without_top_trades_r"].items())
    return [
        "Robustness:",
        f"  median={_f(rob['median_r'])}R  trim1%={_f(rob['trimmed_mean_1pct_r'])}R  "
        f"trim5%={_f(rob['trimmed_mean_5pct_r'])}R  skew={_f(rob['skew'], 2)}  "
        f"win rate={_pct(rob['win_rate'])}",
        f"  mean without top days: {days}",
        f"  mean without top trades: {trades}",
    ]


def dollar_lines(dollars: Mapping[str, Any] | None, label: str = "Dollars") -> list[str]:
    if not dollars:
        return []
    sizing = dollars.get("sizing") or {}
    return [
        f"{label}:",
        f"  $/trade={_usd_ci(dollars.get('per_trade'))}",
        f"  $/symbol-session={_usd_ci(dollars.get('per_symbol_session'))}",
        f"  mean bps of notional={_f(dollars.get('mean_bps_of_notional'), 2)}  "
        f"ROE/yr={_pct(dollars.get('return_on_equity_per_year'))}  "
        f"total=${_f(dollars.get('total_net_pnl_usd'), 0)}",
        f"  median $ risk={_f(dollars.get('median_initial_risk_usd'), 0)}  "
        f"capped by buying power={_pct(sizing.get('share_capped_by_buying_power'))} "
        f"({sizing.get('orders_capped_by_buying_power')}/{sizing.get('n_trades')})",
    ]


def fixed_rule_report(summary: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines += robustness_lines(summary.get("robustness"))
    lines += power_lines(summary.get("power"))
    lines += dollar_lines(summary.get("dollars"))
    return "\n".join(lines)


def paired_report(summary: Mapping[str, Any]) -> str:
    lines: list[str] = []
    diff = summary.get("difference_usd")
    if diff:
        lines.append(f"Variant - baseline $/symbol-session: {_usd_ci(diff, 'mean_diff_usd')}")
    lines += power_lines(summary.get("power_per_session"), unit="R/session")
    for name in ("baseline", "variant"):
        lines += dollar_lines((summary.get(name) or {}).get("dollars"), label=name.title())
    return "\n".join(lines)
