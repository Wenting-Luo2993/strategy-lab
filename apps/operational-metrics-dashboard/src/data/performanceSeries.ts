import type { DerivedClosedTrade } from "./dashboardSelectors";
import type { EquitySnapshot } from "./types";

export type TimestampValuePoint = {
  time: number;
  value: number;
};

export type PerformanceSummary = {
  maximumDrawdownPct: number | null;
  longestLosingStreak: number;
  currentLosingStreak: number;
  marRatio: number | null;
};

export function equityCurvePoints(equity: EquitySnapshot[]): TimestampValuePoint[] {
  const byMarketDay = new Map<string, TimestampValuePoint>();
  equity.forEach((snapshot) => {
    const timestamp = toTimestamp(snapshot.timestamp);
    if (timestamp !== null && Number.isFinite(snapshot.net_liquidation)) {
      const marketDay = marketDate(snapshot.timestamp);
      const existing = byMarketDay.get(marketDay);
      if (!existing || timestamp > existing.time) {
        byMarketDay.set(marketDay, {
          time: timestamp,
          value: Number(snapshot.net_liquidation),
        });
      }
    }
  });
  return [...byMarketDay.values()]
    .sort((left, right) => left.time - right.time);
}

export function tradePnlPoints(trades: DerivedClosedTrade[]): TimestampValuePoint[] {
  const points = trades
    .filter((trade) => Number.isFinite(trade.pnl))
    .map((trade) => ({
      time: toTimestamp(trade.exitTime),
      value: Number(trade.pnl),
    }))
    .filter((point): point is TimestampValuePoint => point.time !== null)
    .sort((left, right) => left.time - right.time);
  let previousTime = Number.NEGATIVE_INFINITY;
  return points.map((point) => {
    const time = Math.max(point.time, previousTime + 1);
    previousTime = time;
    return { ...point, time };
  });
}

export function performanceSummary(
  equity: EquitySnapshot[],
  trades: DerivedClosedTrade[],
): PerformanceSummary {
  const equityPoints = equityCurvePoints(equity);
  const maximumDrawdownPct = maximumDrawdown(equityPoints);
  const streaks = losingStreaks(trades);
  return {
    maximumDrawdownPct,
    ...streaks,
    marRatio: marRatio(equityPoints, maximumDrawdownPct),
  };
}

function maximumDrawdown(points: TimestampValuePoint[]): number | null {
  if (!points.length) {
    return null;
  }
  let peak = points[0].value;
  let maximumDrawdownPct = 0;
  for (const point of points) {
    peak = Math.max(peak, point.value);
    if (peak <= 0) {
      continue;
    }
    maximumDrawdownPct = Math.max(
      maximumDrawdownPct,
      ((peak - point.value) / peak) * 100,
    );
  }
  return maximumDrawdownPct;
}

function losingStreaks(
  trades: DerivedClosedTrade[],
): Pick<PerformanceSummary, "longestLosingStreak" | "currentLosingStreak"> {
  const chronologicalPnl = trades
    .filter((trade) => Number.isFinite(trade.pnl))
    .sort((left, right) => (
      new Date(left.exitTime).getTime() - new Date(right.exitTime).getTime()
    ))
    .map((trade) => Number(trade.pnl));
  let longestLosingStreak = 0;
  let currentLosingStreak = 0;
  for (const pnl of chronologicalPnl) {
    if (pnl < 0) {
      currentLosingStreak += 1;
      longestLosingStreak = Math.max(longestLosingStreak, currentLosingStreak);
    } else {
      currentLosingStreak = 0;
    }
  }
  return { longestLosingStreak, currentLosingStreak };
}

function marRatio(
  points: TimestampValuePoint[],
  maximumDrawdownPct: number | null,
): number | null {
  if (points.length < 2 || maximumDrawdownPct === null || maximumDrawdownPct <= 0) {
    return null;
  }
  const first = points[0];
  const last = points[points.length - 1];
  const elapsedDays = (last.time - first.time) / (24 * 60 * 60);
  if (first.value <= 0 || last.value <= 0 || elapsedDays <= 0) {
    return null;
  }
  const annualizedReturn = (
    Math.pow(last.value / first.value, 365.25 / elapsedDays) - 1
  ) * 100;
  return annualizedReturn / maximumDrawdownPct;
}

function toTimestamp(value: string): number | null {
  const timestamp = Math.floor(new Date(value).getTime() / 1000);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function marketDate(value: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}
