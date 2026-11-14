<!-- taskboard
{"id":"taskboard-roadmap","title":"Taskboard delivery roadmap","projects":["taskboard"],"status":"current","kind":"roadmap","reviewed":"2026-11-14"}
-->
# Taskboard delivery roadmap

The initial scope is a read-only, self-contained HTML snapshot with curated
tasks and automatically discovered, opted-in documents. No API, deployment,
trading, database or write controls are part of the board.

```taskboard-task
{"id":"taskboard-delivery","title":"Build and verify the offline taskboard","project":"taskboard","status":"done","milestone":"Usable local board","note":"Implemented offline overview, filters, task/dependency details and embedded documents. Verified direct-file browsing on desktop and a 390px mobile viewport, including offline mode and zero resource requests.","acceptance":"Open the generated HTML without a server, filter projects/tasks, follow dependencies and read embedded documents with no network requests or broken references.","reviewed":"2026-11-14","depends_on":[],"refs":["taskboard-standard"]}
```

```taskboard-task
{"id":"taskboard-authoring","title":"Standardize documentation and add the authoring skill","project":"taskboard","status":"done","milestone":"Maintainable ingestion","note":"Added document-project-work skill, standard/templates and repository instructions. Classified existing references and two historical handoffs without moving their paths. Eleven generator regressions cover ingestion, validation, escaping and snapshot preservation.","acceptance":"Provide a discoverable skill, templates, stable IDs, validation and documented rules for evidence, historical status and regeneration.","reviewed":"2026-11-14","depends_on":[],"refs":["taskboard-standard"]}
```

```taskboard-task
{"id":"taskboard-priorities","title":"Review the seeded priorities and remaining-work inventory","project":"taskboard","status":"review","milestone":"Useful next actions","note":"The seed is derived from repository docs and this conversation, not a comprehensive audit of other sessions. Review project boundaries and next choices before expanding it.","acceptance":"Confirm or amend the project map and near-term tasks; retain options as backlog rather than treating them as approved work.","reviewed":"2026-11-14","depends_on":[],"refs":["taskboard-standard","orb-results","reliability-spec"]}
```
