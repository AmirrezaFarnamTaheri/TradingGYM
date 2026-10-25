> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# Measured Rust ADX/DMI acceleration

## Scope and language choice

The stateful Wilder ADX/DMI recurrence in `trading_gym.agent.strategy_lab.adx.compute_adx` is now optionally executed by the existing Rust simulation library. This is a numerical research kernel, not an order router, credential service, risk-policy engine, or autonomous grant of trading authority.

An initial profile of 100,000 bars measured a Python median of 0.371 seconds over five calls. In the instrumented profile, essentially all time was in the recurrence, per-bar finiteness checks, and Python scalar operations; Pandas selection was negligible. That establishes a useful native-port target rather than choosing a language first.

Rust was chosen because the project already has a dependency-free Rust cdylib, loader, build job, and parity seam. No new crate dependency or runtime was added. Go is deferred until a measured concurrent-I/O workload or service boundary warrants it. C# is deferred until a concrete .NET-only broker/platform integration warrants its toolchain and deployment burden; neither is rejected universally, and neither is added cosmetically.

## Implementation

- [`kernel/src/adx.rs`](../../kernel/src/adx.rs): safe slice-based recurrence, validation, native unit tests, and a documented C boundary.
- [`trading_gym/agent/playground/kernel.py`](../../trading_gym/agent/playground/kernel.py): optional-symbol discovery, typed ctypes arguments, contiguous buffers, Python `math.fsum` seeds, and invalid-output rejection.
- [`trading_gym/agent/strategy_lab/adx.py`](../../trading_gym/agent/strategy_lab/adx.py): selects the native result when available; retains the unchanged Python recurrence as the reference and fallback.
- [`scripts/benchmark_adx.py`](../../scripts/benchmark_adx.py): bounded, deterministic offline benchmark; verifies bitwise equality before reporting timings and refuses to claim a native result when the ADX entry point is unavailable.

`axiom_wilder_adx` is an additive ABI-1 symbol. Existing simulation entry points and signatures are unchanged. An old ABI-1 artifact without this symbol returns to the Python reference, as does an unavailable/disabled library. Native rejection is not an availability fallback: Python raises and does not publish partial outputs.

The native loop preserves expression association and IEEE-754 double arithmetic, NaN warmup, zero-direction behavior, and output naming/index alignment. Seed sums stay in Python's `math.fsum`; no naive native summation replaces them. There is no randomness, I/O, clock access, allocation, secret lookup, or venue capability in the Rust recurrence. Safe-wrapper output arrays are newly allocated and disjoint; inputs are not mutated.

The unsafe FFI function requires aligned, appropriately sized, non-overlapping buffers for the lifetime of the call. It rejects null pointers, impossible lengths, invalid periods/seeds and invalid numeric inputs before the safe recurrence; runtime overflow returns a negative status and invalidates every output. This FFI is a trusted in-process compute boundary, not a sandbox for arbitrary pointers or untrusted native binaries.

## Paired benchmark

Measured on the local Windows / Python 3.11.9 environment with NumPy 1.26.4, Pandas 2.2, and the release Rust artifact. Workload is synthetic finite OHLC, period 14, seed 917. Seven samples per backend alternate evaluation order; correctness is checked before timing. Timings include DataFrame admission, NumPy preparation, seeds, allocations, FFI and recurrence, but exclude compilation and initial library loading.

| Bars | Python median | Rust median | Paired speedup |
|---:|---:|---:|---:|
| 1,000 | 2.2029 ms | 0.4347 ms | 5.07× |
| 10,000 | 17.4371 ms | 0.6379 ms | 27.34× |
| 100,000 | 172.5577 ms | 6.2207 ms | 27.74× |

The initial profiling median and the later paired median differ with runtime load; the speedup uses only the paired measurements, not the larger initial baseline. These are local ADX measurements, not a whole-bot speedup, broker-latency benchmark, or cross-platform guarantee.

Build using the existing locked crate:

```bash
cargo build --release --locked --offline --manifest-path kernel/Cargo.toml
```

Reproduce the measured synthetic workload in the same local test environment:

```bash
scratchpad/refactor-venv/Scripts/python.exe scripts/benchmark_adx.py --bars 1000 10000 100000 --repeat 7
```

The equivalent installed-project invocation is:

```bash
poetry run python scripts/benchmark_adx.py --bars 1000 10000 100000 --repeat 7
```

`AXIOM_KERNEL_DISABLE=1` retains the Python-only path. `AXIOM_KERNEL_PATH` can select the trusted compiled artifact. Rebuild an older artifact to get ADX acceleration; its existing simulation functionality remains compatible.

## Validation

- Rust tests: **12 passed** (the existing seven plus five ADX contracts).
- Rustfmt and Clippy with warnings denied: **passed**; release build: **passed**.
- Initial native / existing simulation parity and ADX contracts: **58 passed**.
- Full strategy/playground suites, expanded native tests, benchmark CLI and CI single-pass checks: **276 passed, 1 skipped**. The skip is an existing CLI symlink fixture that the local Windows account cannot create, not native parity. Python bridge coverage **86%**, ADX module **97%**, combined **90%**. This is Python coverage, not a Rust line-coverage claim.
- Expanded parity covers all four components, random windows and periods, multiple numeric scales, flat data, noncontiguous and deliberately unaligned inputs, original-frame immutability, missing/old libraries, invalid dimensions/lengths/periods/values, overflow, and negative native status without silent fallback. A safely intercepted pointer test reproduced the unaligned-view hazard before Rust dereferenced anything; the bridge now uses NumPy's existing `require` with contiguous/aligned requirements.
- The existing kernel CI job now runs ADX native parity against its compiled artifact. An explicitly requested artifact that cannot load fails the new native test fixture instead of passing through a skip. Ordinary installations without the optional library remain supported.
- Whole Python suite on the final aligned port: **3,039 passed, 33 failed, 16 skipped, 18 errors**. Comparing bounded failure/error identifiers against the previous full-suite baseline found **no new identifiers**; the same 33 failures and 18 errors remain, so this is not a clean full-suite result. Raw output: ignored `scratchpad/native-port-full-tests.txt`.
- An AST comparison confirms the retained Python recurrence is unchanged from upstream; only optional dispatch and output allocation were added around it.
- **45 selected fusion cases passed, 61 deselected** in a separate characterization run. These are mocked/local algorithm tests, not actual trained-model, provider, or funded-venue qualification.

Raw local measurements: ignored `scratchpad/adx-benchmark-before.json`, `adx-profile-before.txt`, `adx-benchmark-after.json`, `adx-native-validation.txt`, and `fusion-characterization.txt`.

## Fusion qualification remains separate

There is a real RL-proposal / LLM-governance backend and deterministic fusion arbitration. It supports veto/size adjustment and configured disagreement handling, but the current API still denies funded live operation unless intelligence is qualified RL-only. This port does not remove that restriction or resolve the previously recorded full-suite, credential/fallback, broker-settlement, multi-writer, and deployment qualification gaps. See [the earlier evidence](../DELETION_REVIEW.md) for the outstanding baseline rather than assuming native speed establishes trading safety or profitability.
