import assert from "node:assert/strict";
import test from "node:test";
import { calendarYearRanges, comparisonCurves, cumulativeR, decodeDailySeries, detailCache, rangeSeries, researchNumber } from "../src/data/researchSeries.ts";
import { combinedComparison } from "../src/data/linkedCharts.mjs";
import { SupabaseResearchDataAdapter } from "../src/data/researchSupabaseAdapter.ts";

function payload() {
  return {
    version: 1, experimentId: "experiment-1", basis: "daily test",
    runs: [{
      runId: "run-1", label: "QQQ", candidateId: "rule", foldIndex: 0,
      segmentRole: "train", tradeCount: 3, metrics: {}, profitFactor: "1.234567",
      rAvailable: true, equityAvailable: true, startingEquity: 100,
      dailyR: [{ date: "2026-01-02", value: 5 }, { date: "2026-01-05", value: -2 }, { date: "2026-01-06", value: 1 }],
      dailyEquity: [{ date: "2026-01-02", value: 120 }, { date: "2026-01-05", value: 90 }, { date: "2026-01-06", value: 99 }],
    }],
  };
}

test("combined R drawdown uses the summed curve rather than summing independent drawdowns", () => {
  const days = ["2026-01-02", "2026-01-05"];
  const points = (values) => values.map((value, i) => ({ date: days[i], value }));
  const all = combinedComparison([
    { capital: 100, r: points([5, -3]), prices: points([10, 12]), closed: points([100, 110]) },
    { capital: 100, r: points([-2, 4]), prices: points([20, 18]), closed: points([100, 95]) },
  ]);
  assert.deepEqual(cumulativeR(all.r), [
    { date: days[0], value: 3, drawdownR: 0 }, { date: days[1], value: 4, drawdownR: 0 },
  ]);
  assert.equal(all.benchmark[0].value, 200);
  assert.equal(all.closed.at(-1).value, 205);
});
test("calendar shortcuts use full years, clip dataset edges, and omit years without data", () => {
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

test("range rebases cumulative R and equity peaks without using the previous all-history peak", () => {
  const run = decodeDailySeries(payload(), "experiment-1").runs[0];
  const result = rangeSeries(run, "2026-01-06", "2026-01-06");
  assert.equal(result.totalR, 1);
  assert.equal(result.maxDrawdown, 0);
  assert.equal(result.drawdown[0].value, 0);
  assert.equal(rangeSeries(run, "2026-01-02", "2026-01-06").maxDrawdown, -25);
  assert.throws(() => rangeSeries(run, "2026-01-06", "2026-01-02"), /start/);
  assert.equal(rangeSeries(run, "2027-01-01", "2027-01-02").maxDrawdown, null);
});

test("missing equity is unavailable, numeric strings have bounded precision, input remains unchanged", () => {
  const p = payload(), original = structuredClone(p);
  const run = decodeDailySeries(p, "experiment-1").runs[0];
  assert.equal(researchNumber(run.profitFactor), "1.235");
  assert.equal(researchNumber(""), "Unavailable");
  assert.equal(researchNumber("0.123456789", 4), "0.1235");
  assert.deepEqual(p, original);
  run.dailyEquity = []; run.equityAvailable = false;
  assert.equal(rangeSeries(run, "2026-01-02", "2026-01-06").maxDrawdown, null);
});

test("daily price benchmarks and closed equity rebase to identical dollars without changing retained history", () => {
  const p = payload();
  p.runs[0].dailyPrices = [{ date: "2026-01-02", value: 50 }, { date: "2026-01-05", value: 100 }, { date: "2026-01-06", value: 150 }];
  p.runs[0].dailyClosedEquity = [{ date: "2026-01-02", value: 100 }, { date: "2026-01-05", value: 200 }, { date: "2026-01-06", value: 180 }];
  const run = decodeDailySeries(p, "experiment-1").runs[0];
  const original = structuredClone(run);
  const computed = comparisonCurves(run, "2026-01-05", "2026-01-06");
  assert.deepEqual(computed.benchmark.map(p => p.value), [100, 150]);
  assert.deepEqual(computed.closed.map(p => p.value), [100, 90]);
  assert.deepEqual(run, original);
  assert.deepEqual(comparisonCurves(run, "2027-01-01", "2027-12-31").benchmark, []);
});
test("decoder rejects identity, invalid calendar dates, duplicate dates, nonfinite values and availability mismatch", () => {
  assert.throws(() => decodeDailySeries(payload(), "wrong"), /identity/);
  for (const edit of [
    p => p.runs[0].dailyR[0].date = "2026-02-30",
    p => p.runs[0].dailyR[1].date = "2026-01-02",
    p => p.runs[0].dailyR[0].value = "Infinity",
    p => p.runs[0].equityAvailable = false,
  ]) {
    const p = payload(); edit(p);
    assert.throws(() => decodeDailySeries(p, "experiment-1"));
  }
});

test("lazy detail cache deduplicates, evicts, expires and does not cache errors", async () => {
  let calls = 0;
  const cache = detailCache(async id => { calls++; if (id === "fail") throw new Error("failure"); return id; }, 2, 10);
  assert.equal(calls, 0);
  await Promise.all([cache("a"), cache("a")]); assert.equal(calls, 1);
  await cache("b"); await cache("c"); await cache("a"); assert.equal(calls, 4);
  await assert.rejects(cache("fail")); await assert.rejects(cache("fail")); assert.equal(calls, 6);
  await new Promise(resolve => setTimeout(resolve, 15));
  await cache("a"); assert.equal(calls, 7);
});

test("daily adapter requests only one experiment, uses anonymous GET and has no eager traffic", async () => {
  const calls = [];
  const adapter = new SupabaseResearchDataAdapter("https://example.supabase.co", "anon", async (url, init) => {
    calls.push({ url: new URL(url), init });
    return new Response(JSON.stringify([{ payload: payload() }]));
  });
  assert.equal(calls.length, 0);
  assert.equal((await adapter.getExperimentDailySeries("experiment-1")).runs.length, 1);
  assert.equal(calls[0].url.searchParams.get("experiment_id"), "eq.experiment-1");
  assert.equal(calls[0].url.searchParams.get("limit"), "1");
  assert.equal(calls[0].init.method, "GET");
});
