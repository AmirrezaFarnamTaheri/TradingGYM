"""Stateless linear-student export and measured CPU parity experiment.

This bounded deployment path reuses the native ModelBundle normalization and
observation contract. Teacher agreement, ONNX export, and numeric parity are
engineering measurements; none establish profitable policy behavior or funded
execution permission. Recurrent policies and accelerator providers need their
own state, numerical and latency qualification and are deliberately rejected.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import time
from typing import Any, Sequence

import numpy as np
import pandas as pd

from trading_gym.core.security import verified_regular_file_snapshot
from trading_gym.agent.intelligence.decision_journal import canonical_digest
from trading_gym.agent.intelligence.types import np_finite
from trading_gym.rl.pipelines.model_bundle import ModelBundle, feature_data_digest

_SHA = re.compile(r"^[0-9a-f]{64}$")
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def _versions() -> dict[str, str]:
    return {
        name: importlib.metadata.version(name)
        for name in ("numpy", "onnx", "onnxruntime")
    }


def _numeric(values: Any, *, ndim: int) -> np.ndarray:
    raw = np.asarray(values)
    if raw.ndim != ndim or raw.dtype.kind not in "fiu" or not np.isfinite(raw).all():
        raise ValueError("expected a finite real numeric array with the declared shape")
    with np.errstate(over="ignore"):
        result = raw.astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("observation exceeds the float32 contract")
    return result


def prepare_observations(
    bundle: ModelBundle, features: pd.DataFrame, account_features: np.ndarray
) -> np.ndarray:
    """Apply the existing frozen normalizer, then its declared account tail."""
    if (
        not isinstance(features.index, pd.DatetimeIndex)
        or features.index.tz is None
        or not features.index.is_unique
        or not features.index.is_monotonic_increasing
    ):
        raise ValueError("corpus needs unique ordered timezone-aware decision times")
    accounts = _numeric(account_features, ndim=2)
    expected_tail = [
        "log_equity_over_initial",
        "log_price_over_initial",
        "position_initial_units",
    ]
    if [item["name"] for item in bundle.contract["account_features"]] != expected_tail:
        raise ValueError("account state contract requires separate qualification")
    if accounts.shape != (len(features), len(expected_tail)):
        raise ValueError("account observation tail is missing or misaligned")
    return np.concatenate((bundle.normalizer.transform(features), accounts), axis=1)


def _bounds(bundle: ModelBundle) -> tuple[float, float]:
    action = bundle.contract["action"]
    if (
        action["semantic"] != "signed_target_leverage"
        or action["zero"] != "target_flat"
        or action["shape"] != [1]
        or action["dtype"] != "float32"
    ):
        raise ValueError("unsupported deployment action contract")
    low, high = -float(action["short_cap"]), float(action["long_cap"])
    if not np_finite(low) or not np_finite(high) or not low <= 0 <= high or low >= high:
        raise ValueError("invalid target-leverage bounds")
    return low, high


@dataclass(frozen=True)
class LinearStudent:
    weights: tuple[float, ...]
    bias: float
    bundle_json: str
    teacher_id: str
    teacher_artifact_sha256: str
    training_corpus_sha256: str
    training_first: str
    training_last: str
    training_cutoff: float
    runtime_versions: tuple[tuple[str, str], ...]
    state_contract: str = "stateless"

    def __post_init__(self) -> None:
        bundle = self.bundle
        if (
            self.state_contract != "stateless"
            or not self.teacher_id
            or not _SHA.fullmatch(self.teacher_artifact_sha256)
            or not _SHA.fullmatch(self.training_corpus_sha256)
            or len(self.weights) != len(bundle.normalizer.columns) + 3
            or len(self.weights) > 4096
            or not all(np_finite(x) for x in (*self.weights, self.bias))
            or not np_finite(self.training_cutoff)
            or set(dict(self.runtime_versions)) != {"numpy", "onnx", "onnxruntime"}
        ):
            raise ValueError("invalid stateless student provenance or parameters")
        first, last = pd.Timestamp(self.training_first), pd.Timestamp(
            self.training_last
        )
        if (
            first.tz is None
            or last.tz is None
            or first >= last
            or first <= pd.Timestamp(bundle.training_last)
            or last.timestamp() > self.training_cutoff
        ):
            raise ValueError(
                "student corpus overlaps teacher training or exceeds its cutoff"
            )
        _bounds(bundle)

    @property
    def bundle(self) -> ModelBundle:
        return ModelBundle.from_dict(json.loads(self.bundle_json))

    @property
    def student_id(self) -> str:
        return canonical_digest(asdict(self))

    @property
    def source_id(self) -> str:
        return "student/" + self.student_id

    def actions(self, observations: np.ndarray) -> np.ndarray:
        values = _numeric(observations, ndim=2)
        if values.shape[1] != len(self.weights):
            raise ValueError("observation dimension differs from the frozen bundle")
        low, high = _bounds(self.bundle)
        action = values @ np.asarray(self.weights, dtype=np.float32).reshape(
            -1, 1
        ) + np.float32(self.bias)
        if not np.isfinite(action).all():
            raise ValueError("student target overflow")
        return np.clip(action, low, high).astype(np.float32)

    def predict(
        self, observation, deterministic=True, *, state=None, episode_start=None
    ):
        if deterministic is not True or state is not None or episode_start is not None:
            raise ValueError("only deterministic stateless deployment is qualified")
        array = np.asarray(observation)
        single = array.ndim == 1
        result = self.actions(array.reshape(1, -1) if single else array)
        return result[0] if single else result, None


def _teacher_rows(features, targets, available_at, *, cutoff, bounds):
    targets = _numeric(targets, ndim=1)
    times = np.asarray(available_at)
    if (
        times.dtype.kind not in "fiu"
        or times.shape != (len(features),)
        or not np.isfinite(times).all()
        or targets.shape != (len(features),)
        or not np_finite(cutoff)
    ):
        raise ValueError("teacher output availability must match every corpus row")
    decision_times = features.index.asi8 / 1e9
    if (
        np.any(times < decision_times)
        or np.any(times > cutoff)
        or np.any(targets < bounds[0])
        or np.any(targets > bounds[1])
    ):
        raise ValueError(
            "teacher outputs are unavailable or outside the action contract"
        )
    return targets, times.astype(np.float64)


def fit_linear_student(
    *,
    bundle: ModelBundle,
    features: pd.DataFrame,
    account_features: np.ndarray,
    teacher_targets: np.ndarray,
    teacher_available_at: Sequence[float],
    teacher_id: str,
    teacher_artifact_sha256: str,
    training_cutoff: float,
    ridge: float = 1e-6,
) -> LinearStudent:
    """Fit only the declared student-training corpus; no economic qualification."""
    observations = prepare_observations(bundle, features, account_features)
    if not np_finite(ridge) or ridge < 0 or len(features) < observations.shape[1] + 2:
        raise ValueError(
            "need a finite nonnegative ridge and enough training observations"
        )
    targets, times = _teacher_rows(
        features,
        teacher_targets,
        teacher_available_at,
        cutoff=training_cutoff,
        bounds=_bounds(bundle),
    )
    design = np.column_stack(
        (observations.astype(np.float64), np.ones(len(observations)))
    )
    penalty = np.sqrt(ridge) * np.eye(design.shape[1])
    penalty[-1, -1] = 0  # do not regularize the intercept
    coefficients = np.linalg.lstsq(
        np.vstack((design, penalty)),
        np.concatenate((targets.astype(np.float64), np.zeros(design.shape[1]))),
        rcond=None,
    )[0]
    coefficients = _numeric(coefficients, ndim=1)
    corpus = canonical_digest(
        {
            "raw_features": feature_data_digest(features),
            "account_features": np.asarray(account_features).tolist(),
            "teacher_targets": targets.tolist(),
            "teacher_available_at": times.tolist(),
            "ridge": ridge,
            "teacher_artifact_sha256": teacher_artifact_sha256,
        }
    )
    return LinearStudent(
        tuple(float(x) for x in coefficients[:-1]),
        float(coefficients[-1]),
        json.dumps(
            bundle.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
        teacher_id,
        teacher_artifact_sha256,
        corpus,
        features.index[0].isoformat(),
        features.index[-1].isoformat(),
        training_cutoff,
        tuple(sorted(_versions().items())),
    )


def _onnx_model(student: LinearStudent):
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    low, high = _bounds(student.bundle)
    graph = helper.make_graph(
        [
            helper.make_node("MatMul", ["observations", "weights"], ["linear"]),
            helper.make_node("Add", ["linear", "bias"], ["raw_target"]),
            helper.make_node(
                "Clip", ["raw_target", "minimum", "maximum"], ["target_leverage"]
            ),
        ],
        "tradinggym_stateless_linear_student",
        [
            helper.make_tensor_value_info(
                "observations", TensorProto.FLOAT, [None, len(student.weights)]
            )
        ],
        [
            helper.make_tensor_value_info(
                "target_leverage", TensorProto.FLOAT, [None, 1]
            )
        ],
        initializer=[
            numpy_helper.from_array(array, name)
            for name, array in (
                (
                    "weights",
                    np.asarray(student.weights, dtype=np.float32).reshape(-1, 1),
                ),
                ("bias", np.asarray([student.bias], dtype=np.float32)),
                ("minimum", np.asarray(low, dtype=np.float32)),
                ("maximum", np.asarray(high, dtype=np.float32)),
            )
        ],
    )
    model = helper.make_model(
        graph,
        producer_name="TradingGYM-stateless-student-v1",
        opset_imports=[helper.make_opsetid("", 17)],
        ir_version=9,
    )
    helper.set_model_props(
        model,
        {
            "trading_gym_student": json.dumps(
                asdict(student), sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        },
    )
    onnx.checker.check_model(model)
    return model


def export_linear_student(student: LinearStudent, path: str | Path) -> dict:
    """Write a new self-contained artifact; refuse to replace an existing file."""
    data = _onnx_model(student).SerializeToString()
    if len(data) > _MAX_ARTIFACT_BYTES:
        raise ValueError("student artifact exceeds the export budget")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(data)
    return {
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "artifact_bytes": len(data),
        "student_id": student.student_id,
        "source_id": student.source_id,
        "bundle_sha256": canonical_digest(student.bundle.to_dict()),
        "state_contract": "stateless",
        "opset": 17,
        "execution_authority": False,
    }


class ONNXStudent:
    def __init__(
        self,
        path: str | Path,
        *,
        expected_artifact_sha256: str,
        bundle: ModelBundle,
        providers: Sequence[str] = ("CPUExecutionProvider",),
    ):
        import onnx
        import onnxruntime as ort

        requested = tuple(providers)
        if any(name not in ort.get_available_providers() for name in requested):
            raise ValueError(
                "requested execution provider is unavailable; no implicit fallback"
            )
        if requested != ("CPUExecutionProvider",):
            raise ValueError(
                "this bounded path qualifies CPU only; accelerator qualification is separate"
            )
        with verified_regular_file_snapshot(
            path,
            expected_sha256=expected_artifact_sha256,
            label="student ONNX",
            max_bytes=_MAX_ARTIFACT_BYTES,
        ) as snapshot:
            data = snapshot.read_bytes()
        model = onnx.load_model_from_string(data)
        metadata = {entry.key: entry.value for entry in model.metadata_props}
        if (
            len(metadata) != 1
            or len(model.metadata_props) != 1
            or "trading_gym_student" not in metadata
        ):
            raise ValueError("artifact lacks unique student provenance")
        payload = json.loads(metadata["trading_gym_student"])
        payload["weights"] = tuple(payload["weights"])
        payload["runtime_versions"] = tuple(
            tuple(pair) for pair in payload["runtime_versions"]
        )
        student = LinearStudent(**payload)
        if student.bundle.to_dict() != bundle.to_dict():
            raise ValueError(
                "deployment bundle differs from the student feature/state contract"
            )
        if dict(student.runtime_versions) != _versions():
            raise ValueError(
                "deployment runtime changed; re-export and qualify parity explicitly"
            )
        # Restrict loading to this exact self-contained graph/metadata format.
        # No external tensors, custom operators, or recurrent state are loaded.
        if _onnx_model(student).SerializeToString() != data:
            raise ValueError("artifact is not the qualified stateless student graph")
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(
            data, sess_options=options, providers=list(requested)
        )
        session.disable_fallback()
        if tuple(session.get_providers()) != requested:
            raise ValueError("runtime changed execution provider during initialization")
        self.student, self.session = student, session
        self.student_id, self.source_id = student.student_id, student.source_id
        self.artifact_sha256, self.artifact_bytes = expected_artifact_sha256, len(data)

    def predict(
        self, observation, deterministic=True, *, state=None, episode_start=None
    ):
        if deterministic is not True or state is not None or episode_start is not None:
            raise ValueError("only deterministic stateless deployment is qualified")
        array = np.asarray(observation)
        single = array.ndim == 1
        values = _numeric(array.reshape(1, -1) if single else array, ndim=2)
        if values.shape[1] != len(self.student.weights):
            raise ValueError("observation dimension differs from the frozen bundle")
        output = self.session.run(["target_leverage"], {"observations": values})[0]
        if output.shape != (len(values), 1) or not np.isfinite(output).all():
            raise ValueError("runtime returned an invalid target tensor")
        return output[0] if single else output, None


def measure_student_parity(
    student: LinearStudent,
    deployed: ONNXStudent,
    *,
    features: pd.DataFrame,
    account_features: np.ndarray,
    teacher_targets: np.ndarray,
    teacher_available_at: Sequence[float],
    evaluation_as_of: float,
    absolute_tolerance: float = 1e-6,
    relative_tolerance: float = 1e-5,
    boundary_margin: float = 1e-4,
    warmup: int = 5,
    repetitions: int = 50,
) -> dict:
    """Held-out teacher imitation, runtime parity and named-host CPU timing."""
    observations = prepare_observations(student.bundle, features, account_features)
    if (
        deployed.student_id != student.student_id
        or len(features) == 0
        or features.index[0].timestamp() <= student.training_cutoff
        or any(
            not np_finite(x) or x < 0
            for x in (absolute_tolerance, relative_tolerance, boundary_margin)
        )
        or type(warmup) is not int  # noqa: E721
        or warmup < 0
        or type(repetitions) is not int  # noqa: E721
        or repetitions < 5
    ):
        raise ValueError("invalid held-out deployment experiment")
    targets, availability = _teacher_rows(
        features,
        teacher_targets,
        teacher_available_at,
        cutoff=evaluation_as_of,
        bounds=_bounds(student.bundle),
    )
    reference = student.actions(observations).ravel()
    deployed_actions = np.asarray(deployed.predict(observations)[0]).ravel()
    if (
        deployed_actions.shape != reference.shape
        or not np.isfinite(deployed_actions).all()
    ):
        raise ValueError("runtime output is misaligned or nonfinite")
    errors = np.abs(deployed_actions - reference)
    numerical = bool(
        np.allclose(
            deployed_actions,
            reference,
            atol=absolute_tolerance,
            rtol=relative_tolerance,
        )
    )
    directions = np.sign(reference) == np.sign(deployed_actions)
    low, high = _bounds(student.bundle)
    near_boundary = (
        np.minimum.reduce(
            (np.abs(reference), np.abs(reference - low), np.abs(reference - high))
        )
        <= boundary_margin
    )
    for index in range(warmup):
        deployed.predict(observations[index % len(observations)])
    timings = []
    for index in range(repetitions):
        start = time.perf_counter_ns()
        deployed.predict(observations[index % len(observations)])
        timings.append((time.perf_counter_ns() - start) / 1000.0)
    memory = None
    if platform.system() == "Linux":
        import resource

        memory = {
            "process_high_water_rss_kib": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
            "scope": "whole_process_including_imports_not_model_memory",
        }
    return {
        "experiment": "stateless_linear_student_cpu",
        "student_id": student.student_id,
        "source_id": student.source_id,
        "teacher_id": student.teacher_id,
        "teacher_artifact_sha256": student.teacher_artifact_sha256,
        "student_artifact_sha256": deployed.artifact_sha256,
        "bundle_sha256": canonical_digest(student.bundle.to_dict()),
        "evaluation_corpus_sha256": canonical_digest(
            {
                "features": feature_data_digest(features),
                "accounts": np.asarray(account_features).tolist(),
                "targets": targets.tolist(),
                "target_available_at": availability.tolist(),
            }
        ),
        "rows": len(features),
        "teacher_rmse": float(np.sqrt(np.mean((reference - targets) ** 2))),
        "teacher_direction_agreement": float(
            np.mean(np.sign(reference) == np.sign(targets))
        ),
        "max_absolute_runtime_error": float(errors.max()),
        "numerical_parity": numerical,
        "direction_mismatches": int((~directions).sum()),
        "boundary_rows": int(near_boundary.sum()),
        "boundary_direction_mismatches": int((~directions & near_boundary).sum()),
        "engineering_parity_pass": numerical
        and bool(directions.all())
        and bool(near_boundary.any()),
        "tolerances": {
            "absolute": absolute_tolerance,
            "relative": relative_tolerance,
            "boundary_margin": boundary_margin,
            "zero_semantic": "target_flat",
        },
        "latency_microseconds": {
            "warmup": warmup,
            "repetitions": repetitions,
            "p50": float(np.quantile(timings, 0.5)),
            "p95": float(np.quantile(timings, 0.95)),
            "scope": "single_observation_python_validation_plus_ORT_not_order_latency",
        },
        "memory": memory,
        "artifact_bytes": deployed.artifact_bytes,
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "runtime_versions": _versions(),
            "providers": deployed.session.get_providers(),
            "intra_op_threads": 1,
            "inter_op_threads": 1,
        },
        "economic_qualification": False,
        "execution_authority": False,
        "limitations": [
            "stateless linear student only",
            "teacher agreement is not calibration or alpha",
            "CPU timing is host-specific",
            "fees and position-dependent replay require separate evaluation",
        ],
    }
