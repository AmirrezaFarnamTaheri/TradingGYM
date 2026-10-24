"""Offline observation challengers; no allocation, financial replay or promotion.

Inputs are already admitted/aligned by the closed-bar availability recipe. This
experiment keeps raw execution context separate from model representation. It
does not feed normalized spread values back into the native execution engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

import numpy as np
import pandas as pd

from trading_gym.features.feature_fusion import fuse_features
from trading_gym.pipelines.model_bundle import FrozenNormalizer, feature_data_digest
from trading_gym.pipelines.observation_contract import NATIVE_UNITS
from trading_gym.pipelines.research_protocol import decision_seconds

_CATEGORIES = frozenset({"sector", "can_short"})
_MODES = ("legacy_raw_attention", "fixed_weighted", "masked_concat")


def _mask_array(masks, shape):
    result = np.zeros(shape, dtype=bool) if masks is None else np.asarray(masks)
    if result.dtype != np.bool_ or result.shape != shape:
        raise ValueError("masks need exact boolean timeframe/sample shape")
    if result[0].any():
        raise ValueError("base timeframe/context cannot be missing")
    return result.copy()


def _legacy_weights(values, masks):
    safe = np.where(masks[:, :, None], 0.0, values)
    logits = np.einsum("sf,tsf->st", safe[0], safe, dtype=np.float64)
    logits[masks.T] = -np.inf
    logits -= np.max(logits, axis=1, keepdims=True)
    weights = np.exp(logits)
    weights /= weights.sum(axis=1, keepdims=True)
    if not np.isfinite(weights).all():
        raise ValueError("legacy routing produced nonfinite weights")
    return weights


@dataclass(frozen=True)
class RepresentationBatch:
    values: np.ndarray
    columns: tuple[str, ...]
    raw_execution_context: np.ndarray
    context_columns: tuple[str, ...]


@dataclass(frozen=True)
class FrozenRepresentation:
    timeframes: tuple[str, ...]
    feature_columns: tuple[str, ...]
    continuous: tuple[str, ...]
    units: tuple[str, ...]
    normalizers: tuple[FrozenNormalizer, ...]
    legacy_normalizer: FrozenNormalizer
    sector_levels: tuple[float, ...]
    training_hashes: tuple[str, ...]
    training_cutoff: str

    def __post_init__(self):
        for field in (
            "timeframes",
            "feature_columns",
            "continuous",
            "units",
            "normalizers",
            "sector_levels",
            "training_hashes",
        ):
            object.__setattr__(self, field, tuple(getattr(self, field)))
        if len(self.timeframes) != len(self.normalizers) or len(self.units) != len(
            self.feature_columns
        ):
            raise ValueError("representation specification shape mismatch")

    def to_dict(self):
        return {
            "schema": "representation-probe.v1",
            "timeframes": list(self.timeframes),
            "feature_columns": list(self.feature_columns),
            "continuous": list(self.continuous),
            "units": list(self.units),
            "sector_levels": list(self.sector_levels),
            "normalizers": [n.to_stats() for n in self.normalizers],
            "legacy_normalizer": self.legacy_normalizer.to_stats(),
            "training_hashes": list(self.training_hashes),
            "training_cutoff": self.training_cutoff,
            "availability": "requires_shared_closed_bar_admission",
            "authority": "offline_only",
        }

    def transform(
        self, frames: dict[str, pd.DataFrame], *, mode="masked_concat", masks=None
    ) -> RepresentationBatch:
        if mode not in _MODES or set(frames) - set(self.timeframes):
            raise ValueError("unknown representation or timeframe")
        base = frames.get(self.timeframes[0])
        if base is None:
            raise ValueError("base timeframe is required")
        decision_seconds(base)
        mask = _mask_array(masks, (len(self.timeframes), len(base)))
        raw, normalized = [], []
        for index, (name, normalizer) in enumerate(
            zip(self.timeframes, self.normalizers)
        ):
            frame = frames.get(name)
            if frame is None:
                mask[index] = True
                values = np.zeros((len(base), len(self.feature_columns)))
            else:
                if (
                    not frame.index.equals(base.index)
                    or tuple(frame.columns) != self.feature_columns
                ):
                    raise ValueError(
                        "timeframe feature identity/order/timestamps differ from the specification"
                    )
                values = frame.to_numpy(dtype=np.float64)
            clean = np.where(mask[index, :, None], 0.0, values)
            if not np.isfinite(clean).all():
                raise ValueError("available representation inputs must be finite")
            raw.append(clean)
            continuous = pd.DataFrame(
                clean, index=base.index, columns=self.feature_columns
            )[list(self.continuous)]
            # Missing values are zero in representation space, not normalized
            # fabricated raw values; the fixed mask columns preserve identity.
            continuous.loc[mask[index], :] = np.asarray(normalizer.center)
            normalized.append(
                np.where(mask[index, :, None], 0.0, normalizer.transform(continuous))
            )
        raw_array, normalized_array = np.stack(raw), np.stack(normalized)
        if mode == "masked_concat":
            output = normalized_array.transpose(1, 0, 2).reshape(len(base), -1)
            columns = [
                f"{tf}:{col}" for tf in self.timeframes for col in self.continuous
            ]
        elif mode == "fixed_weighted":
            output = fuse_features(normalized_array, mask)
            columns = [f"fixed:{col}" for col in self.continuous]
        else:
            weights = _legacy_weights(raw_array, mask)
            mixed = np.einsum("st,tsf->sf", weights, raw_array)
            mixed = pd.DataFrame(mixed, index=base.index, columns=self.feature_columns)
            output = self.legacy_normalizer.transform(mixed[list(self.continuous)])
            columns = [f"legacy:{col}" for col in self.continuous]
        parts = [output, mask.T.astype(np.float32)]
        columns.extend(f"missing:{tf}" for tf in self.timeframes)
        if "sector" in self.feature_columns:
            sectors = base["sector"].to_numpy()
            if not np.isin(sectors, self.sector_levels).all():
                raise ValueError(
                    "unseen sector category requires a new fitted specification"
                )
            parts.append(
                np.column_stack([sectors == level for level in self.sector_levels])
            )
            columns.extend(f"sector={level:g}" for level in self.sector_levels)
        if "can_short" in self.feature_columns:
            permission = base["can_short"].to_numpy()
            if not np.isin(permission, [0.0, 1.0]).all():
                raise ValueError("short permission must remain boolean")
            parts.append(permission[:, None])
            columns.append("can_short")
        context_columns = tuple(
            c for c in ("spread_ratio", "volatility") if c in self.feature_columns
        )
        context = base[list(context_columns)].to_numpy(dtype=np.float64, copy=True)
        # Unit experiments may convert ratio -> bps; execution context is
        # restored to its physical ratio and never receives model z-scores.
        units = dict(zip(self.feature_columns, self.units))
        if (
            "spread_ratio" in context_columns
            and units["spread_ratio"] == "basis_points"
        ):
            context[:, context_columns.index("spread_ratio")] /= 10000.0
        values = np.column_stack(parts).astype(np.float32)
        if not np.isfinite(values).all() or not np.isfinite(context).all():
            raise ValueError("representation exceeds finite output range")
        values.setflags(write=False)
        context.setflags(write=False)
        return RepresentationBatch(values, tuple(columns), context, context_columns)


def fit_representation(
    frames: dict[str, pd.DataFrame], *, masks=None, units=None, method="standard"
) -> FrozenRepresentation:
    if not frames or any(not isinstance(name, str) or not name for name in frames):
        raise ValueError("named training timeframes are required")
    names = tuple(frames)
    base = frames[names[0]]
    decision_seconds(base)
    columns = tuple(base.columns)
    continuous = tuple(c for c in columns if c not in _CATEGORIES)
    if not continuous or not set(columns).issubset(NATIVE_UNITS):
        raise ValueError(
            "probe needs known native feature semantics and at least one continuous feature"
        )
    declared_units = {c: NATIVE_UNITS[c][0] for c in columns}
    supplied = dict(units or {})
    if set(supplied) - set(columns):
        raise ValueError("unit metadata contains unknown features")
    declared_units.update(supplied)
    for column in columns:
        allowed = {NATIVE_UNITS[column][0]}
        if column == "spread_ratio":
            allowed.add("basis_points")
        if declared_units[column] not in allowed:
            raise ValueError(
                "unsupported unit conversion; declare its physical conversion first"
            )
    mask = _mask_array(masks, (len(names), len(base)))
    normalizers, hashes, raw = [], [], []
    for index, name in enumerate(names):
        frame = frames[name]
        if not frame.index.equals(base.index) or tuple(frame.columns) != columns:
            raise ValueError(
                "training timeframes must have identical ordered feature schemas and decisions"
            )
        available = frame.loc[~mask[index]]
        normalizers.append(FrozenNormalizer.fit(available[list(continuous)], method))
        hashes.append(feature_data_digest(available))
        raw.append(
            np.where(mask[index, :, None], 0.0, frame.to_numpy(dtype=np.float64))
        )
    raw_array = np.stack(raw)
    weights = _legacy_weights(raw_array, mask)
    legacy = pd.DataFrame(
        np.einsum("st,tsf->sf", weights, raw_array), index=base.index, columns=columns
    )
    legacy_normalizer = FrozenNormalizer.fit(legacy[list(continuous)], method)
    sectors = (
        tuple(float(v) for v in sorted(base["sector"].unique()))
        if "sector" in columns
        else ()
    )
    fitted = FrozenRepresentation(
        names,
        columns,
        continuous,
        tuple(declared_units[c] for c in columns),
        tuple(normalizers),
        legacy_normalizer,
        sectors,
        tuple(hashes),
        base.index[-1].isoformat(),
    )
    fitted.transform(
        frames, masks=mask
    )  # Validate permissions and context before publishing a fit.
    return fitted


def run_representation_probe(
    frames: dict[str, pd.DataFrame], *, train_stop: int, ridge: float = 1.0
) -> dict:
    """Compare one-step return prediction on later data; this is NOT a PnL test.

    All three declared candidates share training and evaluation rows. Choosing
    among their errors consumes selection evidence and needs a later final test.
    No new financial simulator or learned-attention authority is introduced.
    """
    base = next(iter(frames.values()))
    if "log_ret" not in base or not 4 <= train_stop <= len(base) - 3:
        raise ValueError(
            "probe needs log_ret and nonempty chronological train/test intervals"
        )
    if not np.isfinite(ridge) or ridge <= 0:
        raise ValueError("ridge must be finite and positive")
    fitted = fit_representation(
        {name: frame.iloc[:train_stop] for name, frame in frames.items()}
    )
    target = base["log_ret"].to_numpy(dtype=np.float64)
    outcomes = {}
    for mode in _MODES:
        batch = fitted.transform(frames, mode=mode)
        x = np.column_stack([np.ones(len(base)), batch.values.astype(np.float64)])
        train_x, train_y = x[: train_stop - 1], target[1:train_stop]
        regularizer = np.eye(x.shape[1]) * ridge
        regularizer[0, 0] = 0.0
        coefficients = np.linalg.solve(
            train_x.T @ train_x + regularizer, train_x.T @ train_y
        )
        prediction = x[train_stop:-1] @ coefficients
        error = float(np.mean((prediction - target[train_stop + 1 :]) ** 2))
        if not np.isfinite(error):
            raise ValueError("probe produced a nonfinite score")
        outcomes[mode] = {
            "mean_squared_next_log_return_error": error,
            "dimensions": len(batch.columns),
            "evaluation_predictions": prediction.tolist(),
        }
    specification = fitted.to_dict()
    return {
        "schema": "representation-comparison.v1",
        "specification": specification,
        "specification_sha256": hashlib.sha256(
            json.dumps(specification, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
        "last_training_label_available_at": base.index[train_stop - 1].isoformat(),
        "first_evaluation_decision": base.index[train_stop].isoformat(),
        "last_evaluation_label_available_at": base.index[-1].isoformat(),
        "candidate_count": len(_MODES),
        "evidence_role": "selection_probe",
        "results": outcomes,
        "trading_performance_measured": False,
        "promotion_authority_granted": False,
    }


def local_account_vector(
    *,
    equity: float,
    previous_equity: float,
    price: float,
    previous_price: float,
    position: float,
) -> tuple[np.ndarray, str]:
    """A finite account-feature challenger, not a proof of stationarity.

    Insolvent states retain their actual simple equity return and a terminal
    indicator; undefined leverage is represented as zero only with that flag.
    """
    if (
        not np.isfinite(
            [equity, previous_equity, price, previous_price, position]
        ).all()
        or min(previous_equity, price, previous_price) <= 0
    ):
        raise ValueError(
            "account feature inputs must be finite with positive reference equity/prices"
        )
    terminal = equity <= 0
    values = np.asarray(
        [
            (equity - previous_equity) / previous_equity,
            np.log(price / previous_price),
            position * price / equity if not terminal else 0.0,
            float(terminal),
        ],
        dtype=np.float32,
    )
    if not np.isfinite(values).all():
        raise ValueError("account representation exceeds finite range")
    values.setflags(write=False)
    return values, "insolvent" if terminal else "active"
