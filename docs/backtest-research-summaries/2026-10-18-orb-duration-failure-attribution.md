# ORB-duration descriptive failure attribution

**Date:** 2026-10-18  
**Experiment:** `01a1517f-9e61-70e2-8e9b-5e83eba78711`  
**Scope:** Descriptive analysis of the 80 frozen QQQ TEST trades. This is
hypothesis-generating evidence, not a new selection exercise.

## Executive conclusion

The reliability policy correctly rejects promotion of the registered
ORB-duration claim, but the current sample does **not** establish that the true
strategy expectancy is negative.

- Stitched TEST expectancy is `-0.0245R` over 80 trades.
- A deterministic 100,000-sample trade bootstrap gives a 95% interval of
  approximately `[-0.331R, +0.312R]`.
- Only 42.8% of bootstrap samples have positive mean expectancy.
- Fold-level intervals are wider still because each fold has only 40 trades.
- The result is highly dependent on a small number of large EOD winners.
- The short side is the clearest observed drag, but its own interval still
  crosses zero and this split was inspected after TEST outcomes were known.

The decision and the diagnosis therefore answer different questions:

> **Promotion decision:** reject the current claim because the observed held-out
> expectancy and robustness checks failed the pre-registered policy.

> **Scientific diagnosis:** inconclusive about the exact economic mechanism
> because two folds, five calendar months, and 80 trades are too little evidence
> for confident subgroup attribution.

## Data and method

The analysis reads only the immutable TEST runs linked to the completed study:

| Fold | Selected ORB duration | TEST trades | TEST expectancy |
| --- | ---: | ---: | ---: |
| 1 | 15 minutes | 40 | `+0.0181R` |
| 2 | 5 minutes | 40 | `-0.0671R` |
| Stitched | Fold-selected | 80 | `-0.0245R` |

Net R is reconstructed using the same definition as the engine:

```text
net R = (gross trade P&L - commission) / initial risk
```

The bootstrap resamples the 80 stored trade R-multiples with replacement using
a fixed seed and 100,000 repetitions. This gives a reproducible descriptive
uncertainty estimate. It assumes trades are exchangeable and is probably
optimistic if outcomes are serially dependent; there are too few months and
folds for a useful higher-level block bootstrap.

## Aggregate behavior

| Metric | Observed |
| --- | ---: |
| Trades | 80 |
| Mean net expectancy | `-0.0245R` |
| Median trade | `-1.0127R` |
| Standard deviation | `1.4772R` |
| Win rate | 36.25% |
| Average winner | `+1.5782R` |
| Average loser | `-0.9358R` |
| Largest winner | `+6.0730R` |
| Largest loser | `-1.1588R` |
| Maximum chronological losing streak | 6 |
| 95% trade-bootstrap interval | `[-0.3309R, +0.3119R]` |
| Bootstrap probability mean is positive | 42.8% |
| Bootstrap probability mean is at least `+0.10R` | 21.8% |

The observed payoff ratio implies a break-even win rate of about 37.2%. The
actual win rate was 36.25%, less than one percentage point below that
sample-derived threshold. This is a near-boundary result with substantial
sampling uncertainty, not a large and precisely measured negative edge.

## Failure attribution

### 1. The edge is tail-dependent

The strategy has the intended convex shape: losses are usually close to `-1R`,
while winners can be several R. The problem in this sample is that large winners
did not arrive frequently enough.

- 43 trades hit `STOP`; all were losses, averaging `-1.0268R`.
- 37 trades exited at `EOD`; 29 won and eight lost, averaging `+1.1403R`.
- The best eight trades contributed `+26.90R`.
- Removing those eight changes expectancy from `-0.0245R` to `-0.4008R`.
- Removing the worst eight changes expectancy to `+0.0903R`.

The asymmetry is important: the loss tail is bounded and repetitive, while the
profitability claim depends on rare large winners. Eighty observations provide
little precision for estimating the arrival rate and size of that right tail.

### 2. The short side is the clearest observed drag

| Side | Trades | Win rate | Expectancy | 95% trade-bootstrap interval |
| --- | ---: | ---: | ---: | ---: |
| Long | 42 | 45.2% | `+0.1788R` | `[-0.274R, +0.689R]` |
| Short | 38 | 26.3% | `-0.2492R` | `[-0.629R, +0.185R]` |

Short trades reduced the stitched result, whereas long trades were positive in
this sample. This is a plausible direction for a revised hypothesis, but not a
confirmed rule:

- the split was examined after seeing the TEST result;
- both confidence intervals are wide and overlap zero;
- there are only 38 short and 42 long observations;
- long/short behavior may be confounded with the market regimes present in
  these five months.

It would be invalid to simply delete shorts from the completed experiment and
declare success.

### 3. Performance varied materially through the short calendar window

| Month | Trades | Win rate | Expectancy |
| --- | ---: | ---: | ---: |
| 2023-08 | 3 | 33.3% | `+0.3169R` |
| 2023-09 | 20 | 35.0% | `-0.0594R` |
| 2023-10 | 22 | 45.5% | `+0.0462R` |
| 2023-11 | 21 | 23.8% | `-0.1403R` |
| 2023-12 | 14 | 42.9% | `+0.0148R` |

These are descriptive slices only. August has three trades, and even the largest
month has 22. November appears to be the primary weak period, but no regime map
was predeclared, so the current evidence cannot distinguish random clustering
from a durable market-state effect.

### 4. Fold differences are smaller than their uncertainty

| Fold | Expectancy | 95% trade-bootstrap interval | Bootstrap probability mean is positive |
| --- | ---: | ---: | ---: |
| 1, selected 15 minutes | `+0.0181R` | `[-0.372R, +0.448R]` | 52.0% |
| 2, selected 5 minutes | `-0.0671R` | `[-0.524R, +0.463R]` | 37.8% |

Neither 40-trade fold estimates its own expectancy with useful precision. The
selected duration also moved from 15 to 5 minutes, the selected validation
plateaus were singletons, and adjacent TEST settings showed a sign change and a
performance cliff. Together these observations support the policy's
**instability** conclusion, but they do not show which duration is truly best.

### 5. Costs turn a marginal result more negative

The stitched baseline is already close to zero. Predeclared adverse-cost
scenarios reduced expectancy to approximately:

- `-0.0370R` under four/two-tick stop/EOD exit slippage;
- `-0.0492R` under six/three-tick stop/EOD exit slippage.

The strategy is therefore not failing solely because of an extreme cost model,
but it has no observed margin of safety for execution deterioration.

### 6. Positive dollars do not override negative standardized expectancy

The 80 trades produced approximately `$3,931` of net P&L while averaging
`-0.0245R`. Winners carried higher average initial dollar risk than losers
(about `$541` versus `$372`), so dollar-weighting and equal-trade R-weighting
tell different stories.

The registered claim is evaluated in net R because R normalizes trades by their
declared risk. The positive dollar result is worth investigating as a
position-sizing interaction, but it is not a valid reason to replace the
pre-registered objective after observing the outcome.

## What the current result does and does not establish

**Established by deterministic evidence**

- The simulator ran coherently for all 14 linked runs.
- The completed methodology produced a slightly negative stitched TEST point
  estimate.
- The result fails the registered expectancy, fold-consistency, sufficiency,
  cost, neighborhood, plateau, and regime-evidence requirements.
- The current claim is not eligible for freeze or final-holdout use.

**Not established by the current sample**

- That the true ORB expectancy is definitely negative.
- That short ORB trades are structurally unprofitable.
- That 15 minutes is better than 5 or 30 minutes.
- That November represents a durable regime failure.
- That the next 80 trades would reproduce the observed point estimate.

Using the observed `1.48R` standard deviation and an IID normal approximation,
roughly 210 trades would be needed for a 95% mean interval with a `±0.20R`
half-width, and roughly 840 for `±0.10R`. These are planning approximations, not
new acceptance thresholds; serial dependence and regime imbalance could require
more evidence.

## Recommended next experiment

Do not rerun another unconstrained duration sweep or inspect the locked final
holdout. Preserve this study as rejected development evidence and register a
new, narrower hypothesis:

> During the available development period, QQQ long ORB breakouts have more
> stable net expectancy than a symmetric long/short ORB rule; short-side
> performance depends on predeclared market regimes rather than ORB duration
> alone.

Before execution:

1. Keep the 5/15/30-minute duration grid fixed so the study does not expand the
   search in response to these outcomes.
2. Compare the original symmetric rule with a predeclared long-only variant;
   treat this as a new candidate family and disclose the added tests.
3. Define market regimes before reading their outcomes, using causal
   session-available variables rather than post-session labels.
4. Extend development TEST coverage beyond the Policy v1 minimum of 100 trades
   and 120 sessions; target at least 200 trades if the data window permits.
5. Use more than two folds so fold consistency and parameter movement are not
   effectively determined by one transition.
6. Retain cost stress, adjacent-parameter diagnostics, immutable TEST evidence,
   and the same engine-integrity checks.

If that registered study remains unstable, the next conclusion should be that
the simple duration-only ORB hypothesis lacks enough structure—not that another
duration should be selected from the current TEST sample.
