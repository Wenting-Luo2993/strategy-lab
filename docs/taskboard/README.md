<!-- taskboard
{"id":"taskboard-standard","title":"Taskboard and documentation standard","projects":["taskboard"],"status":"current","kind":"reference","reviewed":"2026-11-14"}
-->
# Taskboard and documentation standard

## Open the board

From the repository root, with Python 3.10+ (standard library only):

```powershell
python tools\taskboard\build.py
Start-Process docs\taskboard\index.html
```

The generated HTML contains its CSS, JavaScript, task data and selected document
previews. It opens directly from disk without a server, credentials, package
installation, broker connection or Supabase access. Browser interactions only
filter and inspect. Explicit external links open a new tab; nothing is fetched
automatically. The generated file is local and ignored by Git.

## Where information belongs

| Source | Responsibility |
| --- | --- |
| `docs/taskboard/projects.json` | Stable project IDs, outcomes, stages and next actions |
| Project roadmap Markdown | Explicit tasks, acceptance criteria and dependency IDs |
| Existing plans, research results and incident documents | Detailed rationale and evidence; linked, not duplicated |
| `tools/taskboard/` | Generator, offline UI and tests |
| `.claude/skills/document-project-work/SKILL.md` | Agent workflow for classifying, writing and maintaining documents |

The five seed roadmaps organize existing evidence, not everything ever discussed
or every other session's current work. Backlog tasks are possibilities, not
authorization to execute them. The board never computes percentage-complete
for research or treats deployment success as incident closure.

## Opt a document into automatic discovery

Put this exact comment format at the **very beginning** of a Markdown file
under `docs/`. The content inside the comment is strict JSON (no trailing commas).
Normal Markdown viewers hide it.

```text
<!-- taskboard
{"id":"my-project-plan","title":"A descriptive title","projects":["orb-research"],"status":"current","kind":"plan","reviewed":"2026-11-14"}
-->
```

Use stable lowercase kebab-case IDs. `projects` is a nonempty list of IDs
already in the project registry; a document may support several projects.
New project IDs must be registered deliberately, not invented per document.

| Field | Values / meaning |
| --- | --- |
| `id`, `title` | Stable document identity and display title |
| `projects` | One or more registered projects |
| `kind` | `project`, `plan`, `bug_fix`, `maintenance`, `research`, `evidence`, `reference`, `roadmap` |
| `status` | `current`, `historical`, `superseded` |
| `reviewed` | Date this document's classification was actually reviewed, YYYY-MM-DD; not a claim its observations are fresh |
| `superseded_by` | Optional list of successor document IDs; required for `superseded` |

Only tagged documents are embedded. Untagged historical docs remain untouched.
Files must remain inside `docs/`; escaping symlinks are rejected. Never tag
credentials, account exports or secret-bearing material. A generated board
contains copies of its indexed documents: treat it with the same privacy as
the repository, and do not publish it automatically.

## Add tasks without guessing from prose

In a **current** tagged document, use a fenced JSON block with the language
`taskboard-task`, beginning at column one. This example uses `json` so it is not
ingested; change the fence language to `taskboard-task` in a real task document.

```json
{
  "id": "orb-example-review",
  "title": "Review a specific research question",
  "project": "orb-research",
  "status": "backlog",
  "milestone": "Exploration",
  "note": "Why this matters, current evidence and any waiting reason.",
  "acceptance": "The observable result that would complete this work.",
  "reviewed": "2026-11-14",
  "depends_on": [],
  "refs": ["orb-results"]
}
```

All fields are required. IDs are globally unique, and a task's project must
belong to its source document. `refs` contains one or more tagged document IDs;
`depends_on` names actual prerequisites, not merely related work.

| Task status | Meaning |
| --- | --- |
| `backlog` | Not selected or not yet actionable |
| `ready` | Actionable; all declared prerequisites are done |
| `in_progress` | Work is actually underway |
| `waiting` | Awaiting evidence, an external event, permission or a dependency; explain in `note` |
| `review` | A result, decision or proposed scope needs review |
| `done` | The acceptance criteria have supporting evidence; not a claim of positive research results |

Historical/superseded documents cannot emit tasks. Old checkboxes and headings
never become tasks automatically. Mark a fix implemented separately from
operational verification; keep an inconclusive experiment's conclusion even
when its execution task is done. A drag, click or document statement cannot
authorize production operations or unlock research holdout data.

## Classify before writing

| Intent | Suggested kind | Required narrative |
| --- | --- | --- |
| New project | `project` | Goal, non-goals, success criteria, milestones, dependencies and open decisions |
| Implementation plan | `plan` | Bounded scope, approach, acceptance, validation and risks |
| Bug fix / incident | `bug_fix` or existing `evidence` timeline | Observed time/revision, impact, evidence, cause vs hypothesis, fix, validation, rollout, unresolved follow-ups |
| Maintenance | `maintenance` | Trigger, cadence if applicable, safe procedure, verification, rollback limitations and escalation |
| Research | `research` | Hypothesis, data boundaries, method, experiment IDs, uncertainty, result, lesson and next decision |
| Reference or runbook | `reference` | Purpose, prerequisites, supported procedure and limitations |

Use the closest existing canonical document. Append incident observations in
time order; do not overwrite yesterday's evidence with today's result.
An existing large guide can remain `evidence` while gaining a new bug-fix
section; there is no need to split it into duplicate documents just for a label.

## Keep the board accurate

1. Read the relevant project roadmap and canonical documents before editing.
2. Update task state only from explicit evidence, not an optimistic narrative.
3. Update `reviewed` only when reviewing the record. Generation time is separate.
4. Update project stage/next action when priorities actually change.
5. Preserve historical files and links. Use `historical` for useful old context;
   use `superseded` only with a verified replacement, naming `superseded_by`.
6. Run validation and rebuild, then reload the HTML.

```powershell
python tools\taskboard\build.py --check
python -B -m unittest discover -s tools\taskboard\tests -v
python tools\taskboard\build.py
```

The builder rejects malformed metadata/tasks, duplicate IDs/JSON keys, unknown
projects/references, dependency/supersession cycles, and ready/done tasks with
unfinished prerequisites. A failed build leaves the previous HTML untouched.
Fix the error and rebuild; do not treat the old HTML as newly generated.

Filters and detail panels work offline. Document previews follow the existing
local viewer's safe-escaping approach, adding bounded tables and indexed links.
They are not full GitHub Markdown: raw HTML stays text, images are source
references, diagrams stay code, and links to unindexed local files are shown as
text. Tasks and documents have shareable in-file `#task/id` / `#doc/id` links.

The board warns when the oldest task review is at least seven days old. It
cannot detect changes made after generation; regenerating updates document
content **but does not re-review task states**. Refresh is explicit, and worktree
copies synchronize only through normal Git changes.
