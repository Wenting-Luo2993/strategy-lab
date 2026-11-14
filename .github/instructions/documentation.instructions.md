---
applyTo: "docs/**/*.md,docs/taskboard/projects.json,tools/taskboard/**"
---

# Project documentation and taskboard

For requested documentation (plans, bug fixes, incidents, new projects,
maintenance, research, runbooks or handoffs), follow
`.claude/skills/document-project-work/SKILL.md` and `docs/taskboard/README.md`.
Classify the intent, identify the project and prefer updating a canonical
source. Opt documents into ingestion with the specified metadata; use explicit
task blocks rather than inferring state from prose.

Preserve historical evidence and existing paths. Mark supersession only with a
verified successor. Separate implementation from deployment/observed operation,
and task completion from a research conclusion. Do not expose credentials or
infer production authorization from tasks. Validate and regenerate the local
HTML; never edit the generated file or change review dates just because a build ran.
