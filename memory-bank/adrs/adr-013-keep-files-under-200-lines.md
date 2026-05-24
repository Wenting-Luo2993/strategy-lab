# ADR-013: Keep Documentation Files Under ~200 Lines

**Date**: 2026-05-23

**Status**: ✅ Accepted

## Context

Large documentation files (500+ lines) consume excessive tokens when loaded by AI assistants, reducing context budget for code. Long files also harder for humans to scan and navigate.

## Decision

Keep memory bank and documentation files under ~200 lines (soft limit). When files approach 200 lines, split into logical sub-files or use indexing approach.

## Alternatives Considered

- **No limit** - Rejected (token budget exhaustion, poor scannability)
- **Hard 200-line limit** - Rejected (too rigid, sometimes 220-250 lines is acceptable)
- **Split everything into many tiny files** - Rejected (fragmentation, navigation overhead)

## Reasoning

- AI assistants have token budgets (e.g., 200K tokens total)
- Loading 5 x 500-line files = 50-100K tokens (25-50% of budget)
- ~200 lines ≈ 5-10K tokens per file (manageable)
- Shorter files = faster scanning for humans
- Encourages focused, modular documentation

## Guidelines

**When to split a file**:
- File exceeds 200 lines → Consider splitting
- File has distinct sections → Extract to sub-files
- File mixes concepts → Separate concerns

**How to split**:
- **ADRs**: Use index file (`adr.md`) + individual files (`adrs/adr-NNN-title.md`)
- **Long guides**: Split into sections (e.g., `setup.md`, `deployment.md`, `troubleshooting.md`)
- **Large code files**: Extract modules/classes (standard refactoring)

**Acceptable to exceed 200 lines**:
- Progress log (cumulative history, occasionally pruned)
- Comprehensive reference docs (with clear TOC)
- Generated files (HTML reports, etc.)

## Consequences

- ✅ Reduced token consumption (more context budget for code)
- ✅ Faster file scanning (humans and AI)
- ✅ Modular, focused documentation
- ✅ Easier to maintain (edit smaller files)
- ⚠️ More files to navigate (mitigated by clear naming + index files)
- ⚠️ Requires occasional refactoring/splitting

## Related Files

- `memory-bank/adr.md` (converted to index, was 400+ lines)
- `memory-bank/adrs/` (individual ADR files, ~30-50 lines each)
- `.vscode/copilot-instructions.md` (reduced to 85 lines per ADR-012)
