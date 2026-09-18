# ADR-035: Rust Simulation Kernel Behind an Optional, Parity-Tested Seam

**Status:** Accepted
**Date:** 2026-09-02

## Decision

The playground's bar loop is reimplemented in Rust (`rust/axiom_kernel`, a `cdylib`) and loaded through `ctypes` (`trading_gym/playground/kernel.py`). Three rules make the acceleration non-negotiable rather than a convenience:

1. **Optional.** `load_kernel()` returns `None` when no artifact exists or its ABI version is unknown, and never raises. `run_playground` then uses the Python loop, so a machine with no Rust toolchain is a *supported deployment*, not a broken one. `AXIOM_KERNEL_DISABLE=1` forces the Python path; `AXIOM_KERNEL_PATH` points at a specific artifact.
2. **Reference-defined semantics.** The Python loop in `runner.py::_run_reference` is the semantics of record. The Rust code is written in the *same expression association order*, statement for statement, so IEEE-754 doubles come out bit-identical rather than merely close. `tests/playground/test_kernel_parity.py` asserts equity curves with `np.array_equal` on raw doubles and compares trade ledgers field by field over randomised windows, both cost regimes, partial position fractions, and the force-close edge case. A discrepancy is a kernel bug, never a new behaviour. Reordering an expression in the kernel is a behaviour change even when algebraically equivalent.
3. **Fail-closed FFI.** The C entry point validates null pointers, absurd lengths, capital, fees, position fraction, and price finiteness, and returns a negative status having written nothing; the Python side raises `KernelError` on any negative status instead of silently degrading. A rejection is a real disagreement about validity, not a reason to fall back.

The kernel is pure: no allocation beyond caller-provided buffers on the FFI path, no I/O, no clock, no randomness, and no reachable authority — it computes an equity curve and trades from numbers it is handed. Build artifacts are gitignored (`rust/*/target/`); the crate source is tracked.

## Rationale

The playground loop carries position state across bars, so it cannot be vectorised, and the strategy forge (ADR-034) runs it (candidates × sealed windows) times per generation. That makes it the platform's one genuinely hot numeric path, and the only place where a compiled language buys something Python cannot.

The alternative designs were worse. A build-time extension module (PyO3/maturin) would make a Rust toolchain a hard install dependency for a component that is optional by design. Rewriting the loop in NumPy is impossible without changing semantics (the state dependency is real). Accepting "close enough" floating-point agreement would mean two implementations that disagree about equity by 1e-12 — and since revision decisions compare final-equity *ratios* against a 0.98 regression floor, a drifting kernel would eventually flip a gate outcome. Bit-identity is the only contract that keeps the accelerated and reference paths substitutable.

`ctypes` over a `cdylib` gives a stable C ABI with no build-time coupling: the artifact is loaded if present, ignored if not, and refused if its ABI version does not match.

## Consequences

- **Positive.** The forge's evaluation loop gets a compiled inner loop without adding an install dependency or a second source of truth for simulation semantics. The parity suite doubles as a regression test on the Python reference: changing either implementation alone now fails a test.
- **Negative.** Two implementations of one loop must be maintained together, and the parity tests only run where an artifact exists (they skip otherwise, while the loader-contract tests always run). Building on a machine without a C linker requires the toolchain's bundled `rust-lld` and a user-owned sysroot — documented in the crate, not automated.
- **Risks.** Silent divergence if someone edits one loop and not the other (mitigated: the parity suite compares bitwise, and any edit to either side fails it); ABI drift (mitigated: `axiom_kernel_abi_version` is checked at load and a mismatch degrades to Python rather than crashing); unsafe FFI (mitigated: the single `unsafe` block validates every pointer before constructing a slice, and `panic = "abort"` in the release profile means no unwinding across the FFI boundary).
- **Composition.** Consumed only by `run_playground`; everything above it — the bridge, the forge, revision evidence, Monte Carlo attribution — is unchanged and cannot observe which loop ran. `kernel_status()` exposes availability plus the reason for an operator surface.
