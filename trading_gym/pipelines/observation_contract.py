"""JSON-safe semantics for the existing observation and target-leverage API."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform

NATIVE_UNITS = {
    "log_ret": ("log_return_per_source_bar", "signal"),
    "volatility": ("std_log_return_per_source_bar", "base_execution_context"),
    "rsi": ("fraction_0_1", "signal"),
    "spread_ratio": ("spread_price_over_mid", "base_execution_context"),
    "range_ratio": ("high_minus_low_over_close", "signal"),
    "sector": ("categorical_code", "base_category"),
    "can_short": ("boolean_permission", "base_permission"),
}
RUNTIME_PACKAGES = (
    "numpy",
    "pandas",
    "numba",
    "stable-baselines3",
    "torch",
    "gymnasium",
)


def canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def runtime_versions() -> dict[str, str]:
    result = {"python": platform.python_version()}
    for name in RUNTIME_PACKAGES:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "not-installed"
    return result


def observation_contract(
    cfg, columns, *, feature_recipe: str, unit_overrides=None
) -> dict:
    """Pin representation and economic semantics without storing credentials."""
    overrides = dict(unit_overrides or {})
    if set(overrides) - set(columns):
        raise ValueError("unit overrides contain undeclared feature names")
    schema = []
    for column in columns:
        if column in NATIVE_UNITS:
            unit, role = NATIVE_UNITS[column]
        elif column in overrides:
            unit, role = overrides[column], "custom_signal"
        else:
            raise ValueError(
                f"custom feature {column!r} requires explicit unit metadata"
            )
        if not isinstance(unit, str) or not unit or len(unit) > 100:
            raise ValueError("feature units must be bounded nonempty strings")
        schema.append({"name": column, "unit": unit, "role": role})
    timeframes = list(cfg.features.timeframes)
    active = timeframes if cfg.features.attention.enabled else timeframes[:1]
    economics = cfg.env.model_dump(mode="json")
    # A state-file location is deployment plumbing, not economic semantics.
    economics.get("breaker", {}).pop("state_file", None)
    return {
        "version": "native-observation-contract.v1",
        "feature_recipe": feature_recipe,
        "feature_schema": schema,
        "timeframes": [
            {
                "name": tf.name,
                "bar_seconds": tf.bar_minutes * 60,
                "extra_outage_base_bars": tf.roll_fill_cap,
            }
            for tf in active
        ],
        "availability": {
            "source_timestamp": "bar_open",
            "settlement_delay_seconds": 2,
            "join": "backward_asof",
            "missingness": "complete-asof-fail-stale",
        },
        "history_rows": max(64, cfg.features.rolling_window // 10 + 2),
        "fusion": (
            "fixed_equal_with_base_execution_and_category_context"
            if len(active) > 1
            else "base_only"
        ),
        "normalization": {
            "method": cfg.features.standardization.method,
            "fit_role": "training_only",
            "dtype": "float32",
            "minimum_scale": 1e-6,
        },
        "account_features": [
            {
                "name": "log_equity_over_initial",
                "expression": "log(max(equity,1e-9)/initial_equity)",
            },
            {
                "name": "log_price_over_initial",
                "expression": "log(price/initial_price)",
            },
            {
                "name": "position_initial_units",
                "expression": "position/(initial_equity/initial_price)",
            },
        ],
        "action": {
            "shape": [1],
            "dtype": "float32",
            "semantic": "signed_target_leverage",
            "zero": "target_flat",
            "long_cap": cfg.env.leverage.max_leverage,
            "short_cap": cfg.env.leverage.max_short_leverage,
        },
        "transition": {
            "version": "next-valuation.v1",
            "decision": "completed_row_t",
            "execution_reference": "row_t_mid_close_proxy_if_unobserved",
            "returned_account": "marked_and_financed_at_row_t_plus_1",
            "reward": "net_equity_change_over_settled_interval",
            "intrabar_risk": "row_t_plus_1_ohlc_envelope_assumption",
            "financing": "compound_daily_rate_over_elapsed_market_seconds",
            "unresolved": "intrabar_order_and_publication_delay_overlap",
        },
        "initial_equity": cfg.training.initial_equity,
        "environment_semantics_sha256": hashlib.sha256(
            canonical_json(economics).encode()
        ).hexdigest(),
    }
