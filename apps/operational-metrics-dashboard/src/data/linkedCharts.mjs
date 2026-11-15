export function dateFromTime(time) {
  if (typeof time === "string") return time;
  if (time && typeof time === "object" && "year" in time && "month" in time && "day" in time) {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  return null;
}

/** Missing observations are not zero returns or synthetic equity. */
export function sumDailySeries(groups) {
  if (!groups.length || groups.some((g) => !g.length)) return [];
  const maps = groups.map((g) => new Map(g.map((p) => [p.date, p.value])));
  return groups[0].filter((p) => maps.every((m) => m.has(p.date))).map((p) => ({
    date: p.date, value: maps.reduce((sum, m) => sum + m.get(p.date), 0),
  }));
}

function fundedSum(groups, capitals) {
  const first = sumDailySeries(groups)[0]?.date;
  if (!first || capitals.some((c) => !(c > 0))) return [];
  const baselines = groups.map((g) => g.find((p) => p.date === first).value);
  if (baselines.some((v) => !(v > 0))) return [];
  return sumDailySeries(groups.map((g, i) => g.map((p) => ({
    date: p.date, value: capitals[i] * p.value / baselines[i],
  }))));
}

export function combinedComparison(members) {
  return {
    r: sumDailySeries(members.map((m) => m.r)),
    benchmark: fundedSum(members.map((m) => m.prices), members.map((m) => m.capital)),
    closed: fundedSum(members.map((m) => m.closed), members.map((m) => m.capital)),
  };
}

/** Synchronization is scoped to one four-chart comparison, with no feedback loops. */
export function createChartLink() {
  const peers = new Set();
  let busy = false, lastRange = null, lastCursor = null;
  function update(action) {
    busy = true;
    try { action(); } finally { busy = false; }
  }
  return {
    register(peer, initialRange) {
      if (!lastRange && initialRange) lastRange = { ...initialRange };
      peers.add(peer);
      update(() => {
        if (lastRange) peer.range(lastRange);
        if (lastCursor) peer.cursor(lastCursor);
      });
      return () => {
        peers.delete(peer);
        if (!peers.size) { lastRange = null; lastCursor = null; }
      };
    },
    range(source, range) {
      if (busy || !peers.has(source) || (lastRange?.from === range.from && lastRange?.to === range.to)) return false;
      lastRange = { ...range };
      update(() => peers.forEach((peer) => { if (peer !== source) peer.range(lastRange); }));
      return true;
    },
    cursor(source, cursor) {
      if (busy || !peers.has(source)) return;
      lastCursor = cursor;
      update(() => peers.forEach((peer) => peer.cursor(cursor)));
    },
  };
}
