---
name: document-project-work
description: "Document work in Strategy Lab using the project/taskboard standard. Use whenever asked to document, write or update a plan, bug fix, incident, new project, maintenance procedure, research finding, handoff, design or runbook. Classify the work, associate it with a project, preserve evidence and regenerate the read-only taskboard."
---

# Document project work

This skill writes and organizes repository documentation. It does not authorize
implementation, production changes, publishing, new research runs or holdout access.

## Read first

Read `docs/taskboard/README.md` for the exact metadata/task schema and
`docs/taskboard/projects.json` for registered projects. Read the relevant project
roadmap and canonical source before adding a new document. Use the current
workspace, not another checkout.

## Classify and place

1. Identify the user's intent: **new project, plan/design, bug fix/incident,
   maintenance, research, reference/runbook or handoff**.
2. Associate it with an existing project; cross-project evidence may list
   several projects. Ask only if the project boundary is genuinely ambiguous.
   For an explicitly requested new project, add a stable project record with
   goal, stage and next action; do not classify every bug as a new project.
3. Prefer updating an existing source to creating a duplicate plan or status
   report. For a new source, use a descriptive filename and preserve the
   repository's dated-evidence vs undated-living-document conventions.
4. Add the top-of-file `<!-- taskboard` JSON metadata from the standard.
   Use the closest `kind`; an incident addition to an existing evidence guide
   does not need a separate bug document. Keep the document ID stable.

## Write the appropriate substance

- **Project:** goal, non-goals, success criteria, milestones, dependencies,
  known constraints and open decisions.
- **Plan/design:** confirmed scope, approach, alternatives/tradeoffs where
  material, acceptance, validation and risks. Distinguish recommendations
  from user-approved decisions; planning does not grant execution permission.
- **Bug/incident:** time and affected revision, observed vs expected behavior,
  impact, reproducible evidence, confirmed cause vs hypotheses, fix,
  validation, actual rollout state and unresolved follow-ups. Never label a
  local fix deployed, or an incident resolved, without corresponding evidence.
- **Maintenance:** trigger/cadence, prerequisites, supported procedure, safety
  boundaries, verification, rollback limitations and escalation. For Oracle
  operations follow `deploy-trading-bot`; documentation is not permission.
- **Research:** hypothesis/mechanism, data boundaries, method/cost assumptions,
  experiment IDs, estimates and uncertainty, conclusion, limitations and next
  decision. Update the living ORB results when a new ORB experiment exists.
  An inconclusive result is valid completed work, not a failed task.
- **Reference/handoff:** supported usage and limitations; state the observation
  date and point to current canonical sources. Do not revive old next steps.

Never include secrets or raw private-account exports. Do not repeat credentials
in examples. Use synthetic data where appropriate.

## Maintain work state

Add/update explicit `taskboard-task` fenced JSON blocks in the relevant current
roadmap (or a current tagged document if that is the better source).
Use stable IDs, concrete acceptance criteria, review date, evidence document
references and real prerequisites. Avoid duplicating one task across documents.
Keep implementation, deployment and operational observation separate where
they have different completion evidence.

Do not infer task completion from checkboxes, elapsed time, green liveness,
positive research estimates or another session's unaudited status. State
uncertainty and waiting reasons. Update project stage/next action only when
the evidence or user decision changes.

## Preserve history

Do not delete or move old documents merely because they are dated. Mark useful
past context `historical`. Only mark `superseded` when a verified replacement
exists, and provide its document ID in `superseded_by`. Historical documents
must not emit active task blocks; move the task record to the current roadmap
while preserving its ID and evidence. Preserve historical observation text.

## Validate and regenerate

From the repository root:

```powershell
python tools\taskboard\build.py --check
python tools\taskboard\build.py
```

When changing the generator/schema, also run:

```powershell
python -B -m unittest discover -s tools\taskboard\tests -v
```

Fix validation failures rather than bypassing them. The generated
`docs/taskboard/index.html` is ignored and must not be edited manually.
Check that the document appears under the right project and task links resolve.
Report the meaningful change briefly; do not claim a fresh operational check
merely because documentation or the HTML was regenerated.
