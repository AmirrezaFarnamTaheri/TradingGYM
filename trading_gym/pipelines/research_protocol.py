"""Temporal role manifests and bounded selection protocols for native replay.

Identity sealing reuses the existing experiment plane. These checks establish
admissible chronology, not iid market observations or a profitable strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

import numpy as np
import pandas as pd

from trading_gym.experiments.reproducibility import (
    ArtifactReference, RunManifestCreate, SeedBundle, build_manifest,
)
from trading_gym.experiments.sealed_spec import seal_experiment
from trading_gym.pipelines.model_bundle import feature_data_digest, market_data_digest


def slice_inputs(inputs: dict, interval: slice) -> dict:
    rows = len(inputs["features"])
    result = {}
    for key, value in inputs.items():
        if isinstance(value, pd.DataFrame):
            if len(value) != rows:
                raise ValueError("row-aligned DataFrame metadata length mismatch")
            result[key] = value.iloc[interval].copy()
        elif isinstance(value, (np.ndarray, pd.Index)) and len(value) == rows:
            result[key] = value[interval].copy()
        else:
            result[key] = value
    return result


def decision_seconds(frame: pd.DataFrame) -> np.ndarray:
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None or index.hasnans:
        raise ValueError("research rows require timezone-aware decision timestamps")
    if not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError("research timestamps must be unique and increasing")
    return index.asi8.astype(np.float64) / 1e9


def _label_availability(inputs: dict) -> tuple[np.ndarray, str]:
    times = decision_seconds(inputs["features"])
    labels = inputs.get("label_available_at")
    if labels is None:
        return times, "native_transitions_contained_within_training_slice"
    if isinstance(labels, pd.DatetimeIndex):
        if labels.tz is None or labels.hasnans:
            raise ValueError("label availability must be timezone-aware and finite")
        values = labels.asi8.astype(np.float64) / 1e9
    else:
        values = np.asarray(labels, dtype=np.float64)
    if values.shape != times.shape or not np.isfinite(values).all():
        raise ValueError("label availability must be a finite row-aligned vector")
    return values, "explicit_label_available_at"


def fold_manifest(inputs: dict, train: slice, evaluate: slice, *, fold: int) -> tuple[slice, dict]:
    """Purge only actual unmatured labels; keep RL market replay contiguous.

    If an early training row has a late label, conservatively trim from that
    row onward instead of joining disjoint market periods into one episode.
    Causal feature warm-up before a split remains permitted.
    """
    features = inputs["features"]
    times = decision_seconds(features)
    labels, semantics = _label_availability(inputs)
    if train.start != 0 or not (2 <= train.stop <= evaluate.start < evaluate.stop <= len(features)):
        raise ValueError("fold partitions must be ordered, nonempty and anchored")
    if evaluate.stop - evaluate.start < 2:
        raise ValueError("evaluation needs at least two rows")
    immature = np.flatnonzero(labels[:train.stop] >= times[evaluate.start])
    stop = min(train.stop, int(immature[0])) if len(immature) else train.stop
    if stop < 2:
        raise ValueError("label maturity purge leaves insufficient training history")
    fitted, tested = slice_inputs(inputs, slice(0, stop)), slice_inputs(inputs, evaluate)
    spans = [(0, stop, "training"), (stop, evaluate.start, "purged_or_gap"),
             (evaluate.start, evaluate.stop, "selection_input"),
             (evaluate.stop, len(features), "future_unused")]
    payload = {
        "schema": "native-forward-fold.v1", "fold": fold,
        "source_market_sha256": market_data_digest(inputs),
        "training_features_sha256": feature_data_digest(fitted["features"]),
        "training_market_sha256": market_data_digest(fitted),
        "evaluation_features_sha256": feature_data_digest(tested["features"]),
        "evaluation_market_sha256": market_data_digest(tested),
        "row_roles": [{"start": start, "stop": end, "role": role} for start, end, role in spans if start < end],
        "training_feature_cutoff": features.index[stop - 1].isoformat(),
        "training_label_available_by": float(np.max(labels[:stop])),
        "evaluation_first": features.index[evaluate.start].isoformat(),
        "evaluation_last": features.index[evaluate.stop - 1].isoformat(),
        "label_maturity_semantics": semantics,
        "position_boundary": "independent_flat_reset_no_position_carry",
        "evidence_role": "selection", "scored_rows": [],
    }
    return slice(0, stop), payload


@dataclass(frozen=True)
class ForwardProtocol:
    specification_json: str
    seal_id: str
    development_stop: int

    @property
    def specification(self) -> dict:
        return json.loads(self.specification_json)

    def run_manifest(self, *, experiment_id: str, code_sha256: str, seed: int):
        spec = self.specification
        request = RunManifestCreate(
            experiment_id=experiment_id, code_sha256=code_sha256,
            configuration={"protocol": spec, "seal_id": self.seal_id},
            artifacts=[ArtifactReference(name="admitted-market", kind="dataset",
                                         sha256=spec["source_market_sha256"])],
            seeds=SeedBundle(python=seed, numpy=seed, torch=seed, environment=seed),
            dependency_names=["numpy", "pandas", "stable-baselines3", "gymnasium", "torch"],
            notes="Forward selection protocol; final segment reserved, not automatically qualified.",
        )
        return build_manifest(request)


def reserve_final_segment(inputs: dict, *, candidate_budget: int, final_fraction: float = .2) -> tuple[ForwardProtocol, dict]:
    if isinstance(candidate_budget, bool) or not isinstance(candidate_budget, int) or candidate_budget < 1:
        raise ValueError("candidate budget must be a positive integer")
    if not np.isfinite(final_fraction) or not 0 < final_fraction < 1:
        raise ValueError("final_fraction must lie strictly between zero and one")
    features = inputs["features"]
    decision_seconds(features)
    stop = int(len(features) * (1 - final_fraction))
    if stop < 4 or len(features) - stop < 2:
        raise ValueError("development and reserved final segments are too short")
    development, final = slice_inputs(inputs, slice(0, stop)), slice_inputs(inputs, slice(stop, len(features)))
    spec = {
        "schema": "native-forward-protocol.v1", "candidate_budget": candidate_budget,
        "source_market_sha256": market_data_digest(inputs),
        "development_market_sha256": market_data_digest(development),
        "reserved_final_market_sha256": market_data_digest(final),
        "row_roles": [{"start": 0, "stop": stop, "role": "development"},
                      {"start": stop, "stop": len(features), "role": "reserved_final"}],
        "development_last": features.index[stop - 1].isoformat(),
        "final_first": features.index[stop].isoformat(),
        "final_last": features.index[-1].isoformat(),
        "final_outcomes_consumed": False,
        "position_boundary": "independent_flat_reset_no_position_carry",
        "claim": "temporal_admissibility_not_statistical_independence",
    }
    sealed = seal_experiment(name="native-forward-selection", spec=spec,
                             tape_sha256=spec["source_market_sha256"])
    encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return ForwardProtocol(encoded, sealed.seal_id, stop), development


def assert_causal_prefix(transform: Callable[[pd.DataFrame], pd.DataFrame],
                         original: pd.DataFrame, perturbed: pd.DataFrame,
                         *, through: pd.Timestamp, atol: float = 0.) -> None:
    """Negative-control harness: a future-only perturbation cannot alter a prefix."""
    if not original.index.equals(perturbed.index) or not original.columns.equals(perturbed.columns):
        raise ValueError("negative controls require identical source schema and times")
    pd.testing.assert_frame_equal(original.loc[:through], perturbed.loc[:through], check_exact=True)
    first, changed = transform(original), transform(perturbed)
    if not isinstance(first, pd.DataFrame) or not isinstance(changed, pd.DataFrame):
        raise ValueError("causal transforms must return timestamped DataFrames")
    pd.testing.assert_frame_equal(first.loc[:through], changed.loc[:through],
                                  check_exact=(atol == 0), rtol=0., atol=atol)


def audit_evidence_roles(uses: list[dict]) -> dict:
    """Audit serialized uses for one procedure; persist through existing manifests.

    New procedures may train on matured prior final outcomes, but must name a
    later untouched final partition. This local log cannot detect unrecorded
    external human/model inspection of the same data.
    """
    states, blockers, globally_used = {}, [], set()
    for index, use in enumerate(uses):
        procedure, partition, role = use["procedure_id"], use["partition_sha256"], use["role"]
        if role not in {"training", "selection", "calibration", "final"}:
            raise ValueError("unknown evidence role")
        state = states.setdefault(procedure, {"used": set(), "final": None})
        if not np.isfinite([use["available_by"], use["consumed_at"]]).all() or use["available_by"] > use["consumed_at"]:
            blockers.append(f"unmatured_evidence:{index}")
        if state["final"] is not None:
            blockers.append(f"procedure_changed_after_final:{index}")
        if role == "final":
            if partition in globally_used:
                blockers.append(f"final_partition_already_used:{index}")
            state["final"] = partition
        state["used"].add(partition)
        globally_used.add(partition)
    return {"valid": not blockers, "blockers": blockers,
            "authority_granted": False, "statistical_independence_established": False}
