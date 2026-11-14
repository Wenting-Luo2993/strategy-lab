<!-- taskboard
{"id":"cicd-roadmap","title":"Deployment CI/CD roadmap","projects":["deployment-cicd"],"status":"current","kind":"roadmap","reviewed":"2026-11-14"}
-->
# Deployment CI/CD roadmap

The [reliability specification](../trading-bot-mvp/ibkr-integration/reliability-testing-product-spec.md)
defines the staged gates. At this review, `.github/workflows/` contains Pages
deployment and deployment guardrails, not the proposed full bot-regression gate.

```taskboard-task
{"id":"cicd-httpx","title":"Repair the dashboard API test-harness incompatibility","project":"deployment-cicd","status":"ready","milestone":"Reliable test baseline","note":"34 existing tests use the removed httpx.AsyncClient(app=...) argument. They were excluded from the identity release's 758-test passing run, not silently counted as green.","acceptance":"Migrate the affected test transport pattern and demonstrate the full intended gate without this exclusion.","reviewed":"2026-11-14","depends_on":[],"refs":["reliability-spec","gateway-evidence"]}
```

```taskboard-task
{"id":"cicd-required-tests","title":"Wire bot regressions into CI and define required checks","project":"deployment-cicd","status":"backlog","milestone":"PR regression gate","note":"Reuse existing tests before adding infrastructure. Branch protection configuration is a separate explicit setting, not implied by adding YAML.","acceptance":"Broker/shared-code changes trigger appropriate tests without production secrets; document required-check enforcement and demonstrate a failing change blocks promotion.","reviewed":"2026-11-14","depends_on":["cicd-httpx"],"refs":["reliability-spec"]}
```

```taskboard-task
{"id":"cicd-integration","title":"Add simulated deployment and recovery failure gates","project":"deployment-cicd","status":"backlog","milestone":"Behavioral integration","note":"Exercise unsafe legacy ownership, PendingCancel, partial failures and restoration without touching the Oracle VM.","acceptance":"Unsafe deployment state is rejected before mutation; bot-only updates preserve Gateway ownership and uncertain cancellations cannot authorize duplicate exits.","reviewed":"2026-11-14","depends_on":["cicd-required-tests"],"refs":["reliability-spec"]}
```

```taskboard-task
{"id":"cicd-release-evidence","title":"Standardize approved-release verification evidence","project":"deployment-cicd","status":"backlog","milestone":"Release assurance","note":"The deploy harness is already used. The remaining work is a repeatable evidence contract and gate, not unattended production deployment.","acceptance":"Tie release revision, migration checks, health layers and fresh broker observations to a retained report, with explicit failed and unobserved outcomes.","reviewed":"2026-11-14","depends_on":["cicd-integration"],"refs":["reliability-spec","gateway-evidence"]}
```
