# Memory Bank

This directory contains the living documentation for the trading bot project. It serves as a comprehensive knowledge base for both human developers and AI assistants.

## Purpose

The memory bank helps:
- **Onboard new developers** quickly with context about the project
- **Guide AI assistants** to make better suggestions aligned with project goals
- **Preserve decisions** and prevent re-litigating settled questions
- **Track progress** across features, bugs, and research tasks
- **Maintain continuity** between coding sessions

## File Structure

| File | Purpose | Update Frequency |
|------|---------|------------------|
| [project-brief.md](project-brief.md) | What the project is, who uses it, core goals | Rarely (stable foundation) |
| [product-context.md](product-context.md) | Why it exists, user workflow, success criteria | Occasionally (evolving vision) |
| [system-patterns.md](system-patterns.md) | Architecture, design patterns, data flow | When architecture changes |
| [tech-context.md](tech-context.md) | Dev environment, dependencies, commands | When tooling changes |
| [active-context.md](active-context.md) | Current focus, recent decisions, blockers | **Every session** (most dynamic) |
| [progress-log.md](progress-log.md) | Done/in-progress/not-started tasks | Weekly or per milestone |
| [adr.md](adr.md) | Architectural Decision Record (significant choices) | When major decisions are made |

## How to Use

### For Human Developers

1. **Starting a new session**: Read [active-context.md](active-context.md) to see what's in progress
2. **Making a technical decision**: Document it in [adr.md](adr.md) with reasoning and alternatives
3. **Completing a task**: Update [progress-log.md](progress-log.md) to move it from "in progress" to "done"
4. **Ending a session**: Update [active-context.md](active-context.md) with blockers and next steps

### For AI Assistants

When helping with this project:
1. **Read [project-brief.md](project-brief.md)** to understand goals and constraints
2. **Read [system-patterns.md](system-patterns.md)** to align suggestions with architecture
3. **Check [active-context.md](active-context.md)** to see current focus and recent decisions
4. **Consult [adr.md](adr.md)** before suggesting alternatives to settled decisions

## Maintenance Guidelines

### Update Frequencies

- **After EVERY significant technical decision**: [adr.md](adr.md)
- **After EVERY session**: [active-context.md](active-context.md)
- **Weekly or per milestone**: [progress-log.md](progress-log.md)
- **When architecture changes**: [system-patterns.md](system-patterns.md), [adr.md](adr.md)
- **When tooling changes**: [tech-context.md](tech-context.md)
- **Rarely**: [project-brief.md](project-brief.md), [product-context.md](product-context.md)

### Detailed Update Guidelines

#### ADR (Architectural Decision Record) - After EVERY Significant Decision

**When to add an ADR entry**:
- Choosing between technical alternatives (e.g., YAML vs JSON, library X vs Y)
- Adopting new frameworks or libraries
- Changing system architecture (adding components, breaking changes)
- Making pattern changes that affect multiple files
- Selecting third-party services or APIs

**ADR Entry Must Include**:
- **Date**: When decision was made
- **Status**: ✅ Accepted / 🚧 Proposed / ⏸️ Deferred / ❌ Rejected / 🔄 Superseded
- **Context**: Why the decision was needed
- **Decision**: What was chosen (clear, specific)
- **Alternatives Considered**: What other options were evaluated (with brief pros/cons)
- **Reasoning**: Why this option was chosen (include trade-offs)
- **Consequences**: ✅ Benefits and ⚠️ Drawbacks
- **Related Code/Files**: Where decision is implemented

**Example ADR Entry**:
```markdown
## ADR-012: Use YAML for Experiment Metadata

**Date**: 2026-05-23
**Status**: ✅ Accepted

**Context**: Research Journal needs human-readable experiment metadata that can be edited manually and tracked in Git.

**Decision**: Use YAML for experiment/hypothesis files, JSON for machine-generated data.

**Alternatives Considered**:
- **JSON only** - Fast, machine-readable, but harder for humans to edit and no comments
- **TOML** - Human-friendly but less common in Python ecosystem
- **SQLite database** - Overkill for small-scale metadata, harder to version in Git

**Reasoning**:
- YAML supports comments (document rationale inline)
- Git diffs are readable (important for audit trail)
- Python ecosystem has mature YAML support (pyyaml)
- Human-editable for research notes

**Consequences**:
- ✅ Human-friendly research notes
- ✅ Clean git diffs for metadata changes
- ✅ Comments preserved in files
- ⚠️ YAML parsing slightly slower than JSON (acceptable for metadata)
- ⚠️ Must validate YAML safety (use safe_load, no arbitrary code execution)

**Related Files**:
- `vibe/research/models.py` (YAML serialization)
- `research/experiments/*.yaml` (experiment metadata)
```

---

#### Active Context - After EVERY Session

**Required updates at end of each session**:
- **Current Focus Area**: What you're working on NOW (be specific)
- **Recent Decisions**: Choices made this session (link to ADR if technical)
- **Known Blockers**: What's preventing progress (with status: investigating/on hold/blocked)
- **Open Questions**: Unanswered questions with hypotheses
- **What's Next**: Updated immediate/short-term/medium-term/long-term tasks
- **Session Notes**: What was accomplished, key insights, important discoveries

**DO NOT** let active-context become stale - it's the bridge between sessions.

---

#### Progress Log - Weekly or Per Milestone

**Update when**:
- Completing a major feature or task
- Starting new work (move from "Not Started" to "In Progress")
- Reaching a milestone (version release, framework completion, validation passed)

**Required updates**:
- Move completed tasks: "🚧 In Progress" → "✅ Done"
- Move started tasks: "📋 Not Started" → "🚧 In Progress"
- Add new milestones with date and descriptive emoji
- Update version history if releasing versioned component

---

#### System Patterns - When Architecture Changes

**Update when**:
- Adding new core components (e.g., Research Journal framework)
- Changing data flow or integration points
- Introducing new design patterns (e.g., observer pattern, factory pattern)
- Modifying file organization structure
- Updating technology stack (new libraries, frameworks)

**Keep current**: Diagrams, component descriptions, integration points
**Don't let drift**: Actual implementation should always match documented patterns

---

### Memory Bank Update Workflow

**Standard workflow when making changes**:

1. **Make code changes** (implement feature/fix bug)
2. **If significant technical decision** → Update [adr.md](adr.md) with new ADR entry
3. **Always** → Update [active-context.md](active-context.md) session notes
4. **If task completed/started** → Update [progress-log.md](progress-log.md)
5. **If architecture changed** → Update [system-patterns.md](system-patterns.md)
6. **Commit together**: `git commit -m "Feature X + memory bank updates"`

---

### Anti-Patterns ❌

**Don't do these**:
- ❌ Make architectural decisions without documenting in ADR
- ❌ End session without updating active-context.md
- ❌ Let progress-log show old "in progress" tasks that are actually done
- ❌ Skip ADR updates because "it's just a small change" (small changes compound)
- ❌ Copy-paste large code blocks into memory bank (link to files instead)
- ❌ Let memory bank and actual code diverge

**If unsure whether to update ADR**: When in doubt, document it. Over-documentation is better than lost context.

### Keep It Concise

- ✅ Use bullet points and tables
- ✅ Link to detailed docs (don't duplicate)
- ✅ Remove outdated information
- ✅ **Keep files under ~200 lines** (see ADR-013 for rationale)
- ❌ Don't write essays or lengthy prose
- ❌ Don't copy-paste large code blocks (link to files instead)

**File Size Guideline**: Keep memory bank files under ~200 lines to optimize token usage for AI assistants and improve scannability. When files exceed this:
- **ADRs**: Use index (`adr.md`) + individual files (`adrs/adr-NNN-title.md`)
- **Long guides**: Split into focused sub-files
- **Cumulative logs**: Prune or archive old entries periodically

See [adrs/adr-013-keep-files-under-200-lines.md](adrs/adr-013-keep-files-under-200-lines.md) for full rationale.

### Version Control

All memory bank files are tracked in Git:
- Commit updates at the end of each session
- Use descriptive commit messages (e.g., "Update active context: 2026 YTD analysis")
- Review diffs before committing to catch accidental deletions

## Integration with Other Docs

This memory bank **complements** (not replaces) existing documentation:

| Memory Bank | Other Docs | Relationship |
|-------------|------------|--------------|
| [system-patterns.md](system-patterns.md) | `CLAUDE.md` | Memory bank = reference, CLAUDE.md = coding patterns |
| [progress-log.md](progress-log.md) | `docs/backtester-mvp/` | Memory bank = high-level, docs/ = detailed design |
| [tech-context.md](tech-context.md) | `README.md` | Memory bank = dev environment, README = project overview |
| [active-context.md](active-context.md) | User memory (`regime-research-framework.md`) | Memory bank = current session, user memory = accumulated insights |

## Example Workflow

### Scenario: Starting work on a new feature

1. **Read [active-context.md](active-context.md)**:
   - Current focus: 2026 YTD performance investigation
   - Blockers: Performance degradation, H3 filter failure
   - Next steps: Analyze 2026 YTD trades

2. **Read [system-patterns.md](system-patterns.md)**:
   - Regime research framework is in `vibe/backtester/analysis/regime_research/`
   - Use `scripts/analyze_regimes.py` CLI
   - Follow 6-stage pipeline pattern

3. **Start work**:
   - Implement analysis in `scripts/analyze_regimes.py`
   - Document findings in `docs/backtester-mvp/research-regime-filter/2026-ytd-analysis.md`

4. **Update memory bank**:
   - Move task from "in progress" to "done" in [progress-log.md](progress-log.md)
   - Update [active-context.md](active-context.md) with findings and new blockers
   - If decision made (e.g., "skip 2026 regime filter"), add to [adr.md](adr.md)

## Benefits

✅ **Faster onboarding**: New developers get full context in 15 minutes  
✅ **Better AI assistance**: Assistants make suggestions aligned with project goals  
✅ **Preserved knowledge**: Decisions and reasoning captured for future reference  
✅ **Reduced context switching**: Active context helps resume work quickly  
✅ **Prevented re-work**: ADR prevents re-litigating settled decisions  

## Inspiration

This memory bank structure is inspired by:
- [Architecture Decision Records (ADRs)](https://adr.github.io/)
- [C4 Model for software architecture](https://c4model.com/)
- AI agent memory systems (project brief → active context)

---

**Last Updated**: 2026-05-23  
**Maintained By**: Project team + AI assistants
