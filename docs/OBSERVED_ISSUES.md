# Observed Issues & Known Limitations

> **Status:** Finalized
> **Last Updated:** 2025-11-25
> **Version:** 4.2.1 (Final)

This document tracks known issues, limitations, and the resolution status of the Trading Gym system.

## 🟢 Resolved Issues (v4.2.1 Finalization)

*   **Config Migration:** Hardcoded symbol lists and short caps have been migrated to `config/market_rules.json`.
*   **Test Coverage:** Expanded to >96% across critical components (`FeaturePipeline`, `EnhancedAbanClient`, `AssetRegistry`, `Logging`).
*   **Code Cleanup:** Removed legacy scripts (`clean_sweep.py`) and redundant test files.
*   **API Tests:** Consolidated into a canonical `tests/api/test_api.py`.
*   **Execution Tests:** Replaced basic tests with comprehensive scenario-based tests.

## 🟡 Permanent Limitations (By Design)

### 1. Pandas & Numba Interaction
*   **Impact:** Feature Engineering
*   **Description:** On some systems, `pandas` < 2.2.0 interacting with `numba` > 0.59.0 may cause a segmentation fault.
*   **Mitigation:** The codebase explicitly casts numba outputs to numpy arrays. Ensure `pandas` >= 2.2.0 is installed.

### 2. API Rate Limits
*   **Impact:** Live Trading
*   **Description:** The Abantether API enforces strict rate limits (60 req/min).
*   **Mitigation:** The `GlobalRateLimiter` enforces a safety buffer (50 req/min). High-frequency strategies are throttled by design.

### 3. Backtest vs. Live Slippage
*   **Impact:** Simulation Accuracy
*   **Description:** Real-world liquidity can be thinner than the quadratic model during extreme volatility.
*   **Mitigation:** The simulation uses a conservative slippage model, but live results may still deviate during black swan events.

## 🔴 Critical Issues

*None currently active.*

## ✅ Quality Assurance Summary

| Component | Coverage | Status |
|-----------|----------|--------|
| ExecutionEngine | >98% | ✅ Excellent |
| MarginTradingEnv | >95% | ✅ Excellent |
| API Endpoints | >95% | ✅ Excellent |
| Feature Pipeline | >90% | ✅ Excellent |
| Aban Client | >90% | ✅ Excellent |
| Core Utilities | >95% | ✅ Excellent |

---

**Note:** This project is considered feature-complete for version 4.2. Future development should focus on the Roadmap (Phases 7+).
