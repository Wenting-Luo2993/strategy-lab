import type { DerivedClosedTrade } from "./dashboardSelectors";
import type { EquitySnapshot } from "./types";

export type TimestampValuePoint = {
  time: number;
  value: number;
};

export function equityCurvePoints(equity: EquitySnapshot[]): TimestampValuePoint[] {
  const byTimestamp = new Map<number, number>();
  equity.forEach((snapshot) => {
    const timestamp = toTimestamp(snapshot.timestamp);
    if (timestamp !== null && Number.isFinite(snapshot.net_liquidation)) {
      byTimestamp.set(timestamp, Number(snapshot.net_liquidation));
    }
  });
  return [...byTimestamp.entries()]
    .map(([time, value]) => ({ time, value }))
    .sort((left, right) => left.time - right.time);
}

export function tradePnlPoints(trades: DerivedClosedTrade[]): TimestampValuePoint[] {
  return trades
    .filter((trade) => Number.isFinite(trade.pnl))
    .map((trade) => ({
      time: toTimestamp(trade.exitTime),
      value: Number(trade.pnl),
    }))
    .filter((point): point is TimestampValuePoint => point.time !== null)
    .sort((left, right) => left.time - right.time);
}

function toTimestamp(value: string): number | null {
  const timestamp = Math.floor(new Date(value).getTime() / 1000);
  return Number.isFinite(timestamp) ? timestamp : null;
}
