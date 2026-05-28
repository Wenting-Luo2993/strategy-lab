# NOTE-004: TP Multiplier Flaw Discovery and No-TP Dominance

**Date**: 2026-05-28  
**Related**: HYP-003, EXP-032, EXP-033 through EXP-068  
**Supersedes context of**: EXP-004 (HYP-002 sweep)

---

## Discovery

The initial parameter sweep (EXP-004) had a critical design flaw: the `tp_multiplier` grid was
`[1.5, 2.0, 3.0]` — it never tested `tp_multiplier=0` (no take-profit, let trades run to EOD).

This was identified by the user after reviewing EXP-004 results, which incorrectly concluded that
TP=3.0x was optimal. The user noted that **ORB is a convex payoff structure** — a fundamentally
important insight that the initial sweep failed to test.

---

## ORB Convexity Explained

The ORB setup creates an asymmetric payoff by construction:

- **Losers are bounded**: If price breaks out of the ORB range but then reverses, the stop is at
  the opposite ORB boundary. Maximum loss ≈ 1× ORB range.
- **Winners are unbounded**: A genuine trending breakout can run 3×, 5×, even 10× the ORB range
  during a strong momentum day. There is no natural ceiling.

This is a **convex payoff**: limited downside, unlimited upside. It's exactly what a momentum
breakout strategy is designed to capture.

**Introducing a take-profit multiplier destroys this convexity** by converting the unlimited upside
into a capped win. The right tail of the P&L distribution is truncated, reducing:
- Expectancy (average R-multiple)
- Tail ratio (right tail / left tail)
- Composite score

The win rate increases slightly (winners hit TP before reversing), but this does not compensate for
losing the large trending days that define ORB's alpha.

---

## Corrected Sweep Results (EXP-032, 36 combos)

Grid: `orb_duration=[5, 10, 15]min × tp=[0, 1.5, 2.0, 3.0] × risk=[1, 2, 3]%`

### Top 5 Results

| Rank | ORB | TP | Risk | Composite | Expectancy | Tail Ratio | Win Rate |
|------|-----|----|------|-----------|------------|------------|----------|
| 1 | 5min | none | 1% | 0.7316 | +0.291R | 4.65 | 33.9% |
| 2 | 5min | none | 2% | 0.7296 | +0.291R | 4.65 | 33.9% |
| 3 | 5min | none | 3% | 0.7278 | +0.291R | 4.65 | 33.9% |
| 4 | 5min | 3.0x | 3% | 0.6946 | +0.180R | 2.28 | 45.8% |
| 5 | 5min | 3.0x | 2% | 0.6904 | +0.180R | 2.28 | 45.8% |

### No-TP vs Best TP Comparison

| Metric | TP=none (Rank 1) | TP=3.0x (old Rank 1) | Improvement |
|--------|-----------------|----------------------|-------------|
| Composite | 0.7316 | 0.6946 | +5.4% |
| Expectancy | +0.291R | +0.180R | +61.7% |
| Tail ratio | 4.65 | 2.28 | +103.9% |
| Win rate | 33.9% | 45.8% | -26% (expected) |
| Sharpe | 2.34 | 2.19 | +6.8% |

The win rate drop (33.9% vs 45.8%) is expected and fine — we win less often but much bigger when
we do win.

---

## Robustness & Surface Analysis

- **Robustness score**: 0.920 (expectancy std ±0.025R under slippage perturbation)
- **Surface cliffs**: 0 (no sharp parameter cliffs)
- **Surface plateaus**: 7 (very stable landscape)

The no-TP config is robust and not curve-fit. The plateau structure confirms that the no-TP
advantage holds broadly, not just at a single point.

---

## Production Config Update

Based on EXP-032 findings:
- `orb_duration_minutes: 5` (confirmed)
- `tp_multiplier: 0` (updated from 2.0)
- `risk_pct: 0.01` (conservative choice from the best composite rank)

`vibe/rulesets/orb_production.yaml` updated to reflect `multiplier: 0`.

---

## Lessons Learned

1. **Always include the boundary case**: When sweeping a multiplier parameter, always include 0
   (no multiplier). The absence of a value in the grid is a hidden assumption.
2. **Think about payoff structure first**: Before sweeping, ask "what does the payoff look like?"
   For convex strategies, capping wins is theoretically wrong. Run no-TP as the baseline.
3. **High win rate ≠ better**: TP=3.0x had 45.8% win rate vs 33.9% for no-TP, but lower
   expectancy (+0.180R vs +0.291R). Win rate alone misleads; expectancy and tail ratio tell the
   true story.
