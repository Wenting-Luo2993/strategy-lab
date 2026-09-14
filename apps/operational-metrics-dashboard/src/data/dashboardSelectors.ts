import type {
  DashboardData,
  EquityPnlProvenance,
  EquitySnapshot,
  Position,
} from "./types";

export type DerivedClosedTrade = {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  entryPrice: number | null;
  exitPrice: number | null;
  exitTime: string;
  pnl: number | null;
  currency: string | null;
};

export type PnlPresentation = {
  value: number | null;
  currency: string | null;
  provenance: EquityPnlProvenance;
  version: number | null;
  label: string;
};

export function netLiquidationFor(
  data: DashboardData,
): { value: number | null; currency: string | null } {
  const latest = data.equity[0];
  return {
    value: latest?.net_liquidation ?? null,
    // Account/base currency does not establish this field's denomination.
    currency: latest?.net_liquidation_currency ?? null,
  };
}

export function dashboardAccounts(data: DashboardData) {
  return data.accounts?.length ? data.accounts : data.account ? [data.account] : [];
}

export function dashboardDataForAccount(data: DashboardData, accountId: string | null): DashboardData {
  if (!accountId) {
    return data;
  }
  const accounts = dashboardAccounts(data);
  const account = accounts.find((candidate) => candidate.account_id === accountId) ?? null;
  const allowLegacyMetrics = accounts.length <= 1;
  return {
    ...data,
    account,
    accounts,
    equity: data.equity.filter((item) => item.account_id === accountId),
    positions: data.positions.filter((item) => item.account_id === accountId),
    orderEvents: data.orderEvents.filter((item) => item.account_id === accountId),
    trades: data.trades.filter((item) => item.account_id === accountId),
    metrics: data.metrics.filter((item) => (
      item.dimensions?.account_id === accountId
      || (allowLegacyMetrics && !item.dimensions?.account_id)
    )),
    annotations: data.annotations.filter((item) => item.account_id === accountId),
  };
}

export function activePositionsFor(positions: Position[]): Position[] {
  return positions
    .filter((position) => Math.abs(Number(position.quantity)) > 0)
    .sort((left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime());
}

export function closedTradesFor(data: DashboardData): DerivedClosedTrade[] {
  const authoritative = data.trades
    .filter((trade) => trade.status === "closed" && trade.exit_price != null && trade.exit_time)
    .map((trade) => ({
      id: trade.trade_id,
      symbol: trade.symbol,
      side: trade.side,
      quantity: trade.quantity,
      entryPrice: trade.entry_price,
      exitPrice: trade.exit_price ?? null,
      exitTime: trade.exit_time as string,
      pnl: trade.pnl ?? null,
      currency: trade.pnl_currency ?? null,
    }));
  if (authoritative.length) {
    return authoritative.sort(
      (left, right) => new Date(right.exitTime).getTime() - new Date(left.exitTime).getTime(),
    );
  }
  return closedTradesFromOrderEvents(data.orderEvents);
}

export function realizedPnlFor(data: DashboardData): number | null {
  return realizedPnlPresentationFor(data).value;
}

export function realizedPnlPresentationFor(data: DashboardData): PnlPresentation {
  const latestEquity = data.equity[0];
  if (isFiniteNumber(latestEquity?.realized_pnl)) {
    const provenance = pnlProvenanceFor(latestEquity, "realized");
    return {
      value: latestEquity.realized_pnl,
      currency: latestEquity.realized_pnl_currency ?? null,
      provenance,
      version: latestEquity.pnl_version ?? null,
      label: pnlLabel("realized", provenance),
    };
  }
  if (isFiniteNumber(latestEquity?.local_realized_pnl)) {
    return {
      value: latestEquity.local_realized_pnl,
      currency: latestEquity.local_realized_pnl_currency ?? null,
      provenance: "local",
      version: latestEquity.pnl_version ?? null,
      label: "Local realized P&L (diagnostic)",
    };
  }

  const aggregate = aggregatePnlByCurrency(closedTradesFor(data));
  return {
    ...aggregate,
    provenance: "legacy",
    version: null,
    label: "Reconstructed realized P&L",
  };
}

export function unrealizedPnlFor(
  latestEquity: DashboardData["equity"][number] | undefined,
  activePositions: Position[],
): number | null {
  return unrealizedPnlPresentationFor(latestEquity, activePositions).value;
}

export function unrealizedPnlPresentationFor(
  latestEquity: DashboardData["equity"][number] | undefined,
  activePositions: Position[],
): PnlPresentation {
  if (isFiniteNumber(latestEquity?.unrealized_pnl)) {
    const provenance = pnlProvenanceFor(latestEquity, "unrealized");
    return {
      value: latestEquity.unrealized_pnl,
      currency: latestEquity.unrealized_pnl_currency ?? null,
      provenance,
      version: latestEquity.pnl_version ?? null,
      label: pnlLabel("unrealized", provenance),
    };
  }

  const aggregate = aggregatePnlByCurrency(
    activePositions.map((position) => ({
      pnl: position.unrealized_pnl,
      currency: position.unrealized_pnl_currency ?? null,
    })),
  );
  return {
    ...aggregate,
    provenance: "local",
    version: null,
    label: "Position unrealized P&L",
  };
}

function closedTradesFromOrderEvents(
  orderEvents: DashboardData["orderEvents"],
): DerivedClosedTrade[] {
  const fills = orderEvents
    .filter((event) => event.event_type === "ORDER_FILLED" && event.price != null)
    .sort((left, right) => new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime());
  const closes = orderEvents
    .filter((event) => event.event_type === "TRADE_CLOSED")
    .sort((left, right) => new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime());
  const closingOrderIds = new Set(closes.map(orderAccountKey));
  const entryLots = fills
    .filter((fill) => !closingOrderIds.has(orderAccountKey(fill)))
    .map((fill) => ({
      event: fill,
      remainingQuantity: Math.abs(Number(fill.quantity)),
    }));

  return closes.map((close) => {
    const closeTime = new Date(close.occurred_at).getTime();
    const quantity = Math.abs(Number(close.quantity));
    let remainingToClose = quantity;
    const consumedEntries: { event: DashboardData["orderEvents"][number]; quantity: number }[] = [];
    for (const lot of entryLots) {
      const entry = lot.event;
      if (
        remainingToClose <= 0
        || lot.remainingQuantity <= 0
        || entry.account_id !== close.account_id
        || entry.symbol !== close.symbol
        || new Date(entry.occurred_at).getTime() > closeTime
        || (close.trade_id && entry.trade_id !== close.trade_id)
      ) {
        continue;
      }
      const consumedQuantity = Math.min(lot.remainingQuantity, remainingToClose);
      lot.remainingQuantity -= consumedQuantity;
      remainingToClose -= consumedQuantity;
      consumedEntries.push({ event: entry, quantity: consumedQuantity });
    }
    const hasCompleteEntry = quantity > 0 && remainingToClose <= Number.EPSILON;
    const entryPrice = hasCompleteEntry
      ? consumedEntries.reduce(
        (sum, entry) => sum + Number(entry.event.price) * entry.quantity,
        0,
      ) / quantity
      : null;
    const side = consumedEntries[0]?.event.side ?? close.side;
    const pnl = entryPrice === null || close.price === null
      ? null
      : (side === "sell" || side === "short"
        ? entryPrice - close.price
        : close.price - entryPrice) * quantity;
    return {
      id: close.event_id,
      symbol: close.symbol,
      side,
      quantity,
      entryPrice,
      exitPrice: close.price,
      exitTime: close.occurred_at,
      pnl,
      currency: commonExplicitCurrency([
        close.trade_currency,
        ...consumedEntries.map((entry) => entry.event.trade_currency),
      ]),
    };
  }).reverse();
}

export function aggregatePnlByCurrency(
  rows: { pnl: number | null | undefined; currency: string | null | undefined }[],
): { value: number | null; currency: string | null } {
  const rowsWithPnl = rows.filter((row) => isFiniteNumber(row.pnl));
  if (!rowsWithPnl.length) {
    return { value: null, currency: null };
  }
  const currency = commonExplicitCurrency(rowsWithPnl.map((row) => row.currency));
  if (!currency) {
    return { value: null, currency: null };
  }
  return {
    value: rowsWithPnl.reduce((total, row) => total + Number(row.pnl), 0),
    currency,
  };
}

function orderAccountKey(
  event: DashboardData["orderEvents"][number],
): string {
  return `${event.account_id}\u0000${event.broker_order_id}`;
}

function commonExplicitCurrency(
  currencies: (string | null | undefined)[],
): string | null {
  const normalized = currencies.map((currency) => currency?.trim().toUpperCase() || null);
  if (!normalized.length || normalized.some((currency) => currency === null || currency === "BASE")) {
    return null;
  }
  return new Set(normalized).size === 1 ? normalized[0] : null;
}

function pnlProvenanceFor(
  snapshot: EquitySnapshot,
  field: "realized" | "unrealized",
): EquityPnlProvenance {
  const fieldProvenance = field === "realized"
    ? snapshot.realized_pnl_provenance
    : snapshot.unrealized_pnl_provenance;
  return fieldProvenance ?? snapshot.pnl_provenance ?? "unknown";
}

function pnlLabel(
  field: "realized" | "unrealized",
  provenance: EquityPnlProvenance,
): string {
  const description = field === "realized" ? "realized" : "unrealized";
  if (provenance === "broker") {
    return `Broker ${description} P&L`;
  }
  if (provenance === "local") {
    return `Local ${description} P&L (diagnostic)`;
  }
  if (provenance === "legacy") {
    return `Legacy ${description} P&L`;
  }
  return `${description[0].toUpperCase()}${description.slice(1)} P&L (source unknown)`;
}

function isFiniteNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}
