# ADR-036 — System-1 contextual inference and multi-key provider fleet

**Status:** Implemented for advisory/paper and non-mutating live dry runs;
**not qualified for funded live execution** (2026-10-15).

## Problem and current route

Legacy `LLMDecisionBackend.decide` supplied a single, stateless JSON market
snapshot to one configured OpenAI-compatible chat endpoint. It truncated the
serialized JSON at 4,000 characters, did not transmit verified source-bar
provenance or named features, and did not retrieve bounded working/experience
memory. An `api_key_env` named one secret, with no fleet-wide coordination
between independent workers.

A model's context window is **not** its memory. Chat completion does not
automatically retain TradingGYM's preceding bars, fills, decisions, or
operator state. Each relevant, point-in-time observation must be supplied
again. More tokens without provenance would only produce more chances for
stale data, unbounded instructions, future leakage and hallucination.

## Implemented context packet

`system1.market.v1` is generated afresh from `MarketContext` with bounded
JSON rather than by cutting a string. Required fields never get truncated:

- current mark, previous close and recent realized returns;
- equity, signed position, margin information and peak drawdown;
- signed action bounds and externally supplied finite risk constraints;
- for real-time inference, a completed candle's opening timestamp, duration,
  age and `source_sha256` from the validated feature assembler;
- compact named, *normalized* closed-bar indicators if available;
- optional settled working-memory cycles and a bounded digest of historical
  experiences (warnings/confirmations with support).

The fast model is instructed to treat memory and every externally supplied
field as untrusted **data**, not executable commands. Memory never grants
market authorization, increases a risk limit or rewrites the coordinator.

Freshness is fail-closed: live context without a valid, fully closed,
timestamped source bar is rejected before sending a prompt. The builder
checks finite monetary quantities and rejects stale source evidence.
Historical evaluation does not load the persistent experience journal,
because its later records would create hindsight/leakage. A genuine
point-in-time backtest must supply a historically sealed as-of memory
snapshot separately before use in model calibration.

**Sampling matters:** the latest tradable live mark is not a completed candle.
System-1 receives the last validated base-timeframe closed-bar price sequence
for its returns, while the mark price is separately labeled as a live quote.
When an older embedded environment lacks that series, update-to-update
returns are explicitly labeled `mark_updates` rather than passing as
completed bars. The normalized feature vector remains named separately;
features and monetary position/account quantities are never conflated.

**Memory is evidence, not stateful chat:** verified experience retrieval opens
the hash/anchor journal without rewriting it or pruning expired entries on
disk. Truncation with a surviving anchor fails verification. Optional
settled working-memory disclosure is independent opt-in and only available
if a real as-of `WorkingMemory` is wired; standard paper/live runners do not
yet pass one. Enabling it without a source now fails at policy construction
instead of silently supplying an empty or fabricated history.

The maximum context is 4,000 characters by default. When optional material
does not fit, the builder deterministically removes experience, working
memory, optional indicators and extra returns in that order. Essential
account/risk/freshness data cannot be silently removed; oversize essential
context fails closed. Provider call output is bounded and strict JSON is
required in System-1 mode; text keyword guessing remains legacy LLM-only.

## Fast inference and escalation

`IntelligenceMode.SYSTEM1` accepts up to four ordered
`system1_provider_ids`. Each provider receives the same structured market
packet but may have its own model, base URL and credential pool.
`System1DecisionBackend` rejects malformed, non-finite or incoherent
direction/leverage responses. A low-confidence **directional** response
raises a distinct `UncertainDecision`, not an ordinary transport outage.
Only another qualified fast model or an explicitly configured, strictly
structured and confidence-thresholded deep `llm_provider_id` can review
that uncertainty. Unreviewed uncertainty becomes a zero-exposure abstention;
it must **never** cascade into a risk-on rules/RL fallback. A model's explicit
HOLD is itself a valid conservative abstention, even at low confidence.
Malformed strict JSON or invalid fields fail closed as evidence violations.
Separate, genuine provider transport failures still use the configured
availability fallback. High-confidence advice is clipped to
`system1_max_leverage` (default 1.0) and remains under the
independent pre-trade/execution safety gates.

A `PermissionError` (unverified live context, missing live evidence, revoked
authority, provider 403 or tampered memory) is *not* an ordinary provider
outage and **must never trigger a more permissive trading fallback**.

The agent's registered System-1 arm consumes the concrete
`research.llm.invoke` scope and is placed after cheap local
rules/statistics, before an expensive T4 LLM. This tier is advisory only.

## Credential pool

`ProviderConfig` accepts legacy `api_key_env` plus the new
`api_key_envs` ordered tuple. References must be unique, syntactically
valid environment **names**, limited to sixteen. The provider repository
stores only those names, never values.

A file-locked, bounded on-disk cursor and cooldown map allows independent
worker processes in one data home to share round-robin selection.
Only hashed reference identifiers, cursor and timestamps enter this file;
the actual keys remain in process environment and outgoing
`Authorization` headers. A single request has a total deadline,
fixed attempt cap, no sleep-based spin or redirects, and validates the
destination before each request. Authentication failures (401) can exclude a
bad key, transient transport/server problems can temporarily cool it down,
and 403 is terminal authority denial.

Rate limiting is not a key-bypass feature. HTTP 429 applies a shared
project-level cooldown by default, respecting Retry-After. An explicit
`rotate_on_rate_limit: true` is appropriate **only when the provider
documents independent per-key buckets**. Project hard quotas/billing caps
always stop rotation regardless of that setting.

### Example provider configuration

The operator uses the governed provider-hub repository/API to register
a provider with these JSON fields (values below are *names*, not secrets):

```json
{
  "provider_id": "fast-primary",
  "kind": "openai_compatible",
  "display_name": "Fast advisory provider",
  "model": "your-fast-chat-model",
  "base_url": "https://your-approved-provider.example/v1",
  "api_key_env": "FAST_LLM_KEY_1",
  "api_key_envs": ["FAST_LLM_KEY_2", "FAST_LLM_KEY_3"],
  "rotate_on_rate_limit": false,
  "enabled": true
}
```

Separate System-1 configuration, inside the application's `intelligence`
section:

```yaml
intelligence:
  mode: system1
  system1_provider_ids: [fast-primary, fast-secondary]
  system1_confidence_floor: 0.65
  system1_max_leverage: 1.0
  system1_timeout_seconds: 5.0
  system1_use_experience_memory: false
  system1_use_working_memory: false
  llm_provider_id: deep-analysis
  allow_fallback_to_rules: true
```

The provider `deep-analysis` is optional. Activating historical memory is
an explicit consent/data-governance choice: lessons may contain strategy
information that must not leave the machine without a provider policy.

## Verification and non-goals

Focused isolated tests covered 12 rotation/failover cases and 7 context
cases (red/green); a checked-in regression suite covers provider schemas,
429 and quota behavior, provenance, optional memory, strict parsing, and
factory wiring. Exact-head full Python/TypeScript/Rust CI and real provider
contract tests remain outstanding because GitHub-hosted CI jobs are not
executing on runners.

These changes do **not** certify fast-model trading alpha, latency targets,
token economy, causal signal calibration or gross-flat recovery. Funded
live remains admitted only with the existing hash-pinned RL runtime and
independent venue, accounting, rollback and emergency safety qualification.

## Follow-up: project cooldown and signed risk windows (2026-10-15)

Provider-wide rate limits and hard quotas are **not attached to a model
version or particular credential roster**. The shared rotation-state
filename now depends on provider ID and validated base URL, rather than
model/credential-list membership. A reserved provider-level cooldown
survives swapping all keys or changing models. Per-key cooldowns retain
bounded hashed slots for temporarily removed keys; expired slots are
pruned. The 429 classifier recognizes hard quota conditions in both
`error.code` and `error.type`, and provider-declared
`X-RateLimit-Scope: organization|project|account|global|tenant|workspace`
prevents per-key retry even when rotation was explicitly opted in.
No design can evade provider-side organization limits or make API-key
rotation a substitute for account permission.

Both chat-based and typed native System-1 adapters now independently enforce
a **signed advisory risk window** derived from the operator's minimum and
maximum leverage and the model's own cap. Invalid limits deny *before*
any provider inference call. An inadmissible directional recommendation
becomes HOLD; allowed long and short values are clipped independently.
For example, `min_leverage=-0.25, max_leverage=0` permits a small short
while blocking all new long exposure, rather than imposing a symmetric
zero limit. Independent pre-trade gates, capital reservations and broker
checks remain authoritative.

Live System-1 inference additionally requires `return_basis=completed_bars`,
a prior validated closed-bar close and at least two completed-bar returns.
A valid source-fingerprint string alone does **not** prove that model return
features came from those candles. Irregular quote-mark updates can still
be labeled for diagnostics in the environment, but cannot masquerade as
completed timeframe returns in the live fast-model packet. A SHA-256
fingerprint establishes content identity, not the independent authenticity
of a venue feed. External data provenance, settlement correctness and
causal predictive performance remain unqualified.

Tests:
`tests/intelligence/test_system1_rotation.py`,
`tests/intelligence/test_system1_risk_bounds.py`, and
`tests/intelligence/test_system1_closed_bar_context.py`.
Isolated red/green reproductions and static source checks do not replace
the full hosted Python/Rust/frontend matrix or real provider certification.


## Follow-up: destination policy, metadata, and unambiguous JSON (2026-10-15)

Provider URL validation is performed again just before each HTTP attempt.
If DNS rebinding or a forbidden IP/address causes that validator (or the
native System-One endpoint join) to reject a destination, the caller now
raises a **terminal `PermissionError("provider_destination_denied")`**.
It must not be converted into `BackendUnavailable`, which otherwise
permits a risk-on fallback. Chat and native transports have independent
regression tests for that boundary.

`ProviderConfig.metadata` is still available for small descriptive labels,
but it cannot contain nested credential-like fields, nonfinite/unserializable
values, or more than 8 KiB of JSON. The provider repository independently
revalidates the object on save, including callers that bypass Pydantic
validators through `model_copy(update=...)`. Credential values remain
in operator environment variables and never in model provider documents.

Strict System-1 JSON is interpreted with duplicate-key rejection at every
object nesting level and with nonstandard `NaN` and `Infinity` constants
disabled. A model reply with contradictory `action` or `confidence`
fields is therefore not accepted just because Python's standard JSON
loader otherwise chooses the final duplicate value. Malformed strict
evidence still denies a risk-on fallback under the existing
`system1_unparseable_model_output` guard.

Targeted repository regressions:
`tests/intelligence/test_provider_destination_authority.py`,
`tests/convergence/test_provider_hardening.py`, and
`tests/intelligence/test_llm_json_ambiguity.py`.

**Verification limitation:** only isolated parser and provider-validator
harnesses could execute locally in this environment; they are not a
substitute for running the full repository Python, frontend, Rust and
integration matrix, or validating real provider response schemas.

## Terminal credential and spending status across workers (2026-10-15)

A transport may rotate after HTTP 401 to another distinct configured key
within the attempt/deadline budget. Once the attempted credentials fail
authentication without a successful request, it returns a non-degradable
`ProviderAuthenticationError`. If *all* configured references were attempted
and denied, a separate typed authentication marker persists for one hour
across workers. A partially examined fleet is not globally disabled merely
because the configured three-attempt bound ended; that request still denies
instead of allowing a risk-on rules fallback.

HTTP 402 and provider-declared credit, organization, project, spend, or
usage-cap codes received via HTTP 429 are terminal `ProviderHardQuotaError`
conditions, persisted as a separate hard-limit slot in the provider-shared
credential state for up to 24 hours. Unlike an ordinary temporary 429,
a subsequent worker receives the **same terminal error class**, not a
generic missing-key status. Invalid 400/404/409/413/422 request/model or
schema responses similarly deny instead of silently falling through into
trading. Ordinary transient 429 cooldowns still respect provider scope and
opt-in per-key rotation; no key rotation circumvents project spending
limits or creates additional API capacity.

The state persists **only hashed credential reference identifiers and
cooldown timestamps**; neither raw key values nor response payloads are
stored. Chat and native System-One transport adapters map durable
authentication/quota errors to `PermissionError` before the intelligence
fallback chain can run.

`tests/intelligence/test_llm_fail_closed_rotation.py` includes shared-state,
HTTP status, fleet-exhaustion, quota-code and transport-level regressions.
The same terminal HTTP policy also applies when a local/proxy endpoint
has *no* credential pool: both chat and native System-One transports call
`reject_terminal_provider_status` before ordinary HTTP error handling.
That prevents `401`, `402`, hard-quota `429`, and invalid request/model
`4xx` statuses from being mislabeled as recoverable inference outages,
without changing temporary `429` availability semantics.

For a verified byte-identical copy of the committed `llm_key_pool.py` module
(Git blob `bb154556569c84d7137e76e43106d300e973718a`), **34 isolated
local tests passed**, including unpooled HTTP status classification.
Transport-level regression tests are also committed, but full exact-head
PR CI and real external provider tests remain **unverified**.
