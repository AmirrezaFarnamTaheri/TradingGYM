# ADR-023: Omni-Intelligence Backend Abstraction

**Status:** Accepted
**Date:** 2026-08-24

## Decision

Introduce a pluggable intelligence layer (`trading_gym/intelligence/`) that decouples the *decision source* from the *execution path*. Five backend types cover every compute condition:

| Mode | Source | Condition |
|---|---|---|
| `rl` | Trained SB3 policy (CPU/GPU) | Historical path; unchanged |
| `llm` | OpenAI-compatible chat endpoint | Local Ollama or hosted API |
| `fusion` | RL proposes, LLM governs | Best of both |
| `rules` | Deterministic momentum/defence floor | Always available |
| `auto` | Registry walks priority order | Zero-config resilience |

The LLM transport consumes the existing governed provider hub (`agent/providers.py`) rather than introducing a second credential system. All backends produce the same `DecisionSignal` type and are clipped into environment action space via `IntelligencePolicyAdapter`, which exposes the SB3-compatible `.predict()` surface — zero trade-loop changes.

## Rationale

The platform was hardwired to a single SB3 model. Owners want the bot to run across GPU/CPU/local-LLM/hosted-API conditions without code changes. The provider hub already solved credential management and health probing; the intelligence layer adds only the decision contract and the arbitration rules for fusion mode.

Fusion arbitration is deterministic: RL proposes direction + magnitude; the LLM governor may scale magnitude (proportional to its own leverage suggestion), veto to flat (at high conviction), or flip direction (only with explicit strong disagreement AND `allow_flip=True`). Governor unavailability degrades silently to pure RL.

## Consequences

- `AppConfig.intelligence` is optional (`None` = legacy RL-only path, byte-identical).
- The pre-trade gate, coordinator, and mutation-retry journal apply identically regardless of backend.
- LLM outputs are advisory signals bounded by clip → gate → coordinator; they never grant authority.
- The deterministic rule backend ensures the bot always has a decision source, even without GPU, models, or network access.
