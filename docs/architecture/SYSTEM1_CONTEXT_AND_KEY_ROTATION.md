> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# System-1 inference context and multi-key provider governance

Updated: 2026-10-15. Applies to the advisory-plane integration on TradingGYM PR #2.

## What a System-1 model actually receives

Neither OpenAI-compatible chat completions nor the native
`/v1/systemone` endpoint inherits the trading agent's process state or
previous prompts automatically. Every inference builds a **fresh, bounded
decision-time JSON packet** (`system1.market.v1`) from
`MarketContext`, with the current model's provider-independent task
instructions supplied separately.

The packet deliberately contains:

1. **Market/account state:** symbol, current live *mark quote* versus
   historical close, optional preceding close, equity, signed position
   units, margin ratio, and measured drawdown.
2. **Causal technical evidence:** recent returns explicitly labeled by
   observation cadence, plus a small named subset of finite, *normalized*
   indicators from the real closed-bar feature computation. Normalized
   indicators are not exchange prices or real broker account balances.
3. **Time and provenance:** the opening Unix timestamp, interval in seconds,
   and SHA-256 fingerprint of the validated closed-OHLCV source. In live
   mode, source must be a **completed**, recent bar, with at least three
   closes for independent return sampling. A valid-looking hash alone
   does not prove the bar is complete; the actual feature assembler must
   construct and validate the source.
4. **Operator decision bounds:** the signed minimum/maximum leverage
   constraints. Both fast chat and native finite-choice System-One models
   validate the bounds **before** inference, and clamp/forbid actions after.
5. **Optional memory digests:** settled short-term cycle history when
   `system1_use_working_memory=true` and an actual verified memory object
   was explicitly connected; bounded read-only long-term experience advice
   only with `system1_use_experience_memory=true` and verified live
   freshness. Raw chat transcripts, untrusted tools, API keys, and private
   journals are not supplied. Offline/historical replays never retrieve
   live future experience.

Packet size is capped; optional history and indicators are dropped
first. Essential market/account/risk/freshness fields are never truncated
mid-JSON to fit a token budget.

## Model selection and escalation

`IntelligenceConfig.mode=system1` accepts ordered
`system1_provider_ids` (up to four), `system1_confidence_floor`,
`system1_timeout_seconds`, and `system1_max_leverage`.
Providers may use `openai_chat` or the native finite-choice
`systemone` protocol; these are **different request and response
contracts** and must not be substituted.

A strict directional prediction below the threshold is an
`UncertainDecision`. A second qualified fast model or explicitly
configured strict deep reviewer may review it. If no such reviewer
produces an admissible signal, the posture is neutral HOLD—not a
risk-on RL/rules trade triggered by a parsing/uncertainty failure.
An explicit conservative HOLD remains HOLD even when confidence is low.
Malformed model output, invalid risk limits, and forged/stale context
are authorization/evidence failures, never ways to bypass the live gate.

System-1 remains advisory; neither protocol is permitted to place broker
orders or replace the execution coordinator.

## Multiple API keys and rate limits

A single provider preset can specify `api_key_env` (legacy) and/or an
ordered `api_key_envs` tuple/list of **environment variable names**.
Actual tokens are read only at request time and never stored in model
presets, JSON telemetry, rotation records, prompts, or audit details.
Avoid referencing the same secret value under multiple names.

`CredentialPool` shares round-robin cursor and temporary key
cooldowns across workers with the same data home; its JSON contains only
non-secret hashed variable *names* and expiry times. No key value is
persisted. Selection and cooldown mutations are protected by a
cross-process file lock. An optional `max_requests_per_hour` enforces
a shared *HTTP attempt count* before every upstream request; it is
not a token-budget or monetary-spend ledger.

By default, HTTP 429 applies to the entire provider fleet and blocks all
keys until cooldown expires. `rotate_on_rate_limit=true` is narrowly
opt-in **only** for providers that explicitly document truly independent
per-key rate-limit buckets; it is forbidden for native System-One
providers. Key replacement never bypasses declared project/organization
scopes. Terminal billing/credit/usage codes—including
`credit_balance_exhausted`,
`project_spend_limit_exceeded`,
`organization_spend_limit_exceeded`, and
`organization_usage_limit_exceeded`—do not rotate and are treated as
hard authority denials. Likewise, corrupt shared rotation state, invalid
clock values, and unreadable locks block inference rather than silently
falling through to another trading strategy.

Transient network failures or per-key 401 errors may try another unique
key within a bounded total deadline and a maximum number of attempts.
Remote protocol disconnects are recoverable within that same cap;
explicit 403 denials are not. This is *availability failover*, not a
technique for evading a provider's quotas.

Provider documentation:
- https://developers.openai.com/api/docs/guides/rate-limits
- https://developers.openai.com/api/docs/guides/error-codes

## Remaining qualification limits

This work does **not** establish measured trading alpha, probability
calibration, beneficial distillation, lower latency in production,
cross-machine strongly consistent credential quotas, network-layer DNS
pinning, broker financial reconciliation, or funded-live execution
readiness. The file lock is scoped to workers sharing the same durable
filesystem, not independent machines. For regulated or funded operation,
require a central admission service, egress policy, operator-approved
model evaluations, causal walk-forward/backtest harness, exact-head CI,
and real broker emergency/settlement qualification.

No execution role can be acquired from a model recommendation. Keep
PR #2 in draft and funded live **NO-GO** until independent release
and trading safety gates pass.
