const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);
const number = (v, digits = 3) => Number.isFinite(v) ? v.toFixed(digits) : "Unavailable";

export function calendarYearRanges(dates) {
  const sorted = [...dates].sort();
  const first = sorted[0], last = sorted.at(-1);
  if (!first || !last) return [];
  return [...new Set(sorted.map((date) => date.slice(0, 4)))].map((year) => ({
    year,
    from: year === first.slice(0, 4) ? first : `${year}-01-01`,
    to: year === last.slice(0, 4) ? last : `${year}-12-31`,
  }));
}

/** Complete dated increments for R; complete daily observations for equity. */
export function selectedSeries(points, from, to, { kind = "r", startingEquity = null } = {}) {
  if (from > to) throw new Error("Start date must not be after end date.");
  const visible = points.filter((p) => p.date >= from && p.date <= to);
  let sum = 0;
  const baseline = kind === "r" ? 0 : points.filter((p) => p.date < from).at(-1)?.value ?? startingEquity;
  let peak = baseline;
  let maxDrawdown = null;
  let previous = baseline;
  const main = [], drawdown = [], returns = [], cumulativeReturn = [];
  for (const p of visible) {
    sum += p.value;
    const value = kind === "r" ? sum : p.value;
    main.push({ time: p.date, value });
    if (kind === "equity") {
      if (previous > 0) returns.push({ time: p.date, value: (value / previous - 1) * 100 });
      if (baseline > 0) cumulativeReturn.push({ time: p.date, value: (value / baseline - 1) * 100 });
      previous = value;
    }
    if (peak !== null && Number.isFinite(peak) && (kind === "r" || peak > 0)) {
      peak = Math.max(peak, value);
      const loss = kind === "r" ? value - peak : (value / peak - 1) * 100;
      drawdown.push({ time: p.date, value: loss });
      maxDrawdown = Math.min(maxDrawdown ?? 0, loss);
    }
  }
  return { main, drawdown, returns, cumulativeReturn, total: main.at(-1)?.value ?? null, maxDrawdown };
}

/** One shared date window; pan/zoom changes the window and recomputes every chart. */
export function mountTimeCharts(host, series, { kind = "r", startingEquity = null, comparison = false } = {}) {
  let disposed = false, charts = [], observers = [], listeners = [], timer;
  const dates = [...new Set(series.flatMap((s) => [...s.points, ...(s.prices || []), ...(s.closed || [])].map((p) => p.date)))].sort();
  if (!dates.length) {
    host.innerHTML = '<p class="empty">No complete dated observations were retained.</p>';
    return () => {};
  }
  const years = calendarYearRanges(dates);
  const label = (name) => series.length === 1 && name === "rule" ? "" : name;
  const hidden = new Set();
  const canCombine = series.every((s) => s.group === series[0].group)
    && new Set(series.map((s) => s.name)).size === series.length;
  let from = dates[0], to = dates.at(-1), display = "main", showAll = false;
  host.innerHTML = `<div class="research-range">
    <label>From <input type="date" data-range-from aria-label="Chart range start" value="${escape(from)}"></label>
    <label>To <input type="date" data-range-to aria-label="Chart range end" value="${escape(to)}"></label>
    <button type="button" data-range-all>All history</button>
    ${kind === "equity" ? '<button type="button" data-equity-display aria-pressed="false">Show selected-range return %</button>' : ""}
    </div><div class="chart-controls research-years" role="group" aria-label="Calendar year">
      ${years.map((window) => `<button type="button" data-range-year="${escape(window.year)}" aria-pressed="false">${escape(window.year)}</button>`).join("")}
    </div>${comparison ? `<div class="research-years research-tickers" role="group" aria-label="Visible tickers">
    <label><input type="checkbox" data-ticker-all ${canCombine ? "" : "disabled"}><span>All</span></label>
    ${series.map((s, i) => `<label><input type="checkbox" data-ticker="${i}" checked><span>${escape(s.name)}</span></label>`).join("")}</div>
    <p class="chart-note">${canCombine ? "All sums every independent account, including hidden tickers; dollars start at the sum of their capitals, and R is summed. This is not a shared-capital portfolio backtest." : "All is unavailable across different candidates, folds, roles, or repeated ticker accounts."}</p>` : ""}
    <p data-range-summary role="status"></p><div class="${comparison ? "comparison-grid" : ""}">
    ${comparison ? '<div data-library-benchmark></div>' : ""}<div data-library-main></div>
    ${comparison ? '<div data-library-closed></div>' : ""}<div data-library-drawdown></div></div>
    ${kind === "equity" ? '<div data-library-returns></div>' : ""}
    <p class="chart-note">${comparison ? "Benchmark and reconstructed closed-trade equity start at the same recorded capital at the first selected daily close. Independent accounts; price-ratio benchmark without explicit dividends or benchmark fees. Closed-trade equity is not intraday mark-to-market. " : ""}${kind === "r" ? "Cumulative R restarts at zero; drawdown is in R, not percent."
    : "Daily session equity; selected-range drawdown uses the preceding retained session (or starting capital), not the full-run peak. Stored intraday risk remains in the risk summary."}
    Drag or zoom to change every chart's date window. Crosshairs follow the same ticker and date, with each chart's own value. <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a>.</p>`;
  const summary = host.querySelector("[data-range-summary]");
  const inputFrom = host.querySelector("[data-range-from]"), inputTo = host.querySelector("[data-range-to]");
  let library;
  const clear = () => {
    clearTimeout(timer);
    listeners.forEach((remove) => remove()); listeners = [];
    observers.forEach((o) => o.disconnect()); observers = [];
    charts.forEach((c) => c.remove()); charts = [];
  };
  function render() {
    clear();
    inputFrom.value = from; inputTo.value = to;
    host.querySelectorAll("[data-range-year]").forEach((button, index) => {
      const window = years[index];
      button.setAttribute("aria-pressed", String(from === window.from && to === window.to));
    });
    if (from > to) { summary.textContent = "Start date must not be after end date."; return; }
    const colours = ["#67a7ff", "#fb923c", "#4ade80", "#c084fc", "#f472b6"];
    const models = series.map((s, index) => {
      const prices = (s.prices || []).filter((p) => p.date >= from && p.date <= to);
      const closed = (s.closed || []).filter((p) => p.date >= from && p.date <= to);
      return { name: s.name, key: s.key ?? s.name, index, color: colours[index % colours.length], ...selectedSeries(s.points, from, to, { kind, startingEquity }),
        benchmark: s.capital > 0 && prices.length ? prices.map((p) => ({ time: p.date, value: s.capital * p.value / prices[0].value })) : [],
        closed: s.capital > 0 && closed[0]?.value > 0 ? closed.map((p) => ({ time: p.date, value: s.capital * p.value / closed[0].value })) : [],
      };
    }).filter((m) => !hidden.has(m.index));
    if (showAll && canCombine) {
      const within = (points) => (points || []).filter((p) => p.date >= from && p.date <= to);
      const all = combinedComparison(series.map((s) => ({
        capital: s.capital, r: within(s.points), prices: within(s.prices), closed: within(s.closed),
      })));
      models.unshift({ name: "All", key: "__all__", color: "#eab308",
        ...selectedSeries(all.r, from, to), benchmark: all.benchmark.map((p) => ({ time: p.date, value: p.value })),
        closed: all.closed.map((p) => ({ time: p.date, value: p.value })),
      });
    }
    summary.textContent = models.length ? models.map((m) => `${label(m.name) ? `${label(m.name)}: ` : ""}${kind === "r" ? "cumulative" : "ending equity"} ${number(m.total, kind === "r" ? 3 : 2)}${kind === "r" ? "R" : " USD"} · max drawdown ${number(m.maxDrawdown, kind === "r" ? 3 : 2)}${kind === "r" ? "R" : "%"}`).join(" · ") : "No tickers selected.";
    if (!library) return;
    const styles = getComputedStyle(host);
    const link = createChartLink();
    const calendar = dates.filter((date) => date >= from && date <= to);
    for (const [selector, key, title] of [
      ...(comparison ? [["[data-library-benchmark]", "benchmark", "Buy-and-hold benchmarks"]] : []),
      ["[data-library-main]", display, kind === "r" ? "Selected-range cumulative R" : display === "main" ? "Daily session equity" : "Selected-range return %"],
      ...(comparison ? [["[data-library-closed]", "closed", "Reconstructed closed-trade equity"]] : []),
      ["[data-library-drawdown]", "drawdown", kind === "r" ? "Selected-range drawdown in R" : "Selected-range daily drawdown %"],
      ...(kind === "equity" ? [["[data-library-returns]", "returns", "Daily session returns %"]] : []),
    ]) {
      const node = host.querySelector(selector);
      node.innerHTML = `<h4>${title}</h4><div class="library-time-chart" aria-label="${title}"></div><output class="chart-readout" data-linked-readout></output>`;
      if (!models.some((m) => m[key].length)) {
        node.insertAdjacentHTML("beforeend", '<p class="empty">No usable observations in this range.</p>');
        continue;
      }
      const container = node.querySelector(".library-time-chart");
      const unit = key === "benchmark" || key === "closed" ? " USD" : kind === "r" ? "R" : key === "main" ? " USD" : "%";
      const chart = library.createChart(container, {
        width: container.clientWidth, height: 440,
        layout: { background: { type: library.ColorType.Solid, color: styles.getPropertyValue("--surface").trim() },
          textColor: styles.getPropertyValue("--muted").trim(), attributionLogo: false },
        grid: { vertLines: { visible: false }, horzLines: { color: "#80808030" } },
        crosshair: { mode: library.CrosshairMode.Magnet, vertLine: { style: library.LineStyle.Dashed },
          horzLine: { style: library.LineStyle.Dashed } },
        timeScale: { minBarSpacing: 0.001 },
      });
      charts.push(chart);
      chart.addSeries(library.LineSeries, { lastValueVisible: false, priceLineVisible: false })
        .setData(calendar.map((time) => ({ time })));
      // Layout notifications are not a user's pan/zoom selection.
      let interacted = false, dragging = false, resizing = false, pending;
      const lines = models.filter((m) => m[key].length).map((m) => {
        const line = chart.addSeries(key === "returns" ? library.HistogramSeries : library.LineSeries, { color: m.color,
          title: label(m.name), priceFormat: { type: "custom", minMove: 0.001,
            formatter: (v) => `${number(v, unit === "R" ? 3 : 2)}${unit}` } });
        const values = new Map(m[key].map((p) => [p.time, p.value]));
        line.setData(m[key]);
        return { key: m.key, line, values };
      });
      // The library applies scale updates on paint; its immediate range is still a placeholder.
      let viewport = { from: -0.5, to: calendar.length - 0.5 };
      const peer = {
        range: (range) => {
          viewport = { ...range };
          chart.timeScale().setVisibleLogicalRange(range);
        },
        cursor: (cursor) => {
          const target = cursor && lines.find((l) => l.key === cursor.key);
          const value = target?.values.get(cursor.date);
          if (value === undefined) chart.clearCrosshairPosition();
          else chart.setCrosshairPosition(value, cursor.date, target.line);
          node.querySelector("[data-linked-readout]").textContent = value === undefined ? "" : `${cursor.date} · ${label(models.find((m) => m.key === cursor.key).name)} · ${number(value, unit === "R" ? 3 : 2)}${unit}`;
        },
      };
      listeners.push(link.register(peer, viewport));
      const restoreViewport = () => {
        resizing = true;
        chart.timeScale().setVisibleLogicalRange(viewport);
        resizing = false;
      };
      chart.timeScale().subscribeSizeChange(restoreViewport);
      listeners.push(() => chart.timeScale().unsubscribeSizeChange(restoreViewport));
      const observer = new ResizeObserver(() => {
        resizing = true;
        chart.resize(container.clientWidth, 440);
        chart.timeScale().setVisibleLogicalRange(viewport);
        resizing = false;
      });
      observer.observe(container);
      observers.push(observer);
      const schedule = () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          if (!disposed && !dragging && pending && (pending.from !== from || pending.to !== to)) {
            from = pending.from; to = pending.to; render();
          }
        }, 180);
      };
      const down = () => { interacted = true; dragging = true; };
      const up = () => { if (dragging) { dragging = false; schedule(); } };
      const wheel = () => { interacted = true; };
      const leave = () => link.cursor(peer, null);
      container.addEventListener("pointerdown", down, true);
      container.addEventListener("wheel", wheel, { passive: true, capture: true });
      container.addEventListener("pointerleave", leave);
      window.addEventListener("pointerup", up);
      listeners.push(() => {
        container.removeEventListener("pointerdown", down, true);
        container.removeEventListener("wheel", wheel, true);
        container.removeEventListener("pointerleave", leave);
        window.removeEventListener("pointerup", up);
      });
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (resizing || !interacted || !range) return;
        viewport = { ...range };
        if (!link.range(peer, range)) return;
        const visible = chart.timeScale().getVisibleRange();
        const a = dateFromTime(visible?.from), b = dateFromTime(visible?.to);
        if (a && b) { pending = { from: a, to: b }; schedule(); }
      });
      chart.subscribeCrosshairMove((event) => {
        if (!event.sourceEvent) return;
        const date = dateFromTime(event.time);
        if (!date || !event.point) { link.cursor(peer, null); return; }
        const candidates = lines.filter((l) => l.values.has(date));
        const closest = candidates.find((l) => l.line === event.hoveredSeries) ?? candidates.sort((a, b) =>
          Math.abs(a.line.priceToCoordinate(a.values.get(date)) - event.point.y)
          - Math.abs(b.line.priceToCoordinate(b.values.get(date)) - event.point.y))[0];
        link.cursor(peer, closest ? { date, key: closest.key } : null);
      });
    }
  }
  inputFrom.addEventListener("change", () => { from = inputFrom.value || dates[0]; render(); });
  inputTo.addEventListener("change", () => { to = inputTo.value || dates.at(-1); render(); });
  host.querySelector("[data-range-all]").addEventListener("click", () => { from = dates[0]; to = dates.at(-1); render(); });
  host.querySelectorAll("[data-range-year]").forEach((button, index) => button.addEventListener("click", () => {
    from = years[index].from; to = years[index].to; render();
  }));
  host.querySelectorAll("[data-ticker]").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) hidden.delete(Number(input.dataset.ticker));
    else hidden.add(Number(input.dataset.ticker));
    render();
  }));
  host.querySelector("[data-ticker-all]")?.addEventListener("change", (event) => {
    showAll = event.currentTarget.checked; render();
  });
  host.querySelector("[data-equity-display]")?.addEventListener("click", (event) => {
    display = display === "main" ? "cumulativeReturn" : "main";
    event.currentTarget.setAttribute("aria-pressed", String(display !== "main"));
    render();
  });
  render();
  import("/vendor/lightweight-charts.mjs").then((module) => {
    if (!disposed) { library = module; render(); }
  }).catch((error) => { if (!disposed) summary.textContent = `Chart library unavailable: ${error.message}. Run npm install in tools\\backtest-research-viewer.`; });
  return () => { disposed = true; clear(); };
}

let echartsPromise;
function loadECharts() {
  if (!echartsPromise) echartsPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/vendor/echarts.js";
    script.onload = () => resolve(window.echarts);
    script.onerror = () => { echartsPromise = undefined; script.remove(); reject(new Error("Local ECharts asset unavailable.")); };
    document.head.append(script);
  });
  return echartsPromise;
}

export function mountDiagnosticChart(host, option) {
  let disposed = false, chart, observer;
  host.classList.add("library-diagnostic-chart");
  loadECharts().then((library) => {
    if (disposed) return;
    host.replaceChildren();
    chart = library.init(host);
    chart.setOption({ backgroundColor: "transparent",
      textStyle: { color: getComputedStyle(host).getPropertyValue("--muted").trim() },
      grid: { left: 65, right: 25, bottom: 70, top: 25 },
      ...option, tooltip: { trigger: "item", ...option.tooltip, renderMode: "richText" } });
    observer = new ResizeObserver(() => chart.resize());
    observer.observe(host);
  }).catch((error) => { if (!disposed) host.textContent = `Diagnostic chart unavailable: ${error.message}`; });
  return () => { disposed = true; observer?.disconnect(); chart?.dispose(); };
}
import { combinedComparison, createChartLink, dateFromTime } from "../../../apps/operational-metrics-dashboard/src/data/linkedCharts.mjs";
