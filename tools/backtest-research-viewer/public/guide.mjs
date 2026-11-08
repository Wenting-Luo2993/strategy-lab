// Reading guide: "?" coachmarks next to dashboard sections plus a step-by-step
// tour of the current view. Content is static data so it can be tested without
// a DOM; initGuide wires it to the page.

export const GUIDE_STORAGE_KEY = "backtest-viewer.guide";
export const GUIDE_SCOPES = { page: "main", experiment: "#experiment-detail", run: "#detail-content" };

export const GUIDE_STEPS = [
  // Main page
  {
    id: "overview", scope: "page", selector: "#overview", placement: "corner",
    title: "Research at a glance",
    what: "Counts of everything in this database: experiments, segment runs by lifecycle state, registry records and publication health.",
    read: "Amber numbers need attention. 'Awaiting validation' is normal: fixed-rule, paired and platform-validation studies are recorded without a validator and rest there. 'Execution failed' or 'Dead letters' above zero means something broke.",
    dive: "This strip only says whether the database is healthy. The research itself starts in Experiments.",
  },
  {
    id: "experiments", scope: "page", headings: ["Experiments"],
    title: "Experiments: one question each",
    what: "Each row is one controlled question: a walk-forward study, a fixed-rule study, a paired comparison or a platform validation.",
    read: "'Procedure' says whether the code finished. 'Research reliability' says whether the evidence can be trusted. They are separate: a completed procedure can still be unreliable evidence.",
    dive: "Open an experiment by its title, then use 'Tour this view' in its toolbar for a section-by-section walk-through.",
  },
  {
    id: "segment-runs", scope: "page", selector: "#individual-runs > summary",
    title: "Segment runs (low level)",
    what: "Every single backtest of one setting over one date window, including older runs that were never grouped into an experiment.",
    read: "Use this to audit, not to judge. One run is one sample and says little on its own. Filter by sample role to keep training (in-sample) runs apart from test (out-of-sample) runs.",
    dive: "Open a run to see its equity curve, trades, configuration and provenance.",
  },
  {
    id: "registry", scope: "page", headings: ["Registry"],
    title: "Registry: the research memory",
    what: "Hypotheses, notes, rejected ideas, artifacts and their lineage.",
    read: "Check here before starting a new idea: it may already have been tried and rejected, with the reason recorded.",
    dive: "Filter Type = hypothesis to see what is registered.",
  },
  {
    id: "publications", scope: "page", headings: ["Publication and outbox health"],
    title: "Publication health",
    what: "Whether records have been synced out of this local database.",
    read: "Plumbing only: it says nothing about strategy quality. Pending or failed rows matter only if you rely on the synced copy.",
  },

  // Any experiment
  {
    id: "experiment-kind", scope: "experiment", selector: ".experiment-heading .eyebrow",
    title: "What kind of experiment is this?",
    what: "The label names the kind. A walk-forward selects parameters. A fixed-rule study measures one frozen rule. A paired comparison measures one change against a baseline. A platform validation tests the tool itself.",
    read: "Each kind answers a different question, so read each with its own yardstick. 'Procedure execution' only says whether it ran to the end.",
    dive: "The experiment taxonomy is in section 3 of docs/backtest-research-summaries/2026-10-31-backtest-research-north-star.md.",
  },
  {
    id: "evidence-role", scope: "experiment", selector: "p.notice", contains: "cannot confirm",
    title: "Evidence role",
    what: "Which data this study used, and therefore what it is allowed to prove.",
    read: "Exploration data can reject or refine an idea but never confirm it, because you have already looked at it. Only a run on confirmation tickers you have never examined can support a claim of an edge.",
    dive: "Section 2 (evidence map) of the ORB edge research plan lists which tickers play which role.",
  },
  {
    id: "trial-count", scope: "experiment", selector: "[data-kind-trial] .notice",
    title: "Trial count",
    what: "How many variations this research programme has tried so far, this study included.",
    read: "The more variations you try, the more likely one looks good by luck. A promising result after 40 trials needs stronger evidence than the first one did.",
    dive: "python scripts/show_trial_ledger.py --programme <name> prints the full ledger and the alpha budget.",
  },

  // Fixed-rule study and paired comparison
  {
    id: "outcome", scope: "experiment", headings: ["Outcome"],
    title: "Outcome: is there an edge?",
    what: "Mean net R per trade with its 95% interval (clustered by trading day), compared with the minimum edge worth trading (0.05R) and the minimum detectable effect (MDE).",
    read: "Supported: the whole interval is above the minimum edge. Contradicted: the whole interval is below it, so a meaningful edge is ruled out. Underpowered: the interval straddles it, so you need more trades rather than a verdict. The interval's width matters more than the mean.",
    dive: "Next check By year and By symbol: a real edge should not depend on one year or one ticker.",
  },
  {
    id: "paired-difference", scope: "experiment", headings: ["Difference"],
    title: "Paired difference: does the change help?",
    what: "For each session, variant R minus baseline R on the same day, averaged over all sessions.",
    read: "Pairing by day removes shared market noise, so it detects smaller improvements than comparing two separate studies. Supported means the improvement clears the minimum edge per session. An interval that includes 0 means there is no evidence the change helps.",
    dive: "Change one lever at a time; each comparison counts as a trial.",
  },
  {
    id: "cumulative-r", scope: "experiment", headings: ["Cumulative R and drawdown", "Cumulative R, baseline vs variant"],
    title: "Cumulative R and drawdown",
    what: "Running total of net R per trading day, and below it how far that total sits under its previous peak.",
    read: "A steady slope means the edge is spread across time. A line that is flat for years and then jumps means a few days carry the result. Deep or long drawdowns show what living with the strategy would feel like. For paired studies, a steadily widening gap between the lines means a consistent improvement.",
    dive: "If one stretch dominates, open By year.",
  },
  {
    id: "r-distribution", scope: "experiment", headings: ["Net R distribution"],
    title: "Net R distribution",
    what: "How many trades landed in each R bucket.",
    read: "ORB usually shows many losses near -1R (stopped out) and a long right tail. A mean carried by a handful of +5R trades is fragile: check how many trades sit in the far-right bars.",
    dive: "Exit reasons shows where the tails come from.",
  },
  {
    id: "by-year", scope: "experiment", headings: ["By year", "Difference by year"],
    title: "By year",
    what: "The same estimate split by calendar year (for paired studies, the difference).",
    read: "Look for the same sign in most years, not for every year to be significant: each year has few trades, so wide intervals are normal. An edge that is positive early and negative later is a regime story, not a stable edge.",
    dive: "Conditions (research plan Step 3) test whether a market regime explains it.",
  },
  {
    id: "by-symbol", scope: "experiment", headings: ["By symbol", "Difference by symbol"],
    title: "By symbol",
    what: "The same estimate split by ticker.",
    read: "If one ticker carries the result, the edge may belong to that ticker's volatility rather than to ORB. With five exploration symbols, a pattern that appears on only one is probably noise.",
  },
  {
    id: "exit-reasons", scope: "experiment", headings: ["Exit reasons"],
    title: "Exit reasons",
    what: "Share of trades and mean R by how each trade ended: stop, take-profit, trailing stop or end of day.",
    read: "Shows where the money is made and lost. If end-of-day exits carry the profit, the edge lies in holding winners, so exit changes are the levers to test.",
  },
  {
    id: "excursions", scope: "experiment", headings: ["Excursions"],
    title: "Excursions (MAE vs MFE)",
    what: "Each dot is one trade. Horizontal: the worst point against you (MAE, in R). Vertical: the best point in your favour (MFE, in R).",
    read: "Many stopped trades with high MFE mean winners turned into losers, so a trailing or break-even stop might help. Winners with MAE near -1R barely survived the stop, so a tighter stop would have cut them.",
    dive: "Turn an idea from this chart into a paired comparison that changes one lever.",
  },
  {
    id: "cost-sensitivity", scope: "experiment", headings: ["Cost sensitivity"],
    title: "Cost sensitivity",
    what: "How much extra cost per trade, in basis points, would wipe out the mean net R.",
    read: "If the break-even extra cost is smaller than a realistic error in your slippage estimate (a few bps), the edge is not tradeable even if it is real.",
  },
  {
    id: "conditions", scope: "experiment", headings: ["Conditions"],
    title: "Conditions (diagnostic)",
    what: "The same estimate split into low, mid and high thirds of a feature known before entry, such as gap size, opening volume or opening-range width.",
    read: "Diagnostic means it suggests but cannot prove: every split is another trial. Trust a pattern only if it is monotonic (low, mid, high in order), the high-minus-low interval excludes 0, and it holds in most years and symbols.",
    dive: "A promising condition becomes a hypothesis card and is then confirmed on fresh tickers.",
  },
  {
    id: "platform-validation", scope: "experiment", headings: ["Platform validation"],
    title: "Platform validation: can the tool be trusted?",
    what: "Runs the real engine on synthetic data where the true answer is known.",
    read: "V6 (no edge planted): the tool should wrongly claim an edge in about 2.5% of studies. Much more means its intervals are overconfident. V7 (edge planted): the detection rate should match the predicted power, and the interval should cover the true edge about 95% of the time.",
    dive: "Passing both is the gate (research plan Step 0) before trusting any ORB result.",
  },

  // Walk-forward experiments
  {
    id: "confidence-profile", scope: "experiment", headings: ["Statistical confidence profile"],
    title: "Statistical confidence profile",
    what: "Uncertainty of the stitched held-out (test) expectancy across all folds.",
    read: "Focus on the intervals rather than the t-statistic. If an interval includes 0, or values you would not trade for, the walk-forward has not shown an edge. These intervals treat trades as independent, so they can be optimistic when several trades share a day.",
  },
  {
    id: "reliability-criteria", scope: "experiment", headings: ["Reliability criteria"],
    title: "Reliability criteria",
    what: "Pass/fail checks registered before the study ran.",
    read: "One failed criterion makes the evidence unreliable, however good the mean looks. The info icon on each row explains that criterion.",
  },
  {
    id: "walk-forward-overview", scope: "experiment", headings: ["Walk-forward overview"],
    title: "Walk-forward overview",
    what: "One row per fold: the setting picked on validation data, and how it did on the following unseen test window.",
    read: "This judges the procedure 're-select every fold', not any single setting, so different picks per fold are expected. If the pick jumps around and test results are mixed, selection is fitting noise.",
    dive: "To ask which single setting is best, use a fixed-rule study per setting instead.",
  },
  {
    id: "fold-detail", scope: "experiment", selector: ".experiment-fold > summary",
    title: "Inside a fold",
    what: "Train, then validate and select, then a frozen test, with every candidate's evidence.",
    read: "The highlighted row is the selected setting. Compare its validation score with its test score: a big drop suggests overfitting. The note under the table shows how close the runner-up was.",
    dive: "Click a run alias to open that run.",
  },
  {
    id: "provenance", scope: "experiment", headings: ["Controlled configuration & provenance"],
    title: "Configuration and provenance",
    what: "Plan hash, varied axes and reference configuration.",
    read: "Use it to reproduce the study, or to explain why two experiments disagree.",
  },

  // Run detail
  {
    id: "run-sample", scope: "run", selector: ".sample-notice",
    title: "One run: which sample?",
    what: "A single setting evaluated over one window. This note says whether it is training, validation or test data.",
    read: "Training results are in-sample and optimistic. Only test (out-of-sample) runs say how a frozen choice performs on unseen data.",
    dive: "The tabs below hold performance, trades, configuration, validation checks and provenance.",
  },
  {
    id: "run-equity", scope: "run", headings: ["Strategy equity", "Strategy return"],
    title: "Equity curve",
    what: "Account value (or return) through the run.",
    read: "Judge the shape, not the end level: a smooth climb differs from a few big jumps.",
  },
  {
    id: "run-drawdown", scope: "run", headings: ["Drawdown · intraday"],
    title: "Drawdown",
    what: "Distance below the previous equity peak.",
    read: "The worst value is the pain you would have sat through. Ask whether you would have kept trading at that point.",
  },
  {
    id: "run-session-returns", scope: "run", headings: ["Session returns"],
    title: "Session returns",
    what: "One bar per trading session.",
    read: "Clusters of losing sessions show bad regimes. A few huge bars carrying the total show fragility.",
  },
  {
    id: "run-statistics", scope: "run", headings: ["Recorded statistics"],
    title: "Recorded statistics",
    what: "Metrics stored when the run finished.",
    read: "Expectancy in R and the trade count matter most. Win rate and Sharpe are secondary for ORB, because a low win rate with large winners can still be a good strategy.",
  },
  {
    id: "run-trades", scope: "run", headings: ["Trades"],
    title: "Trades",
    what: "Individual retained trades.",
    read: "Spot-check a few against a chart: entry after the breakout, stop and exit where the rules say. This is how the V3 hand check works.",
  },
];

export function stepsForScope(scope) {
  return GUIDE_STEPS.filter((step) => step.scope === scope);
}

export function readGuidePreference(storage) {
  try {
    const value = storage?.getItem(GUIDE_STORAGE_KEY);
    return value === null || value === undefined ? true : value === "on";
  } catch {
    return true;
  }
}

export function writeGuidePreference(storage, on) {
  try {
    storage?.setItem(GUIDE_STORAGE_KEY, on ? "on" : "off");
  } catch {
    // Storage can be unavailable (private mode); the toggle still works for this page.
  }
}

/** Text that belongs to the element itself, ignoring badges, hints and other children. */
export function ownText(element) {
  return [...(element?.childNodes || [])]
    .filter((child) => child.nodeType === 3)
    .map((child) => child.textContent)
    .join("")
    .replace(/\s+/g, " ")
    .trim();
}

export function matchesHeading(step, text) {
  return Array.isArray(step.headings) && step.headings.includes(text);
}

export function findStepTarget(root, step) {
  if (!root) return null;
  if (step.selector) {
    for (const element of root.querySelectorAll(step.selector)) {
      if (!step.contains || element.textContent.includes(step.contains)) return element;
    }
    return null;
  }
  for (const element of root.querySelectorAll("h2, h3, h4, summary")) {
    if (matchesHeading(step, ownText(element))) return element;
  }
  return null;
}

/** Wire the guide to the page. Returns a small control surface for tests and debugging. */
export function initGuide(doc = globalThis.document, storage = globalThis.localStorage) {
  if (!doc) return null;
  const toggle = doc.getElementById("guide-toggle");
  const tourButton = doc.getElementById("guide-tour");
  const dialogs = { experiment: doc.getElementById("experiment-dialog"), run: doc.getElementById("detail") };
  let enabled = readGuidePreference(storage);
  let tour = null;
  let focused = null;
  let scheduled = false;

  const bubble = buildBubble(doc);
  const observer = new MutationObserver(() => schedule());

  function schedule() {
    if (scheduled || !enabled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      decorate();
    });
  }

  function decorate() {
    for (const [scope, selector] of Object.entries(GUIDE_SCOPES)) {
      const root = doc.querySelector(selector);
      if (!root) continue;
      for (const step of stepsForScope(scope)) {
        const target = findStepTarget(root, step);
        if (!target || target.querySelector(":scope > .guide-hint")) continue;
        const hint = doc.createElement("button");
        hint.type = "button";
        hint.className = "guide-hint";
        hint.dataset.guideHint = step.id;
        hint.setAttribute("aria-label", `Guide: ${step.title}`);
        hint.textContent = "?";
        hint.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          openTour(scope, [{ step, target }], 0, true);
        });
        if (step.placement === "corner") target.classList.add("guide-anchor");
        target.append(hint);
      }
    }
    for (const dialog of Object.values(dialogs)) {
      const toolbar = dialog?.querySelector(":scope > form");
      if (!toolbar || toolbar.querySelector(".guide-dialog-tour")) continue;
      const button = doc.createElement("button");
      button.type = "button";
      button.className = "guide-dialog-tour";
      button.textContent = "Tour this view";
      button.addEventListener("click", () => startTour());
      // The experiment toolbar is a flex row (title, close); the run dialog floats its close button right.
      const close = toolbar.querySelector("button");
      if (close && toolbar.classList.contains("experiment-toolbar")) close.before(button);
      else toolbar.append(button);
    }
  }

  function undecorate() {
    doc.querySelectorAll(".guide-hint, .guide-dialog-tour").forEach((element) => element.remove());
    doc.querySelectorAll(".guide-anchor").forEach((element) => element.classList.remove("guide-anchor"));
  }

  function currentScope() {
    if (dialogs.run?.open) return "run";
    if (dialogs.experiment?.open) return "experiment";
    return "page";
  }

  function visibleItems(scope) {
    const root = doc.querySelector(GUIDE_SCOPES[scope]);
    return stepsForScope(scope)
      .map((step) => ({ step, target: findStepTarget(root, step) }))
      .filter(({ target }) => target && target.getClientRects().length > 0);
  }

  function startTour() {
    if (!enabled) setEnabled(true);
    const scope = currentScope();
    const items = visibleItems(scope);
    if (items.length) openTour(scope, items, 0, false);
  }

  function openTour(scope, items, index, single) {
    tour = { scope, items, index, single };
    showStep();
  }

  function showStep() {
    const { step, target } = tour.items[tour.index];
    focused?.classList.remove("guide-focus");
    focused = target;
    target.classList.add("guide-focus");
    if (!tour.single) target.scrollIntoView({ block: "center" });
    bubble.progress.textContent = tour.single ? "Reading guide" : `Step ${tour.index + 1} of ${tour.items.length}`;
    bubble.title.textContent = step.title;
    bubble.what.textContent = step.what;
    bubble.read.textContent = step.read;
    bubble.diveRow.hidden = !step.dive;
    bubble.dive.textContent = step.dive || "";
    bubble.back.hidden = tour.single;
    bubble.back.disabled = tour.index === 0;
    bubble.next.hidden = tour.single;
    bubble.next.textContent = tour.index === tour.items.length - 1 ? "Finish" : "Next";
    bubble.tourHere.hidden = !tour.single;
    present(bubble.element);
    position();
    bubble.next.hidden ? bubble.close.focus() : bubble.next.focus();
  }

  function present(element) {
    const host = dialogs.run?.open ? dialogs.run : dialogs.experiment?.open ? dialogs.experiment : doc.body;
    const supportsPopover = typeof element.showPopover === "function";
    if (supportsPopover && element.matches(":popover-open")) element.hidePopover();
    if (element.parentElement !== host) host.append(element);
    if (supportsPopover) element.showPopover();
    else element.hidden = false;
  }

  function position() {
    if (!tour) return;
    const item = tour.items[tour.index];
    if (!item.target.isConnected) {
      const root = doc.querySelector(GUIDE_SCOPES[tour.scope]);
      const replacement = findStepTarget(root, item.step);
      if (!replacement) return closeTour();
      item.target = replacement;
      replacement.classList.add("guide-focus");
      focused = replacement;
    }
    const margin = 12;
    const anchor = item.target.getBoundingClientRect();
    const box = bubble.element.getBoundingClientRect();
    const viewHeight = doc.documentElement.clientHeight;
    const viewWidth = doc.documentElement.clientWidth;
    let top = anchor.bottom + margin;
    if (top + box.height > viewHeight - margin && anchor.top - margin - box.height > margin) {
      top = anchor.top - margin - box.height;
    }
    top = Math.max(margin, Math.min(top, viewHeight - box.height - margin));
    const left = Math.max(margin, Math.min(anchor.left, viewWidth - box.width - margin));
    bubble.element.style.top = `${top}px`;
    bubble.element.style.left = `${left}px`;
  }

  function closeTour() {
    tour = null;
    focused?.classList.remove("guide-focus");
    focused = null;
    const element = bubble.element;
    if (typeof element.hidePopover === "function") {
      if (element.matches(":popover-open")) element.hidePopover();
    } else {
      element.hidden = true;
    }
  }

  function setEnabled(on) {
    enabled = on;
    writeGuidePreference(storage, on);
    doc.body.classList.toggle("guide-on", on);
    if (toggle) {
      toggle.setAttribute("aria-pressed", String(on));
      toggle.textContent = on ? "Guide: on" : "Guide: off";
    }
    if (on) {
      observer.observe(doc.body, { childList: true, subtree: true });
      decorate();
    } else {
      observer.disconnect();
      closeTour();
      undecorate();
    }
  }

  bubble.back.addEventListener("click", () => {
    if (tour && tour.index > 0) {
      tour.index -= 1;
      showStep();
    }
  });
  bubble.next.addEventListener("click", () => {
    if (!tour) return;
    if (tour.index >= tour.items.length - 1) return closeTour();
    tour.index += 1;
    showStep();
  });
  bubble.close.addEventListener("click", () => closeTour());
  bubble.tourHere.addEventListener("click", () => startTour());
  toggle?.addEventListener("click", () => setEnabled(!enabled));
  tourButton?.addEventListener("click", () => startTour());
  doc.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && tour) {
      event.preventDefault();
      event.stopPropagation();
      closeTour();
    }
  }, true);
  for (const [scope, dialog] of Object.entries(dialogs)) {
    dialog?.addEventListener("close", () => {
      if (tour?.scope === scope) closeTour();
    });
  }
  globalThis.addEventListener?.("resize", () => position());
  doc.addEventListener("scroll", () => position(), true);

  setEnabled(enabled);
  return { setEnabled, startTour, closeTour, isEnabled: () => enabled };
}

function buildBubble(doc) {
  const element = doc.createElement("div");
  element.id = "guide-bubble";
  element.className = "guide-bubble";
  element.setAttribute("role", "dialog");
  element.setAttribute("aria-labelledby", "guide-bubble-title");
  if (typeof element.showPopover === "function") element.setAttribute("popover", "manual");
  else element.hidden = true;
  const make = (tag, className, text) => {
    const child = doc.createElement(tag);
    if (className) child.className = className;
    if (text) child.textContent = text;
    return child;
  };
  const progress = make("p", "guide-progress");
  const title = make("h3");
  title.id = "guide-bubble-title";
  const what = make("p");
  const readRow = make("p");
  const read = make("span");
  readRow.append(make("strong", "", "How to read it: "), read);
  const diveRow = make("p");
  const dive = make("span");
  diveRow.append(make("strong", "", "Dive deeper: "), dive);
  const actions = make("div", "guide-actions");
  const tourHere = make("button", "", "Tour this view");
  const back = make("button", "", "Back");
  const next = make("button", "primary", "Next");
  const close = make("button", "", "Close");
  [tourHere, back, next, close].forEach((button) => { button.type = "button"; });
  actions.append(tourHere, back, next, close);
  element.append(progress, title, what, readRow, diveRow, actions);
  doc.body.append(element);
  return { element, progress, title, what, read, diveRow, dive, back, next, close, tourHere };
}
