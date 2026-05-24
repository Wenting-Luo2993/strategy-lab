# Architectural Decision Record (ADR)

This document is an index of all architectural decisions. Full details are in individual files under `adrs/`.

**Why ADRs Matter**: Capture the "why" behind decisions to prevent re-litigating settled choices and preserve institutional memory.

---

## Active ADRs

| ADR | Title | Status | Impact | Date |
|-----|-------|--------|--------|------|
| [001](adrs/adr-001-event-driven-backtester.md) | Event-Driven Backtester Architecture | ✅ Accepted | High | 2025-11-15 |
| [002](adrs/adr-002-timezone-aware-datetimes.md) | Timezone-Aware DateTime Operations | ✅ Accepted | Medium | 2025-12-10 |
| 003 | Polygon.io as Primary Data Provider | ✅ Accepted | High | 2025-12-20 |
| 004 | Discord Webhooks for Notifications | ✅ Accepted | Medium | 2026-01-10 |
| 005 | Phase Manager Pattern for Lifecycle | ✅ Accepted | Medium | 2026-02-15 |
| 006 | Regime Research Framework (6-Stage Pipeline) | ✅ Accepted | High | 2026-03-01 |
| [007](adrs/adr-007-remove-take-profit-gate.md) | Remove Take-Profit Gate (EOD-Only Exits) | ✅ Accepted | Critical | 2026-05-16 |
| 008 | Pause Paper Trading Pending 2026 H1 Validation | ✅ Accepted | Critical | 2026-05-23 |
| 009 | Python asyncio for Real-Time Data Pipeline | ✅ Accepted | Medium | 2025-11-01 |
| 010 | YAML Configuration Files (Environment-Based) | ✅ Accepted | Low | 2025-10-20 |
| [011](adrs/adr-011-enforce-memory-bank-maintenance.md) | Enforce Memory Bank Maintenance via Copilot | ✅ Accepted | High | 2026-05-23 |
| [012](adrs/adr-012-minimize-copilot-instructions.md) | Minimize Copilot Instructions, Detail in Memory Bank | ✅ Accepted | Medium | 2026-05-23 |
| [013](adrs/adr-013-keep-files-under-200-lines.md) | Keep Documentation Files Under ~200 Lines | ✅ Accepted | Medium | 2026-05-23 |

---

## Quick Reference

### By Impact Level

**Critical**: 007 (remove TP gate), 008 (pause paper trading)
**High**: 001 (backtester), 003 (Polygon), 006 (regime framework), 011 (memory bank)
**Medium**: 002 (timezone), 004 (Discord), 005 (phases), 009 (asyncio), 012 (copilot), 013 (file sizes)
**Low**: 010 (YAML config)

### By Category

**Infrastructure**: 001, 009, 010
**Data & Providers**: 002, 003
**Trading Strategy**: 006, 007, 008
**Operations**: 004, 005
**Documentation**: 011, 012, 013

---

## Decision Status Legend

- ✅ **Accepted** - Implemented and validated
- 🚧 **Proposed** - Under discussion
- ⏸️ **Deferred** - Tabled for later
- ❌ **Rejected** - Decided against
- 🔄 **Superseded** - Replaced by newer decision

---

## Adding New ADRs

1. Create file: `adrs/adr-NNN-short-title.md`
2. Use template from `memory-bank/README.md`
3. Add entry to this index
4. Update `active-context.md` (recent decisions)
5. Commit together with code changes

**Full ADR template and guidelines**: See [memory-bank/README.md](README.md)

