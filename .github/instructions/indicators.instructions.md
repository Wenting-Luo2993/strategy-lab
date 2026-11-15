---
applyTo: "vibe/common/indicators/**,vibe/common/strategies/**,vibe/research_pipeline/session_features.py"
---

# Shared indicator architecture

Follow `vibe/common/indicators/README.md` before implementing indicators or
strategy features. Keep reusable calculations in the shared indicator layer,
with no strategy/execution/provider imports. Thresholds and trade decisions
belong in validated strategy/ruleset policy, not indicators.

Document units, decision-time availability, warm-up/seeding, missing-data and
session/timezone semantics. Parameterize relevant periods/sessions. Require
prefix-invariance tests and hand-calculated fixtures; preserve existing
behavior under default parameters. Reject unsupported paths explicitly.
Batch-precomputed causal columns are not proof of streaming/live readiness.
