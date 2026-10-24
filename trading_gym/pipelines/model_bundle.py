"""Frozen, JSON-only observation contracts bound by the admitted SB3 ZIP hash.

Inspection never loads a policy or unpickles SB3 metadata. The bundle is saved
as an ordinary JSON-serializable model attribute before atomic publication.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
import zipfile

import numpy as np
import pandas as pd

from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)
from trading_gym.pipelines.observation_contract import (
    observation_contract,
    runtime_versions,
)

BUNDLE_ATTRIBUTE = "trading_gym_bundle"
BUNDLE_SCHEMA = "trading-gym.observation.v2"
FEATURE_RECIPE = "closed-bars-bounded-rsi-fixed-fusion.v3"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_MODEL_BYTES = 8 * 1024 * 1024 * 1024
_METHODS = {"robust", "standard", "zscore", "provided"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _checked_frame(frame: pd.DataFrame) -> np.ndarray:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("features must be a nonempty DataFrame")
    if not all(isinstance(name, str) and name for name in frame.columns):
        raise ValueError("feature columns must have nonempty string names")
    if not frame.columns.is_unique:
        raise ValueError("feature columns must be unique")
    if any(not pd.api.types.is_numeric_dtype(dtype) for dtype in frame.dtypes):
        raise ValueError("all features must be numeric")
    if any(pd.api.types.is_complex_dtype(dtype) for dtype in frame.dtypes):
        raise ValueError("complex feature values are unsupported")
    values = frame.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("features must be finite")
    return values


@dataclass(frozen=True)
class FrozenNormalizer:
    method: str
    columns: tuple[str, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "center", tuple(float(v) for v in self.center))
        object.__setattr__(self, "scale", tuple(float(v) for v in self.scale))
        if self.method not in _METHODS:
            raise ValueError("unsupported normalization method")
        if not self.columns or len(set(self.columns)) != len(self.columns):
            raise ValueError("normalizer requires unique ordered feature names")
        if not all(isinstance(name, str) and name for name in self.columns):
            raise ValueError("invalid normalizer feature name")
        if len(self.center) != len(self.columns) or len(self.scale) != len(
            self.columns
        ):
            raise ValueError("normalizer parameter shape mismatch")
        center = np.asarray(self.center, dtype=np.float64)
        scale = np.asarray(self.scale, dtype=np.float64)
        if (
            not np.isfinite(center).all()
            or not np.isfinite(scale).all()
            or (scale <= 0).any()
        ):
            raise ValueError(
                "normalizer parameters must be finite with positive scales"
            )
        if (
            not np.isfinite(center.astype(np.float32)).all()
            or not np.isfinite(scale.astype(np.float32)).all()
        ):
            raise ValueError(
                "normalizer parameters exceed the float32 observation range"
            )

    @classmethod
    def fit(cls, frame: pd.DataFrame, method: str = "robust") -> "FrozenNormalizer":
        _checked_frame(frame)
        if len(frame) < 2:
            raise ValueError(
                "fitting preprocessing requires at least two training rows"
            )
        method = method.lower()
        if method == "robust":
            center = frame.median()
            scale = frame.quantile(0.75) - frame.quantile(0.25)
        elif method in {"standard", "zscore"}:
            center, scale = frame.mean(), frame.std(ddof=1)
        else:
            raise ValueError("fit supports robust, standard or zscore normalization")
        # Match the existing environment's float32 minimum-scale contract.
        scale = scale.mask(scale == 0, 1.0).clip(lower=1e-6)
        return cls(
            method,
            tuple(frame.columns),
            tuple(map(float, center)),
            tuple(map(float, scale)),
        )

    @classmethod
    def from_stats(cls, columns, stats: Mapping[str, Any]) -> "FrozenNormalizer":
        if not isinstance(stats, Mapping):
            raise ValueError("fitted normalization statistics are required")
        columns = tuple(columns)
        centers, scales = stats.get("center"), stats.get("scale")
        if not isinstance(centers, Mapping) or not isinstance(scales, Mapping):
            raise ValueError("statistics require explicit center and scale mappings")
        if set(centers) != set(columns) or set(scales) != set(columns):
            raise ValueError("statistics do not match the complete feature schema")
        return cls(
            str(stats.get("method", "provided")),
            columns,
            tuple(float(centers[c]) for c in columns),
            tuple(float(scales[c]) for c in columns),
        )

    def validate_features(self, frame: pd.DataFrame) -> None:
        _checked_frame(frame)
        if tuple(frame.columns) != self.columns:
            raise ValueError("feature identity/order differs from the trained bundle")
        self.transform(frame)

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        values = _checked_frame(frame)
        if tuple(frame.columns) != self.columns:
            raise ValueError("feature identity/order differs from the trained bundle")
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            transformed = (
                values.astype(np.float32) - np.asarray(self.center, dtype=np.float32)
            ) / np.maximum(np.asarray(self.scale, dtype=np.float32), np.float32(1e-6))
        if not np.isfinite(transformed).all():
            raise ValueError("normalization produces a nonfinite observation")
        return transformed

    def to_stats(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "center": dict(zip(self.columns, self.center)),
            "scale": dict(zip(self.columns, self.scale)),
        }


def feature_config_digest(cfg) -> str:
    payload = cfg.features.model_dump(mode="json")
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def feature_data_digest(frame: pd.DataFrame) -> str:
    values = _checked_frame(frame)
    digest = hashlib.sha256(_canonical(list(frame.columns)).encode())
    digest.update(
        pd.util.hash_pandas_object(frame.index, index=False)
        .to_numpy(dtype="<u8")
        .tobytes()
    )
    digest.update(values.astype("<f8", copy=False).tobytes())
    return digest.hexdigest()


def market_data_digest(inputs: Mapping[str, Any]) -> str:
    features = inputs["features"]
    digest = hashlib.sha256(feature_data_digest(features).encode())
    for name in ("prices_mid", "prices_high", "prices_low"):
        values = np.asarray(inputs[name], dtype=np.float64)
        if (
            values.shape != (len(features),)
            or not np.isfinite(values).all()
            or (values <= 0).any()
        ):
            raise ValueError(
                "training market data must be positive finite aligned vectors"
            )
        digest.update(name.encode())
        digest.update(values.astype("<f8", copy=False).tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class ModelBundle:
    normalizer: FrozenNormalizer
    symbol: str
    feature_config_sha256: str
    training_data_sha256: str
    training_rows: int
    training_first: str
    training_last: str
    observation_contract_json: str
    runtime_versions_json: str
    training_market_sha256: str | None = None
    source_revision: str | None = None
    feature_recipe: str = FEATURE_RECIPE
    schema: str = BUNDLE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != BUNDLE_SCHEMA:
            raise ValueError(
                "unsupported observation bundle schema; migrate or retrain"
            )
        if self.feature_recipe != FEATURE_RECIPE:
            raise ValueError(
                "feature recipe changed; retrain under the current availability contract"
            )
        for digest in (self.feature_config_sha256, self.training_data_sha256):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("bundle identity must be a SHA-256 digest")
        if (
            isinstance(self.training_rows, bool)
            or not isinstance(self.training_rows, int)
            or self.training_rows < 2
        ):
            raise ValueError("invalid bundle training row count")
        first, last = pd.Timestamp(self.training_first), pd.Timestamp(
            self.training_last
        )
        if (
            first.tzinfo is None
            or last.tzinfo is None
            or pd.isna(first)
            or pd.isna(last)
            or first >= last
        ):
            raise ValueError(
                "bundle training interval must contain ordered timezone-aware times"
            )
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("bundle symbol is required")
        contract, runtime = json.loads(self.observation_contract_json), json.loads(
            self.runtime_versions_json
        )
        if (
            not isinstance(contract, dict)
            or contract.get("version") != "native-observation-contract.v1"
        ):
            raise ValueError("invalid observation contract")
        if [entry.get("name") for entry in contract.get("feature_schema", [])] != list(
            self.normalizer.columns
        ):
            raise ValueError(
                "observation contract feature schema differs from fitted statistics"
            )
        if contract.get("feature_recipe") != self.feature_recipe:
            raise ValueError("observation contract recipe differs from bundle")
        if not isinstance(runtime, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in runtime.items()
        ):
            raise ValueError("runtime manifest must map strings to version strings")
        _canonical(contract)
        _canonical(runtime)
        if self.training_market_sha256 is not None and (
            len(self.training_market_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.training_market_sha256)
        ):
            raise ValueError("invalid training market digest")
        if self.source_revision is not None and (
            not isinstance(self.source_revision, str)
            or len(self.source_revision) != 40
            or any(c not in "0123456789abcdef" for c in self.source_revision)
        ):
            raise ValueError(
                "source_revision must be a full lowercase Git revision when provided"
            )

    @property
    def training_stats(self) -> dict[str, Any]:
        return self.normalizer.to_stats()

    @property
    def contract(self) -> dict:
        return json.loads(self.observation_contract_json)

    def validate_config(self, cfg) -> None:
        if (
            cfg.env.symbol != self.symbol
            or feature_config_digest(cfg) != self.feature_config_sha256
        ):
            raise ValueError(
                "configuration differs from the admitted model observation contract"
            )
        units = {
            entry["name"]: entry["unit"] for entry in self.contract["feature_schema"]
        }
        expected = observation_contract(
            cfg,
            self.normalizer.columns,
            feature_recipe=self.feature_recipe,
            unit_overrides=units,
        )
        if _canonical(expected) != self.observation_contract_json:
            raise ValueError(
                "action, accounting or observation semantics differ from the trained model"
            )

    def validate_runtime(
        self, *, packages=("stable-baselines3", "gymnasium", "torch")
    ) -> None:
        recorded, current = json.loads(self.runtime_versions_json), runtime_versions()
        changed = [
            name
            for name in packages
            if recorded.get(name) in {None, "not-installed"}
            or recorded.get(name) != current.get(name)
        ]
        if changed:
            raise ValueError(
                "model runtime needs explicit compatibility qualification: "
                + ", ".join(changed)
            )

    def validate_features(self, frame: pd.DataFrame) -> None:
        self.normalizer.validate_features(frame)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "feature_recipe": self.feature_recipe,
            "normalizer": self.training_stats,
            "columns": list(self.normalizer.columns),
            "symbol": self.symbol,
            "feature_config_sha256": self.feature_config_sha256,
            "training_data_sha256": self.training_data_sha256,
            "training_rows": self.training_rows,
            "training_first": self.training_first,
            "training_last": self.training_last,
            "observation_contract": self.contract,
            "runtime_versions": json.loads(self.runtime_versions_json),
            "training_market_sha256": self.training_market_sha256,
            "source_revision": self.source_revision,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelBundle":
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "feature_recipe",
            "normalizer",
            "columns",
            "symbol",
            "feature_config_sha256",
            "training_data_sha256",
            "training_rows",
            "training_first",
            "training_last",
            "observation_contract",
            "runtime_versions",
            "training_market_sha256",
            "source_revision",
        }:
            raise ValueError(
                "invalid or legacy observation bundle; reconstruct from training provenance or retrain"
            )
        _canonical(payload)  # Reject nonfinite JSON numbers before interpretation.
        if not isinstance(payload["columns"], list):
            raise ValueError("bundle columns must be an ordered JSON list")
        normalizer = FrozenNormalizer.from_stats(
            payload["columns"], payload["normalizer"]
        )
        return cls(
            normalizer=normalizer,
            observation_contract_json=_canonical(payload["observation_contract"]),
            runtime_versions_json=_canonical(payload["runtime_versions"]),
            **{
                key: value
                for key, value in payload.items()
                if key
                not in {
                    "normalizer",
                    "columns",
                    "observation_contract",
                    "runtime_versions",
                }
            },
        )


def fit_model_bundle(
    cfg,
    frame: pd.DataFrame,
    *,
    inputs=None,
    unit_overrides=None,
    source_revision: str | None = None,
) -> ModelBundle:
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("training features require timezone-aware decision timestamps")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("training feature timestamps must be unique and ordered")
    if inputs is not None and feature_data_digest(
        inputs["features"]
    ) != feature_data_digest(frame):
        raise ValueError("market provenance does not match the fitted feature rows")
    return ModelBundle(
        normalizer=FrozenNormalizer.fit(frame, cfg.features.standardization.method),
        symbol=cfg.env.symbol,
        feature_config_sha256=feature_config_digest(cfg),
        training_data_sha256=feature_data_digest(frame),
        training_rows=len(frame),
        training_first=frame.index[0].isoformat(),
        training_last=frame.index[-1].isoformat(),
        observation_contract_json=_canonical(
            observation_contract(
                cfg,
                frame.columns,
                feature_recipe=FEATURE_RECIPE,
                unit_overrides=unit_overrides,
            )
        ),
        runtime_versions_json=_canonical(runtime_versions()),
        training_market_sha256=(
            market_data_digest(inputs) if inputs is not None else None
        ),
        source_revision=source_revision or os.environ.get("TRADINGGYM_SOURCE_REVISION"),
    )


def attach_model_bundle(model, bundle: ModelBundle) -> None:
    setattr(model, BUNDLE_ATTRIBUTE, bundle.to_dict())


def bundle_for_model(model) -> ModelBundle:
    payload = getattr(model, BUNDLE_ATTRIBUTE, None)
    if payload is None:
        raise ValueError(
            "model lacks frozen preprocessing; recover recorded training provenance or retrain"
        )
    return ModelBundle.from_dict(payload)


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate model metadata key")
        result[key] = value
    return result


def read_model_bundle(
    model_path: str | Path, *, expected_model_sha256: str
) -> ModelBundle:
    """Inspect the hash-admitted SB3 ZIP using bounded JSON only; never unpickle."""
    path = validate_regular_file(model_path, label="model", max_bytes=MAX_MODEL_BYTES)
    with verified_regular_file_snapshot(
        path,
        expected_sha256=expected_model_sha256,
        label="model",
        max_bytes=MAX_MODEL_BYTES,
    ) as snapshot:
        with zipfile.ZipFile(snapshot) as archive:
            entries = [item for item in archive.infolist() if item.filename == "data"]
            if len(entries) != 1 or entries[0].file_size > MAX_METADATA_BYTES:
                raise ValueError("model requires one bounded SB3 JSON metadata member")
            with archive.open(entries[0]) as member:
                raw = member.read(MAX_METADATA_BYTES + 1)
            if len(raw) > MAX_METADATA_BYTES:
                raise ValueError("model metadata exceeds size limit")
    data = json.loads(
        raw,
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError("nonfinite JSON metadata")
        ),
    )
    if not isinstance(data, dict) or BUNDLE_ATTRIBUTE not in data:
        raise ValueError(
            "model lacks frozen preprocessing; recover recorded training provenance or retrain"
        )
    return ModelBundle.from_dict(data[BUNDLE_ATTRIBUTE])


def inspect_model_bundle(model_path: str | Path, *, expected_model_sha256: str) -> dict:
    """Return bounded JSON provenance without deserializing executable policy data."""
    bundle = read_model_bundle(model_path, expected_model_sha256=expected_model_sha256)
    payload = bundle.to_dict()
    return {
        "artifact_sha256": expected_model_sha256,
        "bundle_sha256": hashlib.sha256(_canonical(payload).encode()).hexdigest(),
        "bundle": payload,
        "qualification": {
            "preprocessing": "frozen",
            "source_revision": (
                "recorded_not_independently_verified"
                if bundle.source_revision
                else "unrecorded"
            ),
            "training_market": (
                "hashed" if bundle.training_market_sha256 else "unrecorded"
            ),
            "evaluation": "requires_separate_selection_and_final_evidence",
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect hash-admitted model JSON without unpickling"
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-sha256", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            inspect_model_bundle(args.model, expected_model_sha256=args.model_sha256),
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
