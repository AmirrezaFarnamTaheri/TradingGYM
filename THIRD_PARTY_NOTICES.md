# Peer-project provenance and licence notes

This release was produced through evidence-led convergence. The implementation in this repository is target-native and distributed under the repository's AGPL-3.0-only licence. The peer projects below were inspected for mechanisms, tests, architecture, product workflows, constraints, and negative lessons. No unlicensed donor source file is copied into the runtime.

| Peer | Observed licence/provenance | Contribution form |
|---|---|---|
| EchoBird | MIT licence file | Provider/model hub UX, environment-secret references, bounded command discovery, health and repair concepts |
| QuantDinger | Apache-2.0 licence file | Security boundaries, MCP/tool contracts, strategy lifecycle, data-provider resilience, portfolio monitoring, order-status reconciliation, observability and approval evidence |
| Vibe Research | MIT licence file | Deterministic dossiers, source registry, repository-external durable portfolio state, atomic migration, bull/bear/moderator perspectives and reflection audit |
| Vibe Trading | No root licence found | Reference-only product concept and shell-enabled research anti-pattern converted into a negative guardrail; no source copied |
| System Design Vibecoding | MIT licence file | Reliability, distributed locking, failure handling, SLO/error-budget, API and security design guardrails |
| vibe-quant | MIT licence file | Declarative indicators, overfitting controls, deterministic replay/drift, experiment provenance, research/paper-trading separation and reconciliation concepts |
| vibe-trade | `package.json` declares MIT; no root licence file found | Trade-record capture, order-status lifecycle, trigger/schedule and human-approval concepts; independently reimplemented |
| vibetrading | `pyproject.toml` declares MIT; no root licence file found | Strategy templates, validation, directional-slippage oracles, backtest comparison and prompt-boundary concepts; independently reimplemented |

Exact evidence paths, SHA-256 hashes, dispositions, transformations, target nodes, test links, and licence notes are recorded in `adoption_ledger_9.0.0.csv` and `surface_accountability_9.0.0.csv` in the separate release evidence bundle. Where a repository lacked an explicit root licence, only independently rederived concepts, product observations, or negative lessons were used; no source was transplanted.


> **Owner direction (2026-08-24):** this deployment is private; third-party license compatibility is not treated as a gating criterion for internal absorption decisions. Provenance for every absorbed mechanism is nonetheless recorded in `docs/convergence/absorption-2026-08-24.md` and the adoption ledger.

Owner directive (2026-08-24, reaffirmed): ALL third-party software — GPL, BSD, Apache, UNLICENSED, or otherwise — is treated as MIT-equivalent for absorption into this private deployment. No license-compatibility gate applies to any absorption decision.
