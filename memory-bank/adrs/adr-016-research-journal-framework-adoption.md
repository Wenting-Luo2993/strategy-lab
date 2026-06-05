# ADR-016: Research Journal Framework as Canonical Research Workflow

**Date**: 2026-06-04

**Status**: ✅ Accepted

## Context

Ad-hoc research execution led to weak reproducibility, fragmented notes, and inconsistent lineage tracking across hypotheses and optimization runs.

Research Journal framework implementation is now complete and functional, with registry, persistence, lineage, query, artifact tracking, and integration adapters.

## Decision

Adopt `vibe/research_journal/` as the canonical workflow for strategy research and experiment tracking:

- Register hypotheses and experiments through `ResearchRegistry`.
- Capture execution metadata (including git state) for reproducibility.
- Enforce experiment immutability after completion.
- Track lineage for optimization iterations.
- Use query API and artifact tracking for discovery and integrity.

## Alternatives Considered

- Continue ad-hoc markdown/script-only tracking.
  - Rejected: weak reproducibility and poor lineage.
- External experiment tracker only (without native integration).
  - Rejected: higher integration overhead and less alignment with repo workflow.

## Reasoning

- Provides structured, reproducible, and auditable research lifecycle.
- Integrates naturally with current optimization and backtest workflows.
- Reduces repeated mistakes by preserving decisions and outcomes in canonical records.

## Consequences

- ✅ Research process becomes consistent and reproducible.
- ✅ Experiment lineage and artifacts are traceable.
- ✅ Future agents/developers can resume research with less context loss.
- ⚠️ Requires discipline to register runs instead of one-off scripts.

## Related Files

- `vibe/research_journal/`
- `docs/backtester-mvp/research-journal-framework/IMPLEMENTATION_SUMMARY.md`
- `memory-bank/features/research-journal-guide.md`
