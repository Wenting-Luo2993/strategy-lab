# ORB direction comparison under Reliability Policy v2

**Date:** 2026-10-18  
**Experiment:** `01a151f2-d815-7f0f-86b0-cba0f586413e`  
**Researcher:** Bloom  
**Scope:** Registered nested walk-forward comparison of symmetric and long-only
QQQ ORB rules over the development period. The locked final holdout was
reserved and was not read.

## Executive conclusion

The expanded study found a positive stitched TEST point estimate, but it did
not establish a reliable strategy or a decisive long-only advantage.

| Decision evidence | Result | Interpretation |
| --- | ---: | --- |
| Stitched TEST sample | 502 trades over 630 declared TEST sessions | Much more informative than the prior 80-trade calibration study |
| Mean net expectancy | `+0.0749R` | Promising point estimate, not sufficient by itself |
| t-statistic versus `0R` | `1.086` | The sample mean is only 1.09 estimated standard errors above zero |
| Evidence confidence above `0R` | 86.1% | Below the predeclared 95% confidence standard |
| 95% Student-t interval | `[-0.0606R, +0.2104R]` | Crosses zero; statistical disposition is inconclusive |
| Seeded-bootstrap interval | `[-0.0492R, +0.2071R]` | Independently reaches the same inconclusive conclusion |
| Overall reliability verdict | **REJECTED** | Stability failures take precedence over the inconclusive profitability interval |

The stored verdict is rejected because only half the folds were positive,
validation performance degraded too much on frozen TEST windows, and adjacent
parameters exhibited sign changes and performance cliffs. The positive average
is therefore not stable enough to promote, even though the experiment is no
longer simply a small-sample negative result.

## Registered design and execution

| Item | Design |
| --- | --- |
| Symbol | QQQ |
| Candidate grid | ORB duration `5 / 15 / 30` minutes × direction `symmetric / long_only` |
| Folds | 10 |
| Per-fold windows | 252 TRAIN sessions, 63 VALIDATION sessions, 63 TEST sessions |
| Step | 63 sessions |
| Warmup | 50 sessions |
| Candidate evaluations | 60 across all folds |
| Recorded segment runs | 130 |
| Development evidence end | 2024-10-10 |
| Final holdout | 2025-01-02 through 2026-04-27, reserved and unread |

All ten folds completed. Every frozen TEST run had a passing engine-integrity
report, the research database passed its integrity check, causal data and
leakage checks passed, and all 130 expected linked runs were present.

## Statistical confidence profile

| Measure | Observed |
| --- | ---: |
| Trades | 502 |
| Mean expectancy | `+0.0749007R` |
| Sample standard deviation | `1.5448R` |
| Standard error | `0.06895R` |
| t-statistic | `1.0863` |
| Degrees of freedom | 501 |
| One-sided p-value above `0R` | 0.1389 |
| Evidence confidence above `0R` | 86.1% |
| 95% Student-t interval | `[-0.0606R, +0.2104R]` |
| Seeded-bootstrap interval | `[-0.0492R, +0.2071R]` |
| Bootstrap replicates | 200 |

The t-statistic answers how far the observed mean is from the `0R` threshold in
estimated standard-error units. A value of `1.086` is positive but not large:
the uncertainty remains wide enough that both a modest negative edge and a
meaningful positive edge are compatible with the development evidence.

The 86.1% evidence confidence is the Student-t cumulative probability at the
observed statistic. It is an evidence score under the sampling model, not an
86.1% Bayesian probability that the strategy is profitable.

## Fold and selection stability

| Fold | Frozen selection | TEST expectancy |
| ---: | --- | ---: |
| 1 | Symmetric, 15m | `+0.2160R` |
| 2 | Symmetric, 5m | `-0.0457R` |
| 3 | Long-only, 15m | `-0.0554R` |
| 4 | Symmetric, 5m | `-0.1378R` |
| 5 | Long-only, 5m | `+0.2032R` |
| 6 | Long-only, 30m | `+0.2011R` |
| 7 | Long-only, 15m | `+0.3451R` |
| 8 | Long-only, 15m | `-0.0143R` |
| 9 | Long-only, 30m | `+0.2356R` |
| 10 | Long-only, 15m | `-0.1265R` |

Long-only was selected on validation in seven of ten folds, but selection
frequency is not proof that it is superior. The selected procedure produced
positive TEST expectancy in only 5/10 folds, below the 60% requirement. Mean
validation-to-TEST degradation was `0.2972R`, above the allowed `0.25R`.
Parameter movement was acceptable at `1.2222`, but one selected validation
plateau remained isolated and requires human review.

### Descriptive paired direction comparison

The neighborhood evidence contains the opposite direction mode at the selected
duration for each fold. Comparing those ten pairs descriptively:

| Measure | Long-only versus symmetric |
| --- | ---: |
| Mean long-only TEST expectancy | `+0.0984R` |
| Mean symmetric TEST expectancy | `+0.0633R` |
| Mean paired difference | `+0.0351R` |
| Folds where long-only was higher | 6/10 |
| Paired t-statistic | `0.565` |
| Approximate 95% paired interval | `[-0.105R, +0.176R]` |

This is a small and imprecise advantage. It is useful descriptive evidence that
long-only deserves continued observation, but it does not confirm the revised
hypothesis. It also must not be used to discard the weaker fold outcomes or to
retroactively redefine the registered promotion rule.

## Robustness evidence

| Check | Result | Interpretation |
| --- | ---: | --- |
| Positive TEST folds | 50% | Failed the 60% minimum |
| Validation-to-TEST degradation | `0.2972R` | Failed the `0.25R` maximum |
| Parameter movement | `1.2222` | Passed the `1.5` maximum |
| Four/two-tick cost stress | `+0.0598R` | Passed |
| Six/three-tick cost stress | `+0.0451R` | Passed |
| Neighbor sign change | Present | Failed |
| Neighbor performance cliff | Present | Failed |
| Maximum regime contribution | 50.8% | Passed the 80% concentration limit |
| Unclassified TEST trades | 0 | Causal regime coverage completed |

Cost stress is materially better than in the earlier 80-trade study: the
stitched result remains positive under both predeclared adverse-cost scenarios.
The main issue is not transaction costs. It is temporal and local-parameter
instability.

## Causal regime observations

Regimes were predeclared reporting buckets, not candidate settings or entry
filters. Each TEST trade was classified using only information available before
entry: prior-session close relative to its 50-session moving average and
prior-session ATR percentage relative to the fold's frozen TRAIN median.

| Regime | Trades | Expectancy |
| --- | ---: | ---: |
| Trend-aligned / low volatility | 219 | `+0.1353R` |
| Trend-aligned / high volatility | 109 | `-0.0929R` |
| Counter-trend / low volatility | 60 | `-0.0040R` |
| Counter-trend / high volatility | 114 | `+0.1609R` |

The observations do not support a simple rule such as “trade only when aligned
with the prior trend.” Both trend-aligned/low-volatility and
counter-trend/high-volatility buckets were positive, while
trend-aligned/high-volatility was negative. Turning these diagnostic buckets
into filters without a newly registered experiment would create another
post-selection overfitting loop.

## Decision and next research step

**Current decision:** Do not promote or freeze the adaptive duration/direction
procedure. Preserve the result as completed development evidence and keep the
final holdout locked.

The experiment improved the evidence in three important ways:

1. It expanded the sample from 80 to 502 TEST trades.
2. It changed the point estimate from slightly negative to positive and remained
   positive under adverse cost assumptions.
3. It measured statistical uncertainty explicitly instead of treating the point
   estimate as certain.

It still failed to demonstrate stable transfer from validation to TEST or local
robustness around selected candidates. The next experiment should therefore
not add more duration or regime dimensions. A defensible follow-up is a simpler
registered comparison that fixes the ORB duration and direction before TEST,
using the current evidence only to choose a small number of confirmatory
variants. The acceptance rule should continue to require:

- a 95% expectancy interval wholly above `0R`;
- at least 60% positive folds;
- validation-to-TEST degradation no greater than `0.25R`;
- no material adjacent-parameter sign change or cliff;
- positive predeclared cost-stress expectancy.

If a fixed, simpler rule cannot meet those conditions in new development
windows, the correct conclusion is that this ORB hypothesis is not yet stable
enough—not that the candidate grid should be expanded again.
