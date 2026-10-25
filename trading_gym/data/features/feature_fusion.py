"""Inspectable, unit-equivariant reference fusion for aligned timeframe features.

This is fixed-weight aggregation, not trained attention. Raw feature magnitudes
must not choose their own routing weights. Learned or fitted challengers belong
in explicit training-only representation experiments.
"""

from __future__ import annotations

import numpy as np

# These inputs have a physical or permission meaning in the execution path.
# Their authoritative value is the current BASE timeframe's value.
BASE_CONTEXT_COLUMNS = frozenset({"spread_ratio", "volatility", "sector", "can_short"})


def fuse_features(
    feature_vectors: np.ndarray, masks: np.ndarray, *, weights: np.ndarray | None = None
) -> np.ndarray:
    """Return fixed weighted features, renormalized over available timeframes.

    ``feature_vectors`` is [timeframe, sample, feature]; ``masks`` is a boolean
    [timeframe, sample] array where True means unavailable. Weights are declared
    once per timeframe, nonnegative and finite. All-masked/zero-weight rows fail.
    No fit occurs here, so changing evaluation tails cannot change the weights.
    """
    values = np.asarray(feature_vectors, dtype=np.float64)
    mask = np.asarray(masks)
    if values.ndim != 3 or any(size == 0 for size in values.shape):
        raise ValueError("feature_vectors must be a nonempty 3D tensor")
    if mask.dtype != np.bool_ or mask.shape != values.shape[:2]:
        raise ValueError("masks must be boolean with exact timeframe/sample shape")
    if weights is None:
        declared = np.ones(values.shape[0], dtype=np.float64)
    else:
        declared = np.asarray(weights, dtype=np.float64)
    if (
        declared.shape != (values.shape[0],)
        or not np.isfinite(declared).all()
        or (declared < 0).any()
    ):
        raise ValueError("weights must be finite nonnegative values, one per timeframe")
    active = (~mask) * declared[:, None]
    totals = active.sum(axis=0)
    if (totals <= 0).any() or not np.isfinite(totals).all():
        raise ValueError("each sample needs a positive-weight available timeframe")
    sanitized = np.where(mask[:, :, None], 0.0, values)
    if not np.isfinite(sanitized).all():
        raise ValueError("available features must be finite")
    with np.errstate(over="ignore", invalid="ignore"):
        fused = np.einsum("ts,tsf->sf", active / totals, sanitized).astype(np.float32)
    if not np.isfinite(fused).all():
        raise ValueError("fused features exceed the finite observation range")
    return fused


def preserve_base_context(fused: np.ndarray, base: np.ndarray, columns) -> np.ndarray:
    """Keep execution units and discrete permissions out of feature aggregation."""
    result, source = np.asarray(fused).copy(), np.asarray(base)
    if result.shape != source.shape or result.shape[-1] != len(columns):
        raise ValueError("base context schema/shape mismatch")
    for index, column in enumerate(columns):
        if column in BASE_CONTEXT_COLUMNS:
            result[..., index] = source[..., index]
    return result
