# NOTE-005: HYP-004 Trailing Stop Analysis (EXP-070/071/072)

**Date**: 2026-05-29  
**Related**: HYP-004, EXP-069, EXP-070, EXP-071, EXP-072  
**Objective**: Evaluate break-even trailing stop variants for loser reduction while preserving ORB right-tail convexity.

---

## 1. Executive Summary

Across the tested variants, `trigger_r=3.0` and `plus_ticks=1` (EXP-072) is the strongest overall candidate:

- Best expectancy among tested variants (`+0.2929R`)
- Best total PnL (`$9.38M`)
- Very strong right-tail preservation versus baseline EXP-069
- Trade-off: worse max drawdown than baseline and EXP-070/071

Practical ranking for deployment consideration:
1. EXP-072 (primary)
2. EXP-071 (fallback if drawdown tolerance is tighter)
3. EXP-070 (not recommended)

---

## 2. Baseline and Variant Metrics

Baseline EXP-069 (`no trailing`, `tp_multiplier=0`):
- Expectancy: `+0.2912R`
- Win rate: `33.9%`
- Losing trades: `1107`
- Total PnL: `$9.03M`
- Max drawdown: `-24.79%`

### EXP-070 (`trigger_r=2.0`, `plus_ticks=3`)
- Expectancy: `+0.2742R`
- Win rate: `40.8%`
- Losing trades: `991`
- Total PnL: `$6.97M`
- Max drawdown: `-25.05%`

### EXP-071 (`trigger_r=2.5`, `plus_ticks=1`)
- Expectancy: `+0.2906R`
- Win rate: `38.3%`
- Losing trades: `1033`
- Total PnL: `$9.09M`
- Max drawdown: `-26.69%`

### EXP-072 (`trigger_r=3.0`, `plus_ticks=1`)
- Expectancy: `+0.2929R`
- Win rate: `36.9%`
- Losing trades: `1058`
- Total PnL: `$9.38M`
- Max drawdown: `-27.44%`

---

## 3. Tail Preservation Findings (vs EXP-069)

### EXP-070
- Top-20% winners retaining >=90% PnL: `0.0%`
- Median top-tail PnL capture ratio: `~0.74`
- Interpretation: protects downside and boosts win rate, but clips the right tail too hard.

### EXP-071
- Top-20% winners retaining >=90% PnL: `76.3%`
- Top-10%: `89.5%`
- Top-5%: `93.1%`
- Median top-tail capture ratio: `~0.92-0.94`
- Interpretation: materially better tail preservation than EXP-070, with moderate clipping.

### EXP-072
- Top-20% winners retaining >=90% PnL: `98.2%`
- Top-10%: `98.2%`
- Top-5%: `96.6%`
- Median top-tail capture ratio: `~1.004`
- Interpretation: strongest tail preservation, aligns best with convex ORB behavior.

---

## 4. Parameter Behavior Notes (`trigger_r`, `plus_ticks`)

Observed behavior in this tested slice:

1. `plus_ticks` effect:
- Larger lock-in (`plus_ticks=3`) at lower trigger (EXP-070) appears to over-tighten exits and truncate right-tail PnL.
- Smaller lock-in (`plus_ticks=1`) preserves trend participation better.

2. `trigger_r` effect (with `plus_ticks=1`):
- Raising trigger from `2.5` to `3.0` improved expectancy and total PnL in this sample.
- Higher trigger delays stop movement, allowing more upside continuation.
- Trade-off: drawdown and loser count can worsen versus lower-trigger alternatives.

3. Risk profile trade-off:
- Lower trigger / tighter lock-in -> higher win rate and fewer losers, but reduced tail monetization.
- Higher trigger / lighter lock-in -> better convex payoff capture, but generally higher drawdown risk.

---

## 5. Promotion Checklist (Short Form)

Checklist dimensions used here:
- Expectancy quality
- Right-tail preservation
- Drawdown acceptability
- Simplicity/stability for production operations

### EXP-070
- Expectancy: FAIL (below baseline)
- Tail preservation: FAIL
- Drawdown: PASS-ish (best among variants, but not enough)
- Verdict: **DO NOT PROMOTE**

### EXP-071
- Expectancy: PASS (near baseline)
- Tail preservation: PASS
- Drawdown: CONDITIONAL (worse than baseline)
- Verdict: **PROMOTE CANDIDATE (FALLBACK)**

### EXP-072
- Expectancy: PASS (best)
- Tail preservation: PASS (best)
- Drawdown: CONDITIONAL (worst drawdown)
- Verdict: **PROMOTE CANDIDATE (PRIMARY), WITH DD GUARDRAILS**

---

## 6. Deployment Guidance

Recommended next step:
- Deploy EXP-072 as primary paper/live candidate with explicit drawdown guardrails.
- Keep EXP-071 as contingency profile if live drawdown exceeds risk tolerance.

Suggested guardrails:
1. Daily/rolling drawdown kill-switch.
2. Automatic risk throttle if drawdown exceeds threshold.
3. Ongoing monitoring of top-tail capture versus baseline behavior.
