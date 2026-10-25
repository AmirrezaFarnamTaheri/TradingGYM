> Superseded for previously deferred scope: see [completed implementation](DEFERRED_COMPLETION.md). The table below records the earlier review stage.

# Review follow-up — 2026-10-25

Two supplied reviews of c4af6567 were checked against repository source. Their claimed v32.1 archive/Canvas implementation was not attached. The changes here are independently implemented; the package remains version 32.0.0 pending release qualification.

| Finding | Repository action |
| --- | --- |
| Ignored feature fixtures | Confirmed. Scoped ignore exceptions and staged all 28 original fixtures plus their original-hash manifest. Local integrity checks alone had missed the Git packaging failure. |
| README contract | Restored the accurate GitHub Actions note. |
| Regime history and chop quorum | Regime uses the first 12 returns, respecting MarketContext's most-recent-first contract. Taking the last 12 would select older data and was rejected during integration. Chop rules weight is 0.8, reaching existing 0.4 quorum at rule confidence 0.5. Risk caps and quorum threshold remain unchanged. This changes paper strategy behavior, not a profitability qualification. |
| Statistics head | Remains advisory HOLD evidence, with recorded confidence/rationale visible in decision details. Corrected its misleading docstring: it contributes neither directional conviction nor a risk veto. Making it a veto requires a separately evaluated policy change. |
| Experience lessons | Canonical market/regime/outcome lessons consolidate support. Exact fill P&L remains in the execution journal. The Experience view shows consolidated lessons as advisory paper evidence. |
| Shadow symbol starvation | Eligible symbols are selected in round-robin order. Retained full paper-engine durability during holdout evaluation; no performance benchmark justified replacing execution semantics with a shortcut. |
| Optional planner heads | Accepts bounded rl/decision1 weights alongside existing arms. Missing or disabled heads do not become available because a weight exists. |
| Market warmup | Exposes observed quotes, spread, closed-bar counts and partial interval OHLC/progress. These are locally sampled observations, not exchange-provided historical candles. Timestamps/source failures remain visible. |
| Settings and local origins | Restores SQLite bars for newly selected symbols, invalidates the agent query, permits the named local development/preview ports, and retains foreign-origin rejection. |
| Equity chart | Unknown P&L stays neutral. Keyboard-accessible record slider and pointer crosshair inspect actual stored equity; paper baseline is excluded in observe mode. No curve is inferred from quotes. |
| Spacing and Persian | Fixed adjacent expression spacing and audited literal translation calls. Brand/model identifiers remain identifiable. Dynamic provider-generated rationales are preserved as received. |
| Doctor | Checks Pydantic, cryptography and source-checkout frontend bundle presence in addition to runtime packages. |
| Fourth Strategy Lab tab | Deferred: it conflicts with the accepted three-view product. Existing research stack remains available internally. No synthetic Canvas comparison, bar-step demo or claimed external performance was imported. |

External Abantether/model qualification, Python dependency advisory remediation and funded order routing remain outstanding as stated in [REBUILD](REBUILD.md). These local changes are not a v32.1 release or proof of real-account trading.

## Local verification

- Focused product/API/planner/history/docs/fixture run: 26 passed before the final additional chop-quorum test. The final review-specific run passed all 4 cases, including actual paper fills in chop and consolidated lessons with exact fill P&L retained.
- Arm/fusion/statistics regressions: 58 passed. Runtime/review regressions after preserving most-recent-first ordering: 11 passed.
- Shadow training/review run: 6 passed, including real CPU PPO holdout evaluation.
- Frontend: 8 tests passed, including quote warmup, neutral null P&L and accessible equity inspection. Production build passed.
- Static literal translation audit: 259 literal keys, no missing Persian entries. This is not a claim that arbitrary provider-generated text is translated.
- Isolated reconstruction from staged Git blobs: 28 fixtures matched the original hashes; 29 data files are now staged (manifest plus fixtures), unlike c4af6567.
- Changed Python lint/format checks and regenerated contracts passed. Current browser checked actual empty lesson/history states in English and Persian; external quote telemetry could not be live-qualified without a working exchange connection.

The whole Python suite was not repeated for this follow-up; earlier full-suite counts in REBUILD describe the earlier tree. Changes are local and uncommitted.
