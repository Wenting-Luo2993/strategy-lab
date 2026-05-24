# ADR-011: Enforce Memory Bank Maintenance via Copilot Instructions

**Date**: 2026-05-23

**Status**: ✅ Accepted

## Context

Memory bank (living documentation) is critical for preserving project context, but easy to neglect. Need systematic enforcement to keep it current.

## Decision

Add comprehensive memory bank maintenance guidelines to `.vscode/copilot-instructions.md` requiring updates after every significant decision and session.

## Alternatives Considered

- **Manual discipline only** - Rejected (easy to forget, inconsistent)
- **Pre-commit hooks** - Rejected (too rigid, blocks commits unnecessarily)
- **Separate documentation tool** (Confluence, Notion) - Rejected (creates separation from code)
- **Rely on README updates only** - Rejected (insufficient detail, no ADR tracking)

## Reasoning

- Copilot instructions are read by AI assistant before every suggestion
- Memory bank in Git keeps documentation versioned with code
- ADR captures "why" decisions were made (prevents re-litigation)
- Active context bridges sessions (fast context recovery)
- Progress log tracks what's done/in-progress/planned

## Consequences

- ✅ AI assistants get full context before making suggestions
- ✅ Decisions preserved with reasoning (institutional memory)
- ✅ Fast onboarding for new developers (read memory bank)
- ✅ Prevents repeating rejected approaches
- ⚠️ Requires discipline to update after each session
- ⚠️ Memory bank can drift if guidelines ignored

## Required Updates

- **After EVERY significant technical decision** → Update `adr.md`
- **After EVERY session** → Update `active-context.md`
- **Weekly or per milestone** → Update `progress-log.md`
- **When architecture changes** → Update `system-patterns.md`

## Related Files

- `.vscode/copilot-instructions.md` (enforcement rules)
- `memory-bank/README.md` (usage guide)
- `memory-bank/adrs/` (individual ADR files)
