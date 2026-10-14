# ADR-039 — Typed System-One fleet and second-order donor convergence

**Date:** 2026-10-15. **Status:** integrated on draft PR #2; exact-head CI,
external model and funded-trading qualification **outstanding**.
**Scope:** 12 additional user-supplied archives, building on ADR-037/038.
**Authority:** no direct brokerage, capital, permission or deployment mutation.

## Evidence inventory and review depth

The reproducible donor inventory spans **22 archives, 8,247 file entries,
1,574 observed nested folder paths, 7,923 unique SHA-256 content hashes** and
**280 duplicate-content groups**. Of these entries, 3,795 were classified as
source and 1,716 as tests; the rest include docs, generated code, config,
binaries, assets and embedded Git histories. Classification and syntactic
symbol extraction are exhaustive at the *metadata/index* level only. The
code-to-product judgments below derive from inspected docs and selected
representative modules. No claim is made that all 8,247 entries have been
individually executed or method-by-method proved superior.

### First-order component crosswalk — new donors

| Archive | Inspected architecture and distinctive capabilities | TradingGYM decision |
| --- | --- | --- |
| `jev-model-router-main(2).zip` | Python `model_router/catalog.py`, `classifier.py`, `scorer.py`, `router.py`: TTL provider catalogue, hard capability/cost filtering, routing profile, cheapest-first classifier-failure behavior | **Adapt rules conceptually**, not unrestricted dynamic trading-model selection. Unknown/negative price or missing capability evidence must never be treated as free or qualified. A live catalogue is not a verified trading-signal calibration. |
| `OpenJev-main.zip` | TS `src/lib/evaluate.ts`, UI question editor/results, Express gateway: parallel and one-shot question simulation plus a separate native decider route | **Implemented protocol compatibility** with the finite-choice `state + questions` native endpoint, not its client proxy, UI credentials or free-form LLM-generated scores. |
| `Jev-Auto-Router-master(1).zip` | `src/discovery.ts`, `route-plan.ts`, `policy-guard.ts`, `verification.ts`: per-call routed model/effort choice, explicit eligibility, independent final verification and receipts | **Reuse admission/verification principle**; no unsolicited model switching, shadow-route escalation, or claims of measured quota savings in TradingGYM. |
| `jev-codex-router-main(1).zip` | `router/src/catalog.mjs`, context-window drift, compaction checkpoints, OAuth/key journals, fallback telemetry, substantial control-center UI | **Do not port a second proxy runtime.** Existing provider hub owns secrets, request budgets and authority. Context compaction must preserve financial provenance and as-of time; speculative fallbacks are offline-only. |
| `jev-review-main.zip` | TypeScript git/review adapters, changed-file and codebase judgments, report store/dashboard | **Complement** the offline file-hash source-impact report added in ADR-038. No LLM review output auto-applies code or overrides deterministic release contracts. |
| `JevLoop-main.zip` | `src/decide.ts`, `budget.ts`, `context-prune.ts`, provider fallbacks, event/meter/session machinery; multiple independent questions batched under one bounded state | **Implemented two independent typed questions in one native request** (direction and risk-off). Reject malformed answers; native provider failures remain explicit and cannot be construed as approvals. |
| `memreduct-master.zip` | Windows memory working-set cleaner, binaries, settings files, C/C++ | **Exclude runtime import.** Neither quant research memory nor GPU inference context pruning is Windows working-set trimming; cross-process OS memory mutations are an unrelated risk. |
| `FastAgent-main.zip` | Python agent coordinator, grounding/quality score store, MCP/GUI/web/shell transports, sandbox layers | **Keep as isolated offline research inspiration.** Existing TradingGYM agent/tool scopes and governance remain authoritative. Third-party GUI/shell or MCP access must never be installed inside a funded trade worker merely to expand agent tools. |
| `memory-landscape-master.zip` | Survey document, PDF, images and structured memory literature; no executable application layer | **Use as reference taxonomy only.** Working, episodic, semantic and audit-evidence stores should not be merged without time, provenance, privacy and retention policy. |
| `medulla-main.zip` | Prebuilt cross-host coding-agent terminal, mostly docs and release/install script | **Do not absorb an opaque executable.** Remote terminal orchestration is a developer-plane concern and cannot provide trading-system process authority. |
| `go-system-one-main.zip` | Go `/v1/systemone` constrained-choice/noul/score API, 1 MiB strict request cap, native GPU scorer, kernel and calibration tests | **Native typed transport implemented** for its documented HTTP schema; no claim that a deployed checkpoint is calibrated, GPU timings reproduce, or model weights/licensing were imported. |
| `JevRev-main.zip` | TypeScript workflow evidence scores, round audits, frozen criteria, deterministic objective dominance, review budget and state machine | **Adopt verification philosophy**, not code: independently passed correctness/replay evidence must precede promotion. Existing release gate and human approval remain primary. |

The other ten archives continue to be accounted for in ADR-037 and ADR-038,
including Conduit source/change graph, Jev evidence narrowing, 9Router transport
and quota mechanisms, graphify/codegraph source graph engines, and read-only
market-microstructure donor functions.

## Implemented native advisory protocol

A `ProviderConfig` now has an **opt-in** `wire_protocol` enum:
`openai_chat` (default and backward compatible) or `systemone`.
The latter has a validated `systemone_path` (default
`/v1/systemone`). CLI providers cannot use the native HTTP protocol. Normal
LLM mode rejects native providers instead of making a mistaken chat call.
System-1 factory selection uses the typed backend only for those configured
native providers; all other model slots retain strict chat parsing.

The native backend:
1. Uses TradingGYM's current bounded `system1.market.v1` market/account/risk
   packet, live closed-bar freshness verification and explicitly consented
   settled-memory digest. Stateless native models receive the complete packet
   afresh **as JSON state**, not a blob silently spliced into instructions.
2. Sends independent `direction: choice(hold,long,short)` and
   `risk_off: noul` in **one typed request**. Question text is fixed by
   application code and explicitly excludes execution permissions.
3. Requires exact answer keys, finite 0..1 probability members, normalized
   distributions, a matching winning label and finite confidence. Risk-off
   probability >=0.5, tied winners and zero risk headroom force HOLD;
   malformed and weak answers cannot fabricate a confident direction.
4. Computes signed leverage from bounded local caps rather than accepting
   a model-generated magnitude; keeps the advisory-only/no-authority contract.
5. Reuses the existing env-name credential pool, cross-process hourly
   request ledger, 429/403 taxonomy, total deadline, DNS validation,
   `trust_env=False` and `follow_redirects=False`. Budget corruption,
   authority denial and stale live evidence propagate fail-closed.
6. Makes POST-only native services probeable: an HTTP 405 on the exact
   native endpoint identifies **route reachability only**, not a calibrated
   model or a correct prediction.

### Safe opt-in examples (disabled by default)

Register providers through the governed provider repository/API, with real
operator-selected `provider_id` and model ID. The following are **sample
configuration templates**, not auto-registered executable presets:

```json
{
  "provider_id": "local-gosystemone",
  "kind": "local",
  "display_name": "Go System-One typed advisory",
  "model": "YOUR_LOADED_MODEL_ID",
  "base_url": "http://127.0.0.1:8080",
  "wire_protocol": "systemone",
  "systemone_path": "/v1/systemone",
  "max_requests_per_hour": 60,
  "enabled": false
}
```

```json
{
  "provider_id": "local-decider",
  "kind": "local",
  "display_name": "Mapika decider typed advisory",
  "model": "YOUR_PINNED_DECIDER_ID",
  "base_url": "http://127.0.0.1:8000",
  "wire_protocol": "systemone",
  "systemone_path": "/v1/systemone",
  "enabled": false
}
```

For Ollama, LM Studio and vLLM OpenAI-compatible endpoints, preserve the
`openai_chat` default with approved loopback `/v1` base URLs and real
model IDs; for hosted OpenRouter use `kind=openai_compatible` and an
environment-variable **reference** (not a stored key). Those are different
inference transports from the typed native endpoint.

Example `intelligence` configuration:

```yaml
intelligence:
  mode: system1
  system1_provider_ids: [local-gosystemone, local-decider]
  system1_confidence_floor: 0.75
  system1_max_leverage: 0.5
  system1_timeout_seconds: 5
  system1_use_experience_memory: false
  allow_fallback_to_rules: true
```

Provider entries must be individually registered, enabled, reachable and
allowed to receive the input context. A paper/dry-run eligibility decision
does not override existing funded-live qualification.

## Second-order convergence: separate product planes

1. **Model Capability/Fleet Plane** — canonical protocol, model, cost,
   context limit, rate-limit/billing scope and operator consent; deterministic
   admission before semantic routing. Dynamic catalogues and "cheap" heuristics
   require signed metadata and labels before adoption.
2. **Point-in-Time Evidence Compiler** — closed-bar, venue quote, risk
   limits, account state, as-of memory and hashed provenance as distinct
   typed evidence cards with independent freshness and bounded length.
   Disallow future observations, malformed risk data and unsealed
   historical memory from any fallback path.
3. **Native Decision/Calibration Plane** — quality, abstention, probability
   calibration, ambiguity handling and replay receipts for choice/noul/score
   outputs. Report classifier reliability and trade outcomes **separately**;
   neither raw max probability nor Jev/Go confidence equals measured alpha.
4. **Independent Verification/Promotion Plane** — JevRev-style frozen
   acceptance tests, reproducibility, protected surfaces, holdout replay
   and source-bound audit receipts before any human-authorized promotion.
   The model classifier cannot grade its own trading correctness.
5. **Developer Source & Agent Plane** — source graph and bounded
   memory/context retrieval; multi-agent, shell, GUI, remote terminal and
   code generation only in explicit developer/research sandboxes.
6. **Financial Execution Authority Plane** — single account owner,
   venue settlement/fills/fees, broker fault injection, distributed fencing,
   gross-flat emergency evidence and independent risk controls. No donor
   supplies a production-certified substitute.


## Implemented second-order calibration and replay evidence plane

The standalone, **offline only** `systemone_evaluation.evaluate_records()`
and `scripts/evaluate_systemone.py` analyze independently prepared held-out
records rather than initiating network or brokerage requests. The records
contain a model ID, SHA-256 context/source references, decision time, label
availability time, three directional probabilities, one risk-off probability,
a held-out outcome label and finite elapsed milliseconds. Arbitrary extra
fields (including raw prompts or secrets), train-split labels, malformed
probabilities, unavailable future labels and duplicate model-context records
are rejected. A user-supplied as-of timestamp is required by the CLI.

The deterministic report includes Brier score, ECE (ten bins), accepted
coverage, accuracy among accepted decisions, abstention/risk-off counts,
p95 latency and separate hash commitments for normalized source records and
the report. Hashes support replay matching but **do not authenticate the
upstream source or ground truth**. The evaluator is not a trainer, PnL
simulator, calibration method, promoter, or independent market-risk auditor.
Every report sets `eligible_for_funded_live=false`.

Example:

```sh
python scripts/evaluate_systemone.py \
  --records /path/to/hashed-holdout.jsonl \
  --as-of-unix 1790000000 \
  --confidence-floor 0.75
```

This is the higher-level convergence of JevLoop metering, JevRev's
separate evidence-first review, and Go System One's explicit warning about
uncalibrated probabilities. The isolated, controlled Python suite now covers
40 protocol, adapter, evaluation and CLI cases. Full exact-branch test
verification and representative real-model holdouts remain outstanding.


## Verification boundary and remaining gates

The exact pure protocol contract ran **20/20 tests in an isolated Python
harness**. A further **5/5 backend and HTTP-admission tests** passed using
controlled dependency substitutes. Repository-level tests were added for
provider schema, factory wiring, live provenance refusal, backend parsing,
POST-only native health and budget admission. Those exact-head tests have
**not executed** on hosted CI; they require the complete pinned Python
environment and independently reviewed adapters. Upstream model weights,
real external native service, model-quality calibration, leak-free
historical evaluation, full suite, frontend/Rust/security and broker
qualification remain outstanding.

The manually maintained OpenAPI snapshot must be regenerated from the
application with `python scripts/export_openapi.py` if provider schemas
appear there. Source-accountability SHA-256 entries are tracked in the
generated manifest; the generator's `--check` gate must run on the
final PR head. The branch remains draft and funded live **NO-GO**.
