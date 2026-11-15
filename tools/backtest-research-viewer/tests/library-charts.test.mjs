import assert from "node:assert/strict";
import test from "node:test";
import { calendarYearRanges, selectedSeries } from "../public/library-charts.mjs";
import { combinedComparison, createChartLink, dateFromTime } from "../../../apps/operational-metrics-dashboard/src/data/linkedCharts.mjs";

test("charts initialize from the data calendar, and late registration preserves the user's range", () => {
  const link = createChartLink();
  const ranges = [];
  const first = { range(value) { ranges.push(value); }, cursor() {} };
  const second = { range(value) { ranges.push(value); }, cursor() {} };
  link.register(first, { from: -0.5, to: 1678.5 });
  assert.deepEqual(ranges, [{ from: -0.5, to: 1678.5 }]);
  link.range(first, { from: 250, to: 500 });
  link.register(second, { from: -0.5, to: 1678.5 });
  assert.deepEqual(ranges.at(-1), { from: 250, to: 500 });
});

test("All sums funded accounts and recomputes drawdown after offsetting member losses", () => {
  const days = ["2024-01-02", "2024-01-03", "2024-01-04"];
  const points = (values) => values.map((value, i) => ({ date: days[i], value }));
  const members = [
    { capital: 100, r: points([5, -2, 1]), prices: points([10, 11, 12]), closed: points([100, 110, 120]) },
    { capital: 200, r: points([-5, 2, -1]), prices: points([20, 22, 24]), closed: points([200, 190, 180]) },
  ];
  const original = structuredClone(members);
  const all = combinedComparison(members);
  assert.deepEqual(all.benchmark.map((p) => p.value), [300, 330, 360]);
  assert.deepEqual(all.closed.map((p) => p.value), [300, 300, 300]);
  assert.equal(selectedSeries(all.r, days[0], days[2]).maxDrawdown, 0);
  assert.equal(selectedSeries(all.r, days[0], days[2]).total, 0);
  assert.deepEqual(members, original);
});

test("combined curves require every member's observations and rebase on their common first close", () => {
  const a = [{ date: "2024-01-02", value: 100 }, { date: "2024-01-03", value: 120 }];
  const b = [{ date: "2024-01-03", value: 50 }];
  const all = combinedComparison([{ capital: 100, r: a, prices: a, closed: a },
    { capital: 200, r: b, prices: b, closed: [] }]);
  assert.deepEqual(all.benchmark, [{ date: "2024-01-03", value: 300 }]);
  assert.deepEqual(all.closed, []);
  assert.deepEqual(all.r, [{ date: "2024-01-03", value: 170 }]);
});

test("linked ranges and same-date crosshairs propagate without recursion, and detached charts stop receiving", () => {
  const link = createChartLink(), ranges = [], cursors = [];
  const source = { range() {}, cursor(value) { cursors.push(value); link.cursor(source, value); } };
  const target = { range(value) { ranges.push(value); link.range(target, value); },
    cursor(value) { cursors.push(value); link.cursor(target, value); } };
  const detachSource = link.register(source), detachTarget = link.register(target);
  assert.equal(link.range(source, { from: 1.5, to: 20.5 }), true);
  assert.deepEqual(ranges, [{ from: 1.5, to: 20.5 }]);
  assert.equal(link.range(source, { from: 1.5, to: 20.5 }), false);
  link.cursor(source, { date: "2024-01-03", key: "All" });
  assert.equal(cursors.length, 2);
  link.cursor(source, null);
  assert.deepEqual(cursors.slice(-2), [null, null]);
  detachTarget();
  link.range(source, { from: 3, to: 10 });
  assert.equal(ranges.length, 1);
  detachSource();
  link.register(target);
  assert.equal(ranges.length, 1);
  assert.equal(dateFromTime({ year: 2024, month: 1, day: 3 }), "2024-01-03");
});

test("calendar buttons cover available years, clipping only the data boundaries", () => {
  const dates = ["2020-02-29", "2018-05-30", "2022-03-02", "2019-07-01", "2018-05-30"];
  const original = [...dates];
  assert.deepEqual(calendarYearRanges(dates), [
    { year: "2018", from: "2018-05-30", to: "2018-12-31" },
    { year: "2019", from: "2019-01-01", to: "2019-12-31" },
    { year: "2020", from: "2020-01-01", to: "2020-12-31" },
    { year: "2022", from: "2022-01-01", to: "2022-03-02" },
  ]);
  assert.deepEqual(dates, original);
  assert.deepEqual(calendarYearRanges([]), []);
  assert.deepEqual(calendarYearRanges(["2020-02-29"]), [
    { year: "2020", from: "2020-02-29", to: "2020-02-29" },
  ]);
});

const r = [
  { date: "2024-01-01", value: 5 },
  { date: "2024-01-02", value: -2 },
  { date: "2024-01-03", value: 1 },
];
test("selected R restarts cumulative and peak at zero without mutating history", () => {
  const result = selectedSeries(r, "2024-01-02", "2024-01-03");
  assert.deepEqual(result.main.map((p) => p.value), [-2, -1]);
  assert.equal(result.total, -1);
  assert.equal(result.maxDrawdown, -2);
  assert.equal(selectedSeries(r, "2024-01-01", "2024-01-03").total, 4);
  assert.equal(r[0].value, 5);
});

test("equity drawdown rebases to preceding observation rather than the old peak", () => {
  const points = [{ date: "2024-01-01", value: 200 },
    { date: "2024-01-02", value: 100 }, { date: "2024-01-03", value: 110 }];
  const result = selectedSeries(points, "2024-01-03", "2024-01-03", { kind: "equity", startingEquity: 100 });
  assert.equal(result.maxDrawdown, 0);
  assert.ok(Math.abs(result.cumulativeReturn[0].value - 10) < 1e-10);
  assert.equal(result.total, 110);
  assert.equal(selectedSeries(points, "2024-01-01", "2024-01-03",
    { kind: "equity", startingEquity: 100 }).maxDrawdown, -50);
});

test("first session uses recorded capital; missing baseline does not fabricate drawdown", () => {
  const points = [{ date: "2024-01-01", value: 80 }];
  const result = selectedSeries(points, "2024-01-01", "2024-01-01", { kind: "equity", startingEquity: 100 });
  assert.ok(Math.abs(result.maxDrawdown + 20) < 1e-10);
  assert.ok(Math.abs(result.returns[0].value + 20) < 1e-10);
  assert.equal(selectedSeries(points, "2024-01-01", "2024-01-01", { kind: "equity" }).maxDrawdown, null);
  assert.equal(selectedSeries(points, "2025-01-01", "2025-01-02").total, null);
  assert.throws(() => selectedSeries(points, "2024-02-01", "2024-01-01"), /Start date/);
});
