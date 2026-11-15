import { mountTimeCharts } from "./library-charts.mjs";

const PAD = { left: 82, right: 20, top: 22, bottom: 38 };

function escape(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

export function rangeStart(end, range) {
  const date = new Date(end);
  if (range === "all") return -Infinity;
  const months = { "1m": 1, "3m": 3, "1y": 12 }[range];
  if (!months || !Number.isFinite(date.getTime())) throw new Error("Invalid chart range.");
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() - months);
  const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(day, lastDay));
  return date.getTime();
}

export function chartModel(points, { kind = "equity", startingEquity, range = "all", width = 900, height = 260 } = {}) {
  const baseline = Number.isFinite(startingEquity) && startingEquity > 0 ? startingEquity : null;
  const data = points.map((point) => ({
    time: Date.parse(point.observedAt),
    observedAt: point.observedAt,
    value: kind === "return"
      ? baseline === null ? null : (point.equity - baseline) / baseline
      : kind === "drawdown" ? point.drawdown
        : kind === "sessionReturn" ? point.return : point.equity,
  })).filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
    .sort((left, right) => left.time - right.time);
  if (!data.length) return null;
  const end = data.at(-1).time;
  const start = rangeStart(end, range);
  const visible = data.filter((point) => point.time >= start);
  let minimum = Math.min(...visible.map((point) => point.value));
  let maximum = Math.max(...visible.map((point) => point.value));
  if (kind !== "equity") {
    minimum = Math.min(0, minimum);
    maximum = Math.max(0, maximum);
  }
  const margin = (maximum - minimum) * 0.08 || Math.max(Math.abs(maximum) * 0.01, 0.01);
  minimum -= margin;
  maximum += margin;
  if (kind === "drawdown") maximum = Math.max(0, ...visible.map((point) => point.value));
  const first = visible[0].time;
  const duration = end - first;
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = height - PAD.top - PAD.bottom;
  const y = (value) => PAD.top + (maximum - value) / (maximum - minimum) * plotHeight;
  return {
    points: visible.map((point) => ({
      ...point,
      x: PAD.left + (duration ? (point.time - first) / duration : 0.5) * plotWidth,
      y: y(point.value),
    })),
    ticks: Array.from({ length: 5 }, (_, index) => {
      const value = minimum + (maximum - minimum) * index / 4;
      return { value, y: y(value) };
    }),
    zeroY: y(0),
    kind,
    width,
    height,
  };
}

function chartValue(value, kind) {
  return kind === "equity"
    ? value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 })
    : `${(value * 100).toFixed(2)}%`;
}

export function renderTimeChart(model, label) {
  if (!model) return `<p class="empty">${escape(label)} is unavailable: no usable retained observations.</p>`;
  const points = model.points;
  const line = points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
  const bars = model.kind === "sessionReturn";
  const barWidth = Math.max(1, Math.min(12, (model.width - PAD.left - PAD.right) / points.length * 0.7));
  const marks = bars ? points.map((point) => `
    <rect class="${point.value < 0 ? "chart-loss" : "chart-gain"}" x="${point.x - barWidth / 2}"
      y="${Math.min(point.y, model.zeroY)}" width="${barWidth}" height="${Math.max(0.5, Math.abs(point.y - model.zeroY))}" />
  `).join("") : `
    <polyline class="chart-line ${model.kind === "drawdown" ? "chart-loss" : ""}" points="${line}" />
    ${points.length === 1 ? `<circle class="chart-dot" cx="${points[0].x}" cy="${points[0].y}" r="4" />` : ""}
  `;
  return `
    <div class="time-chart">
      <svg viewBox="0 0 ${model.width} ${model.height}" role="img" aria-label="${escape(label)}">
        ${model.ticks.map((tick) => `
          <line class="chart-grid" x1="${PAD.left}" x2="${model.width - PAD.right}" y1="${tick.y}" y2="${tick.y}" />
          <text class="chart-label" x="${PAD.left - 10}" y="${tick.y + 4}" text-anchor="end">${escape(chartValue(tick.value, model.kind))}</text>
        `).join("")}
        ${marks}
        ${(model.width < 500 ? [0, points.length - 1] : [0, Math.floor((points.length - 1) / 2), points.length - 1]).filter((value, index, all) => all.indexOf(value) === index).map((index) => `
          <text class="chart-label" x="${points[index].x}" y="${model.height - 10}" text-anchor="${index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}">${escape(points[index].observedAt.slice(0, 10))}</text>
        `).join("")}
        <line class="chart-crosshair hidden" x1="0" x2="0" y1="${PAD.top}" y2="${model.height - PAD.bottom}" />
        <circle class="chart-marker hidden" r="4" cx="0" cy="0" />
      </svg>
      <input class="chart-scrubber" type="range" min="0" max="${points.length - 1}" value="${points.length - 1}" aria-label="${escape(label)} observation">
      <output class="chart-readout" aria-live="polite">${escape(points.at(-1).observedAt)} · ${escape(chartValue(points.at(-1).value, model.kind))}</output>
    </div>
  `;
}

function bindChart(container, model) {
  if (!model) return;
  const svg = container.querySelector("svg");
  const slider = container.querySelector("input");
  const readout = container.querySelector("output");
  const crosshair = container.querySelector(".chart-crosshair");
  const marker = container.querySelector(".chart-marker");
  function select(index) {
    const point = model.points[index];
    slider.value = String(index);
    crosshair.setAttribute("x1", point.x);
    crosshair.setAttribute("x2", point.x);
    marker.setAttribute("cx", point.x);
    marker.setAttribute("cy", point.y);
    crosshair.classList.remove("hidden");
    marker.classList.remove("hidden");
    readout.textContent = `${point.observedAt} · ${chartValue(point.value, model.kind)}`;
  }
  slider.addEventListener("input", () => select(Number(slider.value)));
  svg.addEventListener("pointermove", (event) => {
    const transform = svg.getScreenCTM();
    if (!transform) return;
    const position = new DOMPoint(event.clientX, event.clientY).matrixTransform(transform.inverse());
    let nearest = 0;
    for (let index = 1; index < model.points.length; index += 1) {
      if (Math.abs(model.points[index].x - position.x) < Math.abs(model.points[nearest].x - position.x)) nearest = index;
    }
    select(nearest);
  });
}

export function mountPerformanceCharts(host, equity, startingEquity) {
  return mountTimeCharts(host, [{ name: "Strategy", points: equity.sessionEquity || [] }],
    { kind: "equity", startingEquity });
}
