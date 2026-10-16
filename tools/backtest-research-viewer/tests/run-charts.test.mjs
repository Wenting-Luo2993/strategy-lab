import assert from "node:assert/strict";
import test from "node:test";

import { chartModel, rangeStart, renderTimeChart } from "../public/run-charts.mjs";

const points = [
  { observedAt: "2026-01-02T10:00:00-05:00", equity: 100000, drawdown: 0 },
  { observedAt: "2026-01-03T10:00:00-05:00", equity: 120000, drawdown: 0 },
  { observedAt: "2026-04-02T10:00:00-04:00", equity: 108000, drawdown: -0.1 },
];

test("positions observations by elapsed time rather than index", () => {
  const chart = chartModel(points);
  const [first, second, last] = chart.points;
  assert.ok(second.x - first.x < (last.x - first.x) / 50);
  assert.ok(chart.ticks.every((tick) => Number.isFinite(tick.y)));
});

test("zoom preserves initial capital and full-run drawdown", () => {
  const returns = chartModel(points, { kind: "return", startingEquity: 100000, range: "1m" });
  assert.equal(returns.points.length, 1);
  assert.equal(returns.points[0].value, 0.08);
  const risk = chartModel(points, { kind: "drawdown", range: "1m" });
  assert.equal(risk.points[0].value, -0.1);
  assert.equal(risk.ticks.at(-1).value, 0);
  const differentCapital = chartModel(points, { kind: "return", startingEquity: 200000 });
  assert.equal(differentCapital.points[0].value, -0.5);
});

test("responsive chart geometry keeps coordinates inside the labelled plot", () => {
  const chart = chartModel(points, { width: 300, height: 260 });
  assert.equal(chart.width, 300);
  assert.equal(chart.points[0].x, 82);
  assert.equal(chart.points.at(-1).x, 280);
  assert.match(renderTimeChart(chart, "Equity"), /viewBox="0 0 300 260"/);
});

test("does not invent a capital baseline or missing series", () => {
  assert.equal(chartModel(points, { kind: "return" }), null);
  assert.equal(chartModel(points, { kind: "return", startingEquity: 0 }), null);
  assert.equal(chartModel([]), null);
  assert.equal(chartModel([{ observedAt: "bad", equity: NaN }]), null);
  assert.match(renderTimeChart(null, "Equity"), /unavailable/);
});

test("flat, single-point, negative and session-return curves remain finite", () => {
  for (const input of [[points[0]], [points[0], points[0]], [{ ...points[0], equity: -100 }]]) {
    const chart = chartModel(input);
    const html = renderTimeChart(chart, "Equity");
    assert.doesNotMatch(html, /NaN|Infinity/);
    assert.match(html, /type="range"/);
  }
  const bars = chartModel([
    { observedAt: points[0].observedAt, return: null },
    { observedAt: points[1].observedAt, return: -0.05 },
    { observedAt: points[2].observedAt, return: 0.02 },
  ], { kind: "sessionReturn" });
  assert.equal(bars.points.length, 2);
  const html = renderTimeChart(bars, "Session returns");
  assert.match(html, /chart-loss/);
  assert.match(html, /chart-gain/);
  assert.doesNotMatch(html, /NaN|Infinity/);
});

test("calendar windows clamp month-end and leap dates", () => {
  assert.equal(new Date(rangeStart("2026-03-31T12:00:00Z", "1m")).toISOString(), "2026-02-28T12:00:00.000Z");
  assert.equal(new Date(rangeStart("2024-02-29T12:00:00Z", "1y")).toISOString(), "2023-02-28T12:00:00.000Z");
  assert.equal(rangeStart(points[0].observedAt, "all"), -Infinity);
  assert.throws(() => rangeStart(points[0].observedAt, "bad"), /Invalid chart range/);
});

test("chart text and timestamps are escaped", () => {
  const model = chartModel([{ ...points[0], observedAt: points[0].observedAt }]);
  model.points[0].observedAt = '<img src=x onerror="alert(1)">';
  const html = renderTimeChart(model, '<script>alert(1)</script>');
  assert.doesNotMatch(html, /<img|<script>/);
  assert.match(html, /&lt;script&gt;/);
});
