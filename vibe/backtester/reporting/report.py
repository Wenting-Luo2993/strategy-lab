from pathlib import Path
from typing import Dict

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from vibe.backtester.analysis.metrics import (
    BacktestResult, ConvexityMetrics, EquityMetrics,
)

_SCORECARD = [
    ("Win Rate",         "win_rate",          lambda v: f"{v:.1%}", lambda v: "pass" if 0.25 <= v <= 0.45 else "watch" if v < 0.55 else "fail"),
    ("Avg Win R",        "avg_win_r",         lambda v: f"{v:.2f}R", lambda v: "pass" if v >= 3.0 else "watch" if v >= 2.0 else "fail"),
    ("Avg Loss R",       "avg_loss_r",        lambda v: f"{v:.2f}R", lambda v: "pass" if v >= -1.05 else "watch" if v >= -1.2 else "fail"),
    ("Expectancy",       "expectancy_r",      lambda v: f"{v:.2f}R", lambda v: "pass" if v > 0 else "fail"),
    ("Max Win R",        "max_win_r",         lambda v: f"{v:.1f}R", lambda v: "pass" if v >= 8.0 else "watch" if v >= 5.0 else "fail"),
    ("Top 10%",          "top10_pct",         lambda v: f"{v:.0f}%", lambda v: "pass" if v >= 40.0 else "watch" if v >= 30.0 else "fail"),
    ("Skewness",         "skewness",          lambda v: f"{v:.2f}", lambda v: "pass" if v > 0.5 else "watch" if v > 0 else "fail"),
    ("Max Losing Streak","max_losing_streak", lambda v: str(v), lambda v: "pass" if v <= 10 else "watch" if v <= 15 else "fail"),
]

_STATUS_COLOR = {"pass": "#2ecc71", "watch": "#f39c12", "fail": "#e74c3c"}


class ReportGenerator:
    """Generates the convexity dashboard HTML report using Plotly."""

    def generate_html(self, result: BacktestResult, output_path: Path) -> Path:
        overall_html  = self._render_overall(result.overall, result.equity)
        year_htmls    = "".join(
            self._render_year(year, cm)
            for year, cm in sorted(result.by_year.items())
        )
        regime_html   = self._render_regime(result.regime_breakdown)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{result.ruleset_name} — Convexity Report</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  body {{ font-family: Arial, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }}
  h1 {{ color: #58a6ff; }} h2 {{ color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
  .meta {{ color: #8b949e; font-size: 13px; margin-bottom: 20px; }}
  .grid-8 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }}
  .metric-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; text-align: center; }}
  .metric-label {{ font-size: 12px; color: #8b949e; margin-bottom: 6px; }}
  .metric-value {{ font-size: 24px; font-weight: bold; }}
  .pass {{ color: #2ecc71; }} .watch {{ color: #f39c12; }} .fail {{ color: #e74c3c; }}
  .scorecard {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }}
  .sc-item {{ background: #161b22; border-radius: 6px; padding: 10px 14px; font-size: 13px; }}
  .sc-label {{ color: #8b949e; }} .sc-val {{ font-weight: bold; font-size: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #161b22; color: #8b949e; padding: 8px 12px; text-align: right; }}
  th:first-child {{ text-align: left; }}
  td {{ padding: 6px 12px; border-bottom: 1px solid #21262d; text-align: right; }}
  td:first-child {{ text-align: left; font-weight: bold; }}
  .section {{ margin-bottom: 40px; }}
</style>
</head>
<body>
<h1>{result.ruleset_name} v{result.ruleset_version} — Convexity Dashboard</h1>
<p class="meta">Symbol: {result.symbol} &nbsp;|&nbsp; {result.start_date} to {result.end_date}</p>
{overall_html}
{year_htmls}
{regime_html}
</body>
</html>"""

        output_path.write_text(html, encoding="utf-8")
        return output_path

    def _render_scorecard(self, cm: ConvexityMetrics) -> str:
        items = []
        for label, attr, fmt, grade_fn in _SCORECARD:
            val = getattr(cm, attr)
            grade = grade_fn(val)
            color = _STATUS_COLOR[grade]
            items.append(
                f'<div class="sc-item"><div class="sc-label">{label}</div>'
                f'<div class="sc-val" style="color:{color}">{fmt(val)}</div>'
                f'<div style="font-size:11px;color:{color}">{grade.upper()}</div></div>'
            )
        return f'<div class="scorecard">{"".join(items)}</div>'

    def _render_metrics_grid(self, cm: ConvexityMetrics) -> str:
        cards = [
            ("Trades", str(cm.n_trades), ""),
            ("Win Rate", f"{cm.win_rate:.1%}", "pass" if 0.25 <= cm.win_rate <= 0.45 else ""),
            ("Expectancy", f"{cm.expectancy_r:.2f}R", "pass" if cm.expectancy_r > 0 else "fail"),
            ("Total P&L", f"${cm.total_pnl:,.0f}", "pass" if cm.total_pnl > 0 else "fail"),
            ("Max Win", f"{cm.max_win_r:.1f}R", ""),
            ("Skewness", f"{cm.skewness:.2f}", ""),
            ("Max Streak", str(cm.max_losing_streak), ""),
            ("Top 10%", f"{cm.top10_pct:.0f}%", ""),
        ]
        html = '<div class="grid-8">'
        for label, val, cls in cards:
            html += (f'<div class="metric-card"><div class="metric-label">{label}</div>'
                     f'<div class="metric-value {cls}">{val}</div></div>')
        return html + "</div>"

    def _r_histogram(self, r_multiples: list, div_id: str) -> str:
        if not r_multiples:
            return ""
        colors = ["#2ecc71" if r > 0 else "#e74c3c" for r in r_multiples]
        fig = go.Figure(go.Bar(
            x=list(range(len(r_multiples))), y=r_multiples,
            marker_color=colors, name="R-multiple",
        ))
        fig.update_layout(
            title="Trade R Waterfall", plot_bgcolor="#0d1117",
            paper_bgcolor="#0d1117", font_color="#c9d1d9",
            xaxis_title="Trade #", yaxis_title="R",
        )
        return fig.to_html(full_html=False, div_id=div_id)

    def _cumulative_r_chart(self, r_multiples: list, div_id: str) -> str:
        if not r_multiples:
            return ""
        import numpy as np
        cumr = list(np.cumsum(r_multiples))
        fig = go.Figure(go.Scatter(
            x=list(range(len(cumr))), y=cumr,
            mode="lines", line=dict(color="#58a6ff", width=2), name="Cumulative R",
        ))
        fig.update_layout(
            title="Cumulative R", plot_bgcolor="#0d1117",
            paper_bgcolor="#0d1117", font_color="#c9d1d9",
        )
        return fig.to_html(full_html=False, div_id=div_id)

    def _equity_chart(self, eq: EquityMetrics, div_id: str) -> str:
        if eq.equity_curve.empty:
            return ""
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(
            x=eq.equity_curve.index.tolist(), y=eq.equity_curve.tolist(),
            mode="lines", line=dict(color="#58a6ff"), name="Equity",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=eq.drawdown_curve.index.tolist(), y=(eq.drawdown_curve * 100).tolist(),
            mode="lines", fill="tozeroy", line=dict(color="#e74c3c"), name="Drawdown %",
        ), row=2, col=1)
        fig.update_layout(
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117", font_color="#c9d1d9",
        )
        return fig.to_html(full_html=False, div_id=div_id)

    def _render_overall(self, cm: ConvexityMetrics, eq: EquityMetrics) -> str:
        return (
            '<div class="section"><h2>Overall Performance</h2>'
            + self._render_metrics_grid(cm)
            + "<h3>Convexity Scorecard</h3>"
            + self._render_scorecard(cm)
            + self._r_histogram(cm.r_multiples, "r-waterfall-overall")
            + self._cumulative_r_chart(cm.r_multiples, "cumr-overall")
            + self._equity_chart(eq, "equity-overall")
            + "</div>"
        )

    def _render_year(self, year: int, cm: ConvexityMetrics) -> str:
        return (
            f'<div class="section"><h2>{year}</h2>'
            + self._render_metrics_grid(cm)
            + self._render_scorecard(cm)
            + self._r_histogram(cm.r_multiples, f"r-waterfall-{year}")
            + self._cumulative_r_chart(cm.r_multiples, f"cumr-{year}")
            + "</div>"
        )

    def _render_regime(self, breakdown: Dict[str, ConvexityMetrics]) -> str:
        if not breakdown:
            return ""
        rows = ""
        for regime, cm in sorted(breakdown.items()):
            rows += (
                f"<tr><td>{regime}</td><td>{cm.n_trades}</td>"
                f"<td>{cm.win_rate:.1%}</td><td>{cm.expectancy_r:.2f}R</td>"
                f"<td>{cm.avg_win_r:.2f}R</td><td>{cm.avg_loss_r:.2f}R</td>"
                f"<td>${cm.total_pnl:,.0f}</td></tr>"
            )
        return (
            '<div class="section"><h2>Regime Breakdown</h2>'
            "<table><tr><th>Regime</th><th>Trades</th><th>Win%</th>"
            "<th>Expectancy</th><th>Avg Win</th><th>Avg Loss</th><th>P&L</th></tr>"
            + rows + "</table></div>"
        )
