# ADR-012: Minimize Copilot Instructions, Detail in Memory Bank

**Date**: 2026-05-23

**Status**: ✅ Accepted

## Context

Copilot instructions file was growing to 250+ lines with detailed guidelines, making it hard to scan for critical rules. Long instructions reduce effectiveness.

## Decision

Keep copilot-instructions.md under 200 lines (ideally <100) with only critical, frequently-needed rules. Move detailed/situational content to appropriate memory bank files.

## Alternatives Considered

- **Keep everything in copilot-instructions.md** - Rejected (too long, hard to scan)
- **Split into multiple .md files in .vscode/** - Rejected (Copilot only reads copilot-instructions.md)
- **No guidelines, rely on README only** - Rejected (not visible to AI assistant)

## Reasoning

- Short, focused instructions are more likely to be read and followed
- Detailed guidelines belong in memory bank (versioned, searchable)
- Core rules: Read memory bank first, safety rules, update workflows
- Details: ADR templates, cloud deployment, full workflows → memory bank

## Consequences

- ✅ Copilot instructions concise and scannable (85 lines)
- ✅ Critical safety rules prominent (cache deletion, path resolution)
- ✅ Detailed guidelines in appropriate locations (memory-bank/README.md, tech-context.md)
- ✅ Single source of truth for each topic
- ⚠️ Developers must know to check memory bank for details (mitigated by "see memory-bank/" pointers)

## Changes Made

- Reduced `.vscode/copilot-instructions.md` from 250+ to 85 lines
- Moved ADR template to `memory-bank/README.md`
- Moved cloud deployment guidelines to `memory-bank/tech-context.md`
- Kept: Memory bank references, safety rules, update workflows, end-of-session checklist

## Related Files

- `.vscode/copilot-instructions.md` (streamlined)
- `memory-bank/README.md` (detailed ADR template and workflows)
- `memory-bank/tech-context.md` (cloud deployment guidelines)
