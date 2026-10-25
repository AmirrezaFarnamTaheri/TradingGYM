> Current follow-up: [four-view product and guarded funded execution](DEFERRED_COMPLETION.md) supersedes earlier three-view and unconnected-submission descriptions below. Funded execution remains disabled by default and no real orders were placed.

# Local autonomy rebuild — finalized 2026-10-25

Subsequent corrections and their separate verification are recorded in [the review follow-up](REVIEW_FOLLOWUP.md), including the Git-ignore packaging regression that excluded the locally verified feature fixtures from c4af6567.

The application opens directly on loopback with three views: Live monitor, Experience and Settings. English and Persian share the same API; Persian uses right-to-left layout and localized numbers. Current guides are indexed in [README](README.md).

The autonomous paper runtime shares a Decimal ledger across markets, persists processed-bar watermarks and financial risk references, models execution costs, and applies exposure, daily-loss, drawdown, and stop-loss limits. Interrupted writes halt further decisions. Stop-loss cooldowns survive restart. The emergency control requires confirmation and supports keyboard dismissal and focus restoration.

The Abantether connector requests actual quotes, balances and order history, and accumulates complete five-minute bars from observations. There is no production synthetic replay or manufactured history. Observe is the default; paper execution is explicitly virtual. SQLite preserves paginated decision/execution history. Broker and paper fills are separate. Recorded decisions expose inputs, head signals, weights, planner output, model/key identifiers, risk constraints and outcomes. Imported fills without a matching decision remain unattributed.

Settings stores encrypted local credentials; Windows protects the master key with DPAPI. Separate LLM and Decision-1 rosters support up to 16 keys each, with endpoint-scoped admission, rotation and cooldowns. Secret values are not returned. Both cockpit gateways use OpenAI-compatible chat JSON; native System-One research protocols remain separate. Authentication/hard-quota failures block exposure; transient planner failures tighten it. Advice cannot increase financial caps.

Live monitor shows tools, enabled versus active heads, gateway slots and the recorded decision route. CPU PPO shadow challengers use actual observed history with chronological training/holdout separation and evaluation through the paper engine. Trial evidence appears in Experience. A real CPU smoke trial verifies execution, not profitable performance.

Python code lives under one `trading_gym` namespace. The optional Rust crate is `kernel/`; source feature fixtures are under `data/parquet/`. The [deletion review](DELETION_REVIEW.md) distinguishes moved source from retired material. Feature-fixture integrity and immutable workflow-pin checks were revived and integrated into CI/local verification. All 28 fixture hashes were recovered from Git's original manifest, with only paths migrated; tampering/membership regression tests were restored. Old release receipts, dummy-data launch paths and donor-report inventories remain retired.

Setup, first run, UI, troubleshooting, paper/live boundaries, training preparation, settings, decision evidence, frontend and test guides now describe current behavior. Retained financial/research/architecture notes have explicit compatibility scope in the documentation index. Local Markdown file links were checked; historical artifacts are marked as retired instead of linked as present files.

## Verification

- Non-integration Python suite with installed ML dependencies: **3,397 passed, 32 skipped, 5 deselected**. This preceded restored fixture tests and final CLI cleanup.
- Focused API/product/credential/history/fixture tests after restoration: **12 passed**. They cover encrypted keys, actual local HTTP rotation/admission, decision/fill links, observe-without-fills, secret redaction and fixture membership/tampering.
- Final runtime/planner regressions: **11 passed**, including risk halts, cooldowns, restart recovery, pause and planner limits.
- Frontend interaction tests: **6 passed**, including navigation, controls, decision inspection and Persian RTL with isolated test responses.
- Production frontend build: passed. Frontend dependency audit: zero vulnerabilities at verification time.
- Pre-commit dependency audit: npm again reported zero vulnerabilities. `pip-audit` against the installed Python environment reported 295 advisory entries across 30 packages, including duplicate advisory IDs; these are not 295 independently verified vulnerabilities. Python dependency remediation remains outstanding. This installed-environment scan includes development tooling and is not a lock-only production exposure assessment.
- Rust unit tests: **12 passed**. Native/Python parity checks: **47 passed**.
- Changed runtime/API/CLI and restored check lint/format: passed.
- Source distribution and wheel: built successfully. Poetry lock check and application-derived API contract checks passed.
- Fixture inventory: **28 original hashes matched**. Workflow action revisions: verified immutable pins.
- Browser: production build opened on port 8000; all three views, Persian layout, credential controls and actual empty/unavailable states checked at the available in-app viewport. Earlier paper/control exercises are test evidence, not market performance.

These checks describe local work at their stated stages. The refactor remains uncommitted; this is not a hosted CI result or published release. Native code has not changed since its recorded checks.

## Remaining boundaries

**Funded broker order submission is not connected.** Live execution requests are refused. Emergency flatten affects paper positions, not exchange holdings. Broker history is read-only when connectivity and credentials are available.

This environment's Abantether request failed during TLS negotiation (connection reset). No user exchange/model keys are configured. Authenticated balances/history schemas, external inference, real fills and model quality remain unverified. Missing prices, balances and explanations stay unknown. A fresh connector needs 14 complete observed bars for initial signals and 400 before shadow training.

Existing research consumers still depend on the previous API and governance modules. They remain internal compatibility code, with a separate research OpenAPI document, and are not mounted by the cockpit server. Removing those consumers is further backend work; this rebuild does not claim that every historical module was purged.

Docker inputs were repaired but the image was not built here. The local trust boundary supports loopback; ordinary bridge port publishing is not qualified. Broad desktop/tablet/browser accessibility checks remain separate work. No remote publication, credential enrollment or funded transaction was performed.
