import type { DerivedClosedTrade } from "./dashboardSelectors";
import type { EquitySnapshot } from "./types";

export type TimestampValuePoint = {
  time: number;
  value: number;
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
