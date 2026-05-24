# GitHub Copilot Instructions for Strategy Lab

## 📚 CRITICAL: Read Memory Bank First

**Read selectively based on task type** to minimize token usage:

**For architectural/design decisions** → Read:
1. `memory-bank/adr.md` - Check for existing decisions (NEVER re-suggest rejected approaches)
2. `memory-bank/system-patterns.md` - Understand current architecture

**For implementation work** → Read:
1. `memory-bank/active-context.md` - Current focus, recent decisions, blockers
2. `CLAUDE.md` - Code patterns (Discord, phases, timezone, providers) - only relevant sections

**For simple fixes/syntax/general questions** → No memory bank needed

**Why selective reading**: Prevents token waste while ensuring critical context for significant decisions.

**Full guidelines**: See `memory-bank/README.md` for complete memory bank usage.

---

## Python Code Guidelines

### CRITICAL Safety Rules

**NEVER** delete cache files (`*_rolling_cache.parquet`, `*_indicators.pkl`):
- Contains historical data beyond lookback window
- Deleting = **permanent data loss** (can't re-fetch old data from Yahoo Finance)
- Safe alternatives: Use different symbol, copy before testing, use test cache directory

**ALWAYS** use `resolve_workspace_path()` for file paths:
```python
from src.utils.workspace import resolve_workspace_path
path = resolve_workspace_path("data_cache/file.csv")  # ✅ DO
path = Path("data_cache") / "file.csv"                # ❌ DON'T
```

**ALWAYS** use environment variables for cloud config:
```python
bucket = os.getenv("STORAGE_BUCKET_NAME")  # ✅ Cloud-agnostic
bucket = "oracle-bucket-123"                # ❌ Provider-specific
```

**Full guidelines**: See `memory-bank/tech-context.md` for complete dev environment details.

---

## 📝 Memory Bank Maintenance (REQUIRED)

**After EVERY significant technical decision** → Create new ADR:
1. Create `memory-bank/adrs/adr-NNN-short-title.md` (use template from README)
2. Add entry to `memory-bank/adr.md` index
3. Examples: Choosing libraries, changing architecture, adopting frameworks

**After EVERY session** → Update `memory-bank/active-context.md`:
- What you worked on
- Decisions made (link to ADR if technical)
- Current blockers
- Next steps

**Weekly or per milestone** → Update `memory-bank/progress-log.md`:
- Move tasks: Not Started → In Progress → Done
- Add milestones

**When architecture changes** → Update `memory-bank/system-patterns.md`:
- New components, data flow changes, design patterns

---

### End-of-Session Checklist

- [ ] Significant technical decision? → Create ADR file + update index
- [ ] Session notes current? → Update `active-context.md`
- [ ] Tasks completed/started? → Update `progress-log.md`
- [ ] Architecture changed? → Update `system-patterns.md`
- [ ] Memory bank committed with code? → `git commit`

**ADR Template & Full Guidelines**: See `memory-bank/README.md`

**Why this matters**: Prevents lost context, repeated mistakes, undocumented decisions. Enables fast onboarding and better AI assistance.

---

## General Principles

- Follow existing patterns in `CLAUDE.md` and `memory-bank/system-patterns.md`
- Write docstrings and type hints
- Keep functions focused and single-purpose
- Add tests for new functionality
- Commit memory bank updates with code changes
