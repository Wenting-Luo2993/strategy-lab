import {
  fetchOperationalMetrics,
  formatNumber,
  formatTimestamp,
  latestFillGroups,
  summarizeBySymbol,
  summarizeMetrics,
} from "./metrics";

export default async function Home() {
  const metrics = await fetchOperationalMetrics();
  const summary = summarizeMetrics(metrics);
  const bySymbol = summarizeBySymbol(metrics);
  const fills = latestFillGroups(metrics);

  return (
    <main className="min-h-screen bg-[#f5f7f6] text-[#17211d]">
      <section className="border-b border-[#d9e0dc] bg-white">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-5 py-6 sm:px-8 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#4f6f62]">
              IB Paper Operations
            </p>
            <h1 className="mt-2 text-3xl font-semibold text-[#14231d]">
              Execution Metrics
            </h1>
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Metric label="Rows" value={summary.count.toLocaleString()} />
            <Metric label="Fills" value={summary.fills.toLocaleString()} />
            <Metric label="Avg latency" value={`${formatNumber(summary.avgLatencyMs, 0)} ms`} />
            <Metric label="Avg slip" value={`${formatNumber(summary.avgSlippageBps)} bps`} />
          </div>
        </div>
      </section>

      <section className="mx-auto grid w-full max-w-7xl gap-5 px-5 py-6 sm:px-8 lg:grid-cols-[1.4fr_0.8fr]">
        <div className="overflow-hidden rounded-lg border border-[#d9e0dc] bg-white">
          <div className="flex items-center justify-between border-b border-[#e4e9e6] px-4 py-3">
            <h2 className="text-base font-semibold">Recent fills</h2>
            <span className="text-xs text-[#587166]">Latest {fills.length}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-[#eef3f0] text-xs uppercase text-[#4d625a]">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Side</th>
                  <th className="px-4 py-3 text-right">Qty</th>
                  <th className="px-4 py-3 text-right">Expected</th>
                  <th className="px-4 py-3 text-right">Actual</th>
                  <th className="px-4 py-3 text-right">Slip bps</th>
                  <th className="px-4 py-3 text-right">Latency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#e8eeea]">
                {fills.map((fill) => (
                  <tr key={fill.orderId} className="hover:bg-[#f7faf8]">
                    <td className="whitespace-nowrap px-4 py-3 text-[#53675f]">{formatTimestamp(fill.timestamp)}</td>
                    <td className="px-4 py-3 font-medium">{fill.symbol}</td>
                    <td className="px-4 py-3 capitalize">{fill.side}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(fill.quantity, 0)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(fill.expected)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(fill.actual)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(fill.slippageBps)}</td>
                    <td className="px-4 py-3 text-right">{formatNumber(fill.latencyMs, 0)} ms</td>
                  </tr>
                ))}
                {!fills.length && (
                  <tr>
                    <td className="px-4 py-8 text-center text-[#60756d]" colSpan={8}>
                      No operational metrics found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="space-y-5">
          <div className="rounded-lg border border-[#d9e0dc] bg-white p-4">
            <h2 className="text-base font-semibold">Health snapshot</h2>
            <dl className="mt-4 grid gap-3 text-sm">
              <Stat label="Latest metric" value={formatTimestamp(summary.latestTimestamp)} />
              <Stat label="Worst slippage" value={`${formatNumber(summary.worstSlippageBps)} bps`} />
              <Stat label="Max latency" value={`${formatNumber(summary.maxLatencyMs, 0)} ms`} />
            </dl>
          </div>

          <div className="rounded-lg border border-[#d9e0dc] bg-white p-4">
            <h2 className="text-base font-semibold">By symbol</h2>
            <div className="mt-4 space-y-3">
              {bySymbol.map((symbol) => (
                <div key={symbol.symbol} className="border-b border-[#edf1ef] pb-3 last:border-0 last:pb-0">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium">{symbol.symbol}</span>
                    <span className="text-[#587166]">{symbol.fills} fills</span>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-[#587166]">
                    <span>{formatNumber(symbol.avgLatencyMs, 0)} ms avg latency</span>
                    <span className="text-right">{formatNumber(symbol.avgSlippageBps)} bps avg slip</span>
                  </div>
                </div>
              ))}
              {!bySymbol.length && <p className="text-sm text-[#60756d]">No symbols to summarize.</p>}
            </div>
          </div>
        </aside>
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-[#d9e0dc] bg-[#f8fbf9] px-3 py-2">
      <div className="truncate text-xs text-[#587166]">{label}</div>
      <div className="mt-1 truncate text-lg font-semibold text-[#17211d]">{value}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-[#60756d]">{label}</dt>
      <dd className="text-right font-medium">{value}</dd>
    </div>
  );
}
