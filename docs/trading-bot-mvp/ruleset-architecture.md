# Strategy Ruleset Architecture

**Status:** Design / Proposed
**Date:** 2026-03-17
**Scope:** `vibe/common/ruleset/`, `vibe/trading_bot/core/orchestrator.py`, `vibe/rulesets/`

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Goals](#goals)
- [Two-Layer Config Architecture](#two-layer-config-architecture)
- [File Structure](#file-structure)
- [Selecting the Active Ruleset in Production](#selecting-the-active-ruleset-in-production)
  - [Mechanism: `active_ruleset` in AppSettings](#mechanism-active_ruleset-in-appsettings)
  - [How It Is Set Per Environment](#how-it-is-set-per-environment)
  - [Startup Sequence](#startup-sequence)
  - [Switching Rulesets](#switching-rulesets)
- [Config Polymorphism Patterns](#config-polymorphism-patterns)
  - [Pattern 1 — Discriminated Union on `type`](#pattern-1--discriminated-union-on-type-strategy-specific-params)
  - [Pattern 2 — Optional Discriminated Union on `method`](#pattern-2--optional-discriminated-union-on-method-feature-with-multiple-modes)
  - [Pattern 3 — Rule Table](#pattern-3--rule-table-ordered-list-of-conditional-steps)
  - [Pattern Selection Guide](#pattern-selection-guide)
- [Complete YAML Ruleset Example](#complete-yaml-ruleset-example)
- [Pydantic Model Hierarchy](#pydantic-model-hierarchy)
- [Orchestrator Fan-Out Pattern](#orchestrator-fan-out-pattern)
- [Adding New Ruleset Components](#adding-new-ruleset-components)
- [Backtest Parameter Sweeping](#backtest-parameter-sweeping)
- [Migration Plan](#migration-plan)
- [What Does Not Change](#what-does-not-change)
- [FAQ](#faq)

---

## Problem Statement

Strategy parameters are currently scattered across multiple places:

| Concern | Current Location |
|---|---|
| Trading symbols | `AppSettings.trading.symbols` (env var) |
| ORB params (timeframe, cutoff) | `ORBStrategyConfig` (hardcoded defaults) |
| Position sizing | `PositionSizer` constructor args (hardcoded in orchestrator) |
| Stop-loss / take-profit | `ORBStrategyConfig` + `StrategyBase` |
| Trade filters | `ORBStrategyConfig.use_volume_filter` |
| MTF validation | `MTFValidator` (configured inline) |

**Consequences:**
- Changing strategy parameters requires editing Python code
- No single place to see "what is the bot running right now?"
- Backtest parameter sweeping requires manual loops over hardcoded values
- Switching between parameter variants (conservative vs aggressive) is error-prone

---

## Goals

1. **Single source of truth** — one YAML file fully describes a trading strategy instance
2. **Production visibility** — which ruleset is live is immediately readable from a file
3. **No code changes for parameter tuning** — swap YAML files, not Python
4. **Multi-component fan-out** — one ruleset governs strategy, risk, orchestrator, filters
5. **Sweep-ready** — backtest parameter sweeping works off the same model layer
6. **Multi-strategy ready** — adding a new strategy type requires no orchestrator changes

---

## Two-Layer Config Architecture

This design maintains a clean separation between two config concerns:

```
┌─────────────────────────────────────────────────────────┐
│  AppSettings  (infrastructure)                          │
│  Source: .env file / environment variables              │
│                                                         │
│  • API keys (Finnhub, Polygon)                          │
│  • Database path                                        │
│  • Cloud sync settings                                  │
│  • Log level, health check port                         │
│  • active_ruleset: "orb_production"  ← points to file  │
└─────────────────────────────────────────────────────────┘
                          │
                          │ resolves to
                          ▼
┌─────────────────────────────────────────────────────────┐
│  StrategyRuleSet  (trading rules)                       │
│  Source: vibe/rulesets/<name>.yaml                      │
│                                                         │
│  • Instruments (symbols, timeframe)                     │
│  • Strategy parameters (ORB window, filters, entry)     │
│  • Position sizing (method, max risk %)                 │
│  • Exit logic (EOD time, take-profit, stop-loss)        │
│  • Trade filters (VIX, volume)                          │
│  • MTF validation (timeframe, condition)                │
└─────────────────────────────────────────────────────────┘
```

**Rule:** `AppSettings` never contains trading-rule parameters. `StrategyRuleSet` never contains secrets or infrastructure config.

---

## File Structure

```
strategy-lab/
├── vibe/
│   ├── rulesets/                          # ← new: git-tracked trading rules
│   │   ├── orb_production.yaml            # what the live bot is running
│   │   ├── orb_conservative.yaml          # named variant (tighter filters)
│   │   ├── orb_aggressive.yaml            # named variant (wider targets)
│   │   └── sweeps/
│   │       └── orb_robustness.yaml        # backtest sweep parameter spec
│   ├── common/
│   │   ├── ruleset/                       # ← new: ruleset module
│   │   │   ├── __init__.py
│   │   │   ├── models.py                  # StrategyRuleSet + all section Pydantic models
│   │   │   └── loader.py                  # from_yaml(), from_name(), sweep expansion
│   │   └── strategies/                    # unchanged
│   └── trading_bot/
│       ├── config/
│       │   └── settings.py                # AppSettings — add active_ruleset field
│       └── core/
│           └── orchestrator.py            # accepts StrategyRuleSet, fans out to components
├── docs/
│   └── trading-bot-mvp/
│       └── ruleset-architecture.md        # this document
├── python/                                # other projects (unchanged)
└── pine/                                  # other projects (unchanged)
```

---

## Selecting the Active Ruleset in Production

### Mechanism: `active_ruleset` in AppSettings

`AppSettings` gains one new field that names the active ruleset:

```python
# vibe/trading_bot/config/settings.py
class AppSettings(BaseSettings):
    active_ruleset: str = Field(
        default="orb_production",
        description="Name of the active ruleset file under vibe/rulesets/ (without .yaml)"
    )
    # ... rest unchanged
```

This is resolved at startup to the corresponding file `vibe/rulesets/{active_ruleset}.yaml`.

### How It Is Set Per Environment

| Environment | How to set |
|---|---|
| Production (Docker) | `ACTIVE_RULESET=orb_production` in `.env` or Docker env |
| Development | `ACTIVE_RULESET=orb_conservative` in local `.env` |
| CI / backtest | `ACTIVE_RULESET=orb_aggressive` via env var override |
| Backtest sweep | `RuleSetLoader.from_sweep("sweeps/orb_robustness.yaml")` — bypasses this field |

### Startup Sequence

```python
# run.py (entry point)
settings = get_settings()                                      # reads .env
ruleset = RuleSetLoader.from_name(settings.active_ruleset)    # loads vibe/rulesets/<name>.yaml
orchestrator = TradingOrchestrator(config=settings, ruleset=ruleset)
asyncio.run(orchestrator.run())
```

This makes it immediately visible in logs at startup:

```
INFO  - Active ruleset: orb_production (v1.2) — QQQ, SPY, AMZN | ORB 5m | TP 2.0x | Risk 1%
```

### Switching Rulesets

To switch what the bot runs in production, change one line in `.env`:

```bash
# .env
ACTIVE_RULESET=orb_conservative   # was: orb_production
```

No code changes. No redeployment of new code — just restart with the new env value.

---

## Config Polymorphism Patterns

The ruleset uses three distinct patterns depending on the nature of each configurable concern.
Understanding which pattern to use is the key design decision when adding new configurable behaviors.

### Pattern 1 — Discriminated Union on `type` (strategy-specific params)

Used when **different strategies have entirely different parameter shapes**. The `type` field
selects which Pydantic model to validate against. Fields from other strategy types are rejected
at load time.

```yaml
# ORB — has orb_start_time, orb_duration_minutes, etc.
strategy:
  type: orb
  orb_start_time: "09:30"
  orb_duration_minutes: 5
  orb_body_pct_filter: 0.5
  entry_cutoff_time: "15:00"
  entry_mode: no_validation_simple
  one_trade_per_day: true

# Scalping — completely different shape
strategy:
  type: scalping
  min_spread_pct: 0.05
  max_holding_minutes: 10
  tick_momentum_bars: 3

# Candle pattern
strategy:
  type: candle_pattern
  patterns: [hammer, bullish_engulfing]
  confirmation_candles: 1
  min_pattern_body_pct: 0.6
```

```python
class ORBStrategyParams(BaseModel):
    type: Literal["orb"] = "orb"
    orb_start_time: str = "09:30"
    orb_duration_minutes: int = 5
    orb_body_pct_filter: float = 0.5
    entry_cutoff_time: str = "15:00"
    entry_mode: str = "no_validation_simple"
    one_trade_per_day: bool = True
    cancel_other_side: bool = True
    allow_reentry: bool = False

class ScalpingStrategyParams(BaseModel):
    type: Literal["scalping"] = "scalping"
    min_spread_pct: float = 0.05
    max_holding_minutes: int = 10
    tick_momentum_bars: int = 3

class CandlePatternStrategyParams(BaseModel):
    type: Literal["candle_pattern"] = "candle_pattern"
    patterns: list[str]
    confirmation_candles: int = 1
    min_pattern_body_pct: float = 0.6

# Adding a new strategy = add a new model here + one elif in _build_strategy()
StrategyParams = Annotated[
    Union[ORBStrategyParams, ScalpingStrategyParams, CandlePatternStrategyParams],
    Field(discriminator="type")
]
```

Pydantic gives precise field-level errors. Writing `orb_duration_minutes` under `type: scalping`
raises a validation error immediately at startup, before any trading begins.

---

### Pattern 2 — Optional Discriminated Union on `method` (feature with multiple modes)

Used when a feature can be **disabled entirely OR enabled in one of several distinct modes**,
each requiring different parameters. `null` (or omitting the key) means the feature is off.

Simple examples first:

```yaml
# Stop loss: three modes or off
stop_loss:
  method: orb_level            # uses ORB high/low directly, no extra params

stop_loss:
  method: atr_multiple
  multiplier: 1.5
  period: 14

stop_loss:
  method: fixed_pct
  value: 0.02                  # 2% from entry

stop_loss: ~                   # null — no stop loss (not recommended)
```

```python
class OrbLevelStopLoss(BaseModel):
    method: Literal["orb_level"] = "orb_level"
    # no extra params — level is derived from ORB calculation

class ATRMultipleStopLoss(BaseModel):
    method: Literal["atr_multiple"] = "atr_multiple"
    multiplier: float = 1.5
    period: int = 14

class FixedPctStopLoss(BaseModel):
    method: Literal["fixed_pct"] = "fixed_pct"
    value: float  # e.g. 0.02 = 2%

StopLossConfig = Annotated[
    Union[OrbLevelStopLoss, ATRMultipleStopLoss, FixedPctStopLoss],
    Field(discriminator="method")
]

class ExitConfig(BaseModel):
    stop_loss: StopLossConfig = Field(default_factory=OrbLevelStopLoss)
    trailing_stop: Optional[TrailingStopConfig] = None  # see Pattern 3
    take_profit: Optional[TakeProfitConfig] = None
    eod: bool = True
    eod_time: str = "15:55"

    @model_validator(mode="after")
    def at_least_one_exit_required(self) -> "ExitConfig":
        """Require at least one active exit condition to prevent positions from being held indefinitely."""
        has_exit = (
            self.eod
            or self.stop_loss is not None
            or self.take_profit is not None
            or self.trailing_stop is not None
        )
        if not has_exit:
            raise ValueError(
                "ExitConfig must define at least one exit condition "
                "(eod, stop_loss, take_profit, or trailing_stop). "
                "A position with no exit path will be held indefinitely."
            )
        return self
```

---

### Pattern 3 — Rule Table (ordered list of conditional steps)

Used when a behavior is governed by **a sequence of "if condition X, then do Y" rules**
that are evaluated in order. This handles cases where a simple scalar parameter is not
expressive enough.

#### Trailing Stop — all variants

```yaml
# Disabled — omit or null
trailing_stop: ~

# ATR-based: trail by N × ATR
trailing_stop:
  method: atr
  multiplier: 2.0
  period: 14

# Fixed fraction of initial risk distance
# e.g. initial stop was $1.00, trail stays $0.50 behind current price
trailing_stop:
  method: initial_risk_pct
  value: 0.5               # trail = 50% of original stop distance

# Fixed dollar amount behind current price
trailing_stop:
  method: fixed_dollar
  value: 50.0

# Stepped R-multiple — move stop to a locked-in level as price reaches profit targets
# Read: "once price reaches 2R profit, move stop to lock in 1R; once at 3R, lock in 2R..." etc.
trailing_stop:
  method: stepped_r_multiple
  steps:
    - at: 2.0              # when unrealized P&L reaches 2R
      move_stop_to: 1.0    # move stop to lock in 1R profit
    - at: 3.0
      move_stop_to: 2.0
    - at: 4.0
      move_stop_to: 2.5
    - at: 4.5
      move_stop_to: 3.5
```

```python
class ATRTrailingStop(BaseModel):
    method: Literal["atr"] = "atr"
    multiplier: float = 2.0
    period: int = 14

class InitialRiskPctTrailingStop(BaseModel):
    method: Literal["initial_risk_pct"] = "initial_risk_pct"
    value: float  # fraction of original stop distance to trail by

class FixedDollarTrailingStop(BaseModel):
    method: Literal["fixed_dollar"] = "fixed_dollar"
    value: float

class RStep(BaseModel):
    """One step in a stepped R-multiple trailing stop rule table."""
    at: float           # trigger: unrealized P&L reaches this R multiple
    move_stop_to: float # action: move stop to lock in this R multiple

    @model_validator(mode="after")
    def stop_must_be_below_trigger(self) -> "RStep":
        if self.move_stop_to >= self.at:
            raise ValueError(
                f"move_stop_to ({self.move_stop_to}R) must be less than at ({self.at}R) "
                f"— you cannot lock in more profit than the trigger level"
            )
        return self

class SteppedRMultipleTrailingStop(BaseModel):
    method: Literal["stepped_r_multiple"] = "stepped_r_multiple"
    steps: list[RStep]

    @model_validator(mode="after")
    def steps_must_be_ascending(self) -> "SteppedRMultipleTrailingStop":
        ats = [s.at for s in self.steps]
        if ats != sorted(ats):
            raise ValueError("steps must be listed in ascending order of 'at'")
        return self

TrailingStopConfig = Annotated[
    Union[
        ATRTrailingStop,
        InitialRiskPctTrailingStop,
        FixedDollarTrailingStop,
        SteppedRMultipleTrailingStop,
    ],
    Field(discriminator="method")
]
```

The runtime execution logic for `SteppedRMultipleTrailingStop` is straightforward: walk the
steps in order and find the highest `at` threshold that has been reached. The corresponding
`move_stop_to` defines the floor the stop cannot move below.

```python
def compute_stepped_stop(
    cfg: SteppedRMultipleTrailingStop,
    initial_risk: float,   # dollar distance of original stop
    entry_price: float,
    current_price: float,
    side: str,             # "long" | "short"
) -> Optional[float]:
    """Return the stop price floor given current unrealized P&L in R multiples."""
    pnl_r = (current_price - entry_price) / initial_risk if side == "long" \
            else (entry_price - current_price) / initial_risk

    active_stop_r = None
    for step in cfg.steps:         # steps are sorted ascending by `at`
        if pnl_r >= step.at:
            active_stop_r = step.move_stop_to
        else:
            break                  # no need to check further

    if active_stop_r is None:
        return None                # no step triggered yet

    if side == "long":
        return entry_price + active_stop_r * initial_risk
    else:
        return entry_price - active_stop_r * initial_risk
```

#### When to use the Rule Table pattern

The rule table is appropriate when:
- A single scalar cannot capture the intent (a stepped schedule vs a fixed offset)
- The number of rules is variable (user defines as many steps as they want)
- Each rule has a clear `condition → action` shape

Other future candidates for rule tables:
- **Partial exits**: `[{at: 2R, close_pct: 0.5}, {at: 3R, close_pct: 0.3}, {at: 4R, close_pct: 0.2}]`
- **Dynamic position sizing**: scale-in rules by bar pattern
- **Time-based stop tightening**: tighten stop as EOD approaches

---

### Pattern Selection Guide

| Situation | Pattern | YAML key | Pydantic type |
|---|---|---|---|
| Different strategies (ORB vs scalping) | Discriminated union | `type:` | `Union[...] + discriminator="type"` |
| Feature on/off with one fixed mode | Boolean | `enabled: true` | `bool` |
| Feature off or one of N modes | Optional discriminated union | `method:` | `Optional[Union[...] + discriminator="method"]` |
| Ordered conditional rules | Rule table | list of `{condition, action}` | `list[StepModel]` |
| Enum choice, no extra params | Literal | `mode: value` | `Literal["a","b","c"]` |

---

## Complete YAML Ruleset Example

```yaml
# vibe/rulesets/orb_production.yaml
name: orb_production
version: "1.2"
description: "ORB strategy — standard production parameters"

instruments:
  symbols: [QQQ, SPY, AMZN]
  timeframe: 5m

strategy:
  type: orb
  orb_start_time: "09:30"
  orb_duration_minutes: 5
  orb_body_pct_filter: 0.5
  entry_cutoff_time: "15:00"
  entry_mode: no_validation_simple
  one_trade_per_day: true
  cancel_other_side: true
  allow_reentry: false

position_size:
  method: max_loss_pct
  value: 0.01
  max_shares: 500

exit:
  eod: true
  eod_time: "15:55"

  take_profit:
    method: orb_range_multiple
    multiplier: 2.0

  stop_loss:
    method: orb_level

  trailing_stop:
    method: stepped_r_multiple
    steps:
      - at: 2.0
        move_stop_to: 1.0
      - at: 3.0
        move_stop_to: 2.0
      - at: 4.0
        move_stop_to: 2.5
      - at: 4.5
        move_stop_to: 3.5

trade_filter:
  vix_max: 30
  volume_confirmation: false
  volume_threshold: 1.5

mtf_validation:
  enabled: false
  timeframe: 30m
  condition: trend_aligned
```

---

## Pydantic Model Hierarchy

```
StrategyRuleSet
├── InstrumentConfig
│   ├── symbols: list[str]
│   └── timeframe: str
│
├── StrategyParams  — discriminated union on `type`
│   ├── ORBStrategyParams          (type: "orb")
│   ├── ScalpingStrategyParams     (type: "scalping")        ← future
│   └── CandlePatternStrategyParams (type: "candle_pattern") ← future
│
├── PositionSizeConfig
│   ├── method: Literal["max_loss_pct", "fixed_dollar", "fixed_shares"]
│   ├── value: float
│   └── max_shares: int | None
│
├── ExitConfig
│   ├── eod: bool
│   ├── eod_time: str
│   ├── take_profit: Optional[TakeProfitConfig]
│   │   └── discriminated on `method`
│   │       ├── OrbRangeMultipleTakeProfit  (method: "orb_range_multiple")
│   │       ├── ATRMultipleTakeProfit       (method: "atr_multiple")
│   │       └── FixedPctTakeProfit          (method: "fixed_pct")
│   ├── stop_loss: StopLossConfig
│   │   └── discriminated on `method`
│   │       ├── OrbLevelStopLoss            (method: "orb_level")
│   │       ├── ATRMultipleStopLoss         (method: "atr_multiple")
│   │       └── FixedPctStopLoss            (method: "fixed_pct")
│   └── trailing_stop: Optional[TrailingStopConfig]
│       └── discriminated on `method`
│           ├── ATRTrailingStop             (method: "atr")
│           ├── InitialRiskPctTrailingStop  (method: "initial_risk_pct")
│           ├── FixedDollarTrailingStop     (method: "fixed_dollar")
│           └── SteppedRMultipleTrailingStop (method: "stepped_r_multiple")
│               └── steps: list[RStep]
│                   ├── at: float
│                   └── move_stop_to: float
│
├── TradeFilterConfig
│   ├── vix_max: float | None
│   ├── volume_confirmation: bool
│   └── volume_threshold: float
│
└── MTFValidationConfig
    ├── enabled: bool
    ├── timeframe: str
    └── condition: str
```

All models use Pydantic v2 `BaseModel`. Cross-field validators (`@model_validator`) enforce
business rules (e.g. `move_stop_to < at`, steps in ascending order) at load time, not at runtime.

---

## Orchestrator Fan-Out Pattern

The orchestrator is the single point that loads the ruleset and distributes each section to the
appropriate component. **Components themselves do not import or know about rulesets** — they
receive their configuration via constructor injection, same as today.

```python
class TradingOrchestrator:
    def __init__(self, config: AppSettings, ruleset: StrategyRuleSet, ...):
        self.config = config       # infrastructure concerns
        self.ruleset = ruleset     # trading rule concerns

    def _initialize_components(self):
        self.symbols = self.ruleset.instruments.symbols
        self.strategy = self._build_strategy(self.ruleset.strategy)
        self.position_sizer = self._build_position_sizer(self.ruleset.position_size)
        self.stop_loss_mgr = self._build_stop_loss(self.ruleset.exit.stop_loss)
        self.trailing_stop_mgr = self._build_trailing_stop(self.ruleset.exit.trailing_stop)

    def _build_strategy(self, params: StrategyParams) -> StrategyBase:
        # isinstance checks are type-safe; mypy warns if a union variant is unhandled
        if isinstance(params, ORBStrategyParams):
            return ORBStrategy(ORBStrategyConfig(
                name="ORB",
                orb_start_time=params.orb_start_time,
                orb_duration_minutes=params.orb_duration_minutes,
                orb_body_pct_filter=params.orb_body_pct_filter,
                entry_cutoff_time=params.entry_cutoff_time,
            ))
        if isinstance(params, ScalpingStrategyParams):
            return ScalpingStrategy(...)
        raise ValueError(f"Unhandled strategy type: {params.type}")

    def _build_trailing_stop(
        self, cfg: Optional[TrailingStopConfig]
    ) -> Optional[TrailingStopHandler]:
        if cfg is None:
            return None
        if isinstance(cfg, ATRTrailingStop):
            return ATRTrailingStopHandler(multiplier=cfg.multiplier, period=cfg.period)
        if isinstance(cfg, InitialRiskPctTrailingStop):
            return InitialRiskPctHandler(pct=cfg.value)
        if isinstance(cfg, FixedDollarTrailingStop):
            return FixedDollarHandler(amount=cfg.value)
        if isinstance(cfg, SteppedRMultipleTrailingStop):
            return SteppedRHandler(steps=cfg.steps)  # passes the validated list of RStep
        raise ValueError(f"Unhandled trailing stop method: {cfg.method}")
```

The builder methods act as **adapters** between the ruleset vocabulary and the existing
component constructors. `ORBStrategyConfig`, `PositionSizer`, etc. do not change.

---

## Adding New Ruleset Components

The architecture is designed so that adding a new top-level section to `StrategyRuleSet`
(e.g. `alerts`, `execution`, `risk_limits`) requires touching exactly **four places** — no
existing components change.

| Step | What to do | Where |
|---|---|---|
| 1. Define the model | Create a Pydantic `BaseModel` (or discriminated union) for the new section | `vibe/common/ruleset/models.py` |
| 2. Add to `StrategyRuleSet` | Add an optional field with a sensible default | `vibe/common/ruleset/models.py` |
| 3. Add to the YAML | Add the corresponding block to `vibe/rulesets/*.yaml` | `vibe/rulesets/` |
| 4. Fan out in orchestrator | Add a `_build_<component>()` builder and wire the result to the relevant component | `vibe/trading_bot/core/orchestrator.py` |

### Example — adding an `alerts` section

**Step 1 & 2 — model + field:**

```python
# vibe/common/ruleset/models.py

class AlertsConfig(BaseModel):
    on_entry: bool = True
    on_exit: bool = True
    on_orb_established: bool = True
    on_daily_summary: bool = True

class StrategyRuleSet(BaseModel):
    # ... existing fields ...
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)  # ← new
```

**Step 3 — YAML:**

```yaml
# vibe/rulesets/orb_production.yaml
alerts:
  on_entry: true
  on_exit: true
  on_orb_established: true
  on_daily_summary: true
```

**Step 4 — orchestrator fan-out:**

```python
def _initialize_components(self):
    # ... existing builders ...
    self.alert_config = self.ruleset.alerts  # ← new: pass to notifier or store directly
```

### Constraints and guidelines

- **Optional with defaults** — all new top-level fields should have a default so existing YAML
  files continue to load without changes.
- **No secrets in rulesets** — API keys, webhook URLs, and credentials belong in `AppSettings`
  (environment variables), not in the YAML ruleset.
- **Validation at load time** — use `@model_validator` on the new section's model to enforce
  business rules (e.g. `at_least_one_exit_required` on `ExitConfig`). Never defer these checks
  to runtime.
- **Choose the right pattern** — refer to the [Pattern Selection Guide](#pattern-selection-guide)
  to decide whether the new section should use a discriminated union, optional union, rule table,
  or a plain boolean/enum.

---

## Backtest Parameter Sweeping

### Sweep Spec Format

```yaml
# vibe/rulesets/sweeps/orb_robustness.yaml
base: orb_production                    # inherit all non-swept values from this ruleset

sweep:
  strategy.orb_duration_minutes: [5, 10, 15]
  exit.take_profit.multiplier: [1.5, 2.0, 2.5, 3.0]
  position_size.value: [0.005, 0.01, 0.02]
```

Dotted paths work for nested fields. For rule table variants (e.g. sweeping trailing stop steps),
define multiple named rulesets and sweep over ruleset names instead — step configurations are
too structured to sweep cleanly as scalar lists.

### Sweep Expansion

```python
# vibe/common/ruleset/loader.py

def expand_sweep(spec_path: str) -> list[StrategyRuleSet]:
    """Expand a sweep spec into a list of ruleset variants."""
    import itertools, yaml

    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    base = RuleSetLoader.from_name(spec["base"])
    axes = spec["sweep"]  # {dotted.path: [values]}

    variants = []
    for combo in itertools.product(*axes.values()):
        params = dict(zip(axes.keys(), combo))
        variant = _deep_patch(base, params)   # applies dotted-path updates via model_copy
        variants.append(variant)

    return variants  # e.g., 3×4×3 = 36 variants
```

The backtest runner receives a list of `StrategyRuleSet` objects — no special sweep-aware
code is needed in the backtest engine itself.

---

## Migration Plan

This is a non-breaking, incremental migration. Existing components (`ORBStrategyConfig`,
`PositionSizer`, etc.) do not change.

### Phase 1 — Create the ruleset module (no behavior change)

1. Create `vibe/common/ruleset/models.py` with all Pydantic section models
2. Create `vibe/common/ruleset/loader.py` with `from_yaml()`, `from_name()`
3. Create `vibe/rulesets/orb_production.yaml` matching current hardcoded values
4. Write unit tests: load YAML → validate model → assert field values

### Phase 2 — Wire into orchestrator

1. Add `active_ruleset: str` to `AppSettings`
2. Update orchestrator constructor to accept `StrategyRuleSet`
3. Add `_build_strategy()`, `_build_position_sizer()`, `_build_trailing_stop()` builder methods
4. Update `run.py` entry point to load ruleset at startup
5. Log active ruleset name + version at startup

### Phase 3 — Migrate scattered params out of AppSettings

1. Remove `StrategySettings` fields that are now in the ruleset (`orb_*`, `entry_cutoff_time`, etc.)
2. Remove `TradingSettings.symbols` (now in ruleset `instruments.symbols`)
3. Keep `TradingSettings` for remaining infrastructure concerns (capital, market_type)

### Phase 4 — Backtest sweep support

1. Implement `expand_sweep()` in `loader.py`
2. Create first sweep spec `vibe/rulesets/sweeps/orb_robustness.yaml`
3. Wire into backtest runner

---

## What Does Not Change

- `ORBStrategyConfig` and `ORBStrategy` — unchanged; builder acts as adapter
- `PositionSizer` — unchanged; builder adapts method/value to constructor args
- `AppSettings` — gains one field (`active_ruleset`), loses `StrategySettings` in Phase 3
- All existing tests — components still accept same constructor args

---

## FAQ

**Q: Why not put the active ruleset path in `.env` directly?**
A: A name (`orb_production`) is more readable in logs and config than a full path. The loader resolves the name to `vibe/rulesets/{name}.yaml`, keeping the path convention consistent.

**Q: Can a ruleset reference another ruleset (inheritance)?**
A: Not in Phase 1. The sweep spec uses a `base:` pointer, but individual rulesets are standalone. If variant management becomes complex, YAML anchors (`&anchor` / `*alias`) provide lightweight inheritance within a single file.

**Q: What prevents someone from running the wrong ruleset in production?**
A: The ruleset name is visible in `.env` (version-controlled for prod environments), logged at startup, and included in Discord notifications. A code review of `.env` changes is the gate.

**Q: Where do per-symbol overrides go (e.g., tighter stop for AMZN)?**
A: Future extension — `instruments` section can grow a `per_symbol_overrides` map. Not needed in Phase 1.

**Q: Can I sweep over trailing stop step configurations?**
A: Not via the scalar sweep spec — step tables are too structured to sweep as scalar lists.
Instead, define multiple named rulesets (e.g. `orb_stepped_aggressive.yaml`, `orb_stepped_conservative.yaml`) and sweep over ruleset names, or write a Python sweep script that constructs `SteppedRMultipleTrailingStop` objects programmatically.

**Q: What if a new trailing stop mode has completely different parameters from all existing modes?**
A: Add a new model (e.g. `VWAPTrailingStop`) to the `TrailingStopConfig` union and add one
`isinstance` branch in `_build_trailing_stop()`. No other code changes required.
