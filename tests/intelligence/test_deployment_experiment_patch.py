from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
import yaml

from trading_gym.common.config import EnvConfig, FeatureConfig, TrainingConfig
from trading_gym.intelligence.deployment_experiment import (
    ONNXStudent, export_linear_student, fit_linear_student, measure_student_parity, prepare_observations,
)
from trading_gym.pipelines.model_bundle import fit_model_bundle


def student_fixture():
    raw = yaml.safe_load((Path(__file__).parents[2] / "config/app.yaml").read_text())
    cfg = SimpleNamespace(env=EnvConfig.model_validate(raw["env"]),
        features=FeatureConfig.model_validate(raw["features"]),
        training=TrainingConfig.model_validate(raw["training"]))
    training = pd.DataFrame({"spread_ratio": np.linspace(.001, .003, 12),
        "volatility": np.linspace(.01, .03, 12)},
        index=pd.date_range("2024-01-01", periods=12, freq="5min", tz="UTC"))
    bundle = fit_model_bundle(cfg, training)
    rng = np.random.default_rng(7)
    def corpus(start):
        normalized = rng.uniform(-1, 1, size=(24, 2)).astype(np.float32)
        normalized[0] = 0  # an explicit target-zero neighborhood probe
        features = pd.DataFrame(normalized * np.asarray(bundle.normalizer.scale) +
            np.asarray(bundle.normalizer.center), columns=bundle.normalizer.columns,
            index=pd.date_range(start, periods=24, freq="5min", tz="UTC"))
        accounts = rng.uniform(-.3, .3, size=(24, 3)).astype(np.float32)
        accounts[0] = 0
        observations = prepare_observations(bundle, features, accounts)
        teacher = observations @ np.asarray([.05, -.03, .1, .02, .07], dtype=np.float32)
        available = features.index.asi8 / 1e9 + 1
        return features, accounts, teacher, available
    features, accounts, teacher, available = corpus("2024-02-01")
    student = fit_linear_student(bundle=bundle, features=features, account_features=accounts,
        teacher_targets=teacher, teacher_available_at=available, teacher_id="synthetic-linear-teacher",
        teacher_artifact_sha256=hashlib.sha256(b"synthetic-linear-teacher-v1").hexdigest(),
        training_cutoff=float(available[-1]), ridge=0)
    return student, (features, accounts, teacher, available), corpus("2024-03-01")


def run_measurement(student, deployed, evaluation):
    features, accounts, teacher, available = evaluation
    return measure_student_parity(student, deployed, features=features, account_features=accounts,
        teacher_targets=teacher, teacher_available_at=available,
        evaluation_as_of=float(available[-1]), warmup=2, repetitions=10)


def test_actual_onnx_cpu_export_parity_and_measured_latency(tmp_path):
    student, _, evaluation = student_fixture()
    artifact = export_linear_student(student, tmp_path / "student.onnx")
    deployed = ONNXStudent(tmp_path / "student.onnx",
        expected_artifact_sha256=artifact["artifact_sha256"], bundle=student.bundle)
    report = run_measurement(student, deployed, evaluation)
    assert report["engineering_parity_pass"] and report["boundary_rows"] >= 1
    assert report["max_absolute_runtime_error"] < 1e-6
    assert report["teacher_rmse"] < 1e-6
    assert report["host"]["providers"] == ["CPUExecutionProvider"]
    assert report["latency_microseconds"]["p95"] > 0
    assert report["source_id"].startswith("student/")
    assert not report["execution_authority"] and not report["economic_qualification"]
    json.dumps(report, allow_nan=False)


def test_artifact_schema_provider_and_recurrent_state_cannot_silently_change(tmp_path):
    student, _, _ = student_fixture()
    path = tmp_path / "student.onnx"
    artifact = export_linear_student(student, path)
    kwargs = dict(expected_artifact_sha256=artifact["artifact_sha256"], bundle=student.bundle)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        ONNXStudent(path, **{**kwargs, "expected_artifact_sha256": "0" * 64})
    with pytest.raises(ValueError, match="no implicit fallback|CPU only"):
        ONNXStudent(path, providers=("CUDAExecutionProvider",), **kwargs)
    deployed = ONNXStudent(path, **kwargs)
    with pytest.raises(ValueError, match="stateless"):
        deployed.predict(np.zeros(5), state=np.zeros(2))
    with pytest.raises(ValueError, match="dimension"):
        deployed.predict(np.zeros(4))
    with pytest.raises(ValueError, match="finite real"):
        deployed.predict(np.array([0., 0., np.nan, 0., 0.]))
    with pytest.raises(ValueError, match="stateless"):
        replace(student, state_contract="recurrent")
    with pytest.raises(FileExistsError):
        export_linear_student(student, path)


def test_train_evaluation_availability_and_feature_order_are_bound(tmp_path):
    student, training, evaluation = student_fixture()
    artifact = export_linear_student(student, tmp_path / "student.onnx")
    deployed = ONNXStudent(tmp_path / "student.onnx",
        expected_artifact_sha256=artifact["artifact_sha256"], bundle=student.bundle)
    with pytest.raises(ValueError, match="held-out"):
        run_measurement(student, deployed, training)
    features, accounts, teacher, available = evaluation
    with pytest.raises(ValueError, match="identity/order"):
        prepare_observations(student.bundle, features.iloc[:, ::-1], accounts)
    with pytest.raises(ValueError, match="unavailable"):
        measure_student_parity(student, deployed, features=features, account_features=accounts,
            teacher_targets=teacher, teacher_available_at=available,
            evaluation_as_of=float(available[-1] - 1), repetitions=5)


def test_numeric_tolerance_alone_cannot_hide_a_direction_flip(tmp_path):
    student, _, evaluation = student_fixture()
    artifact = export_linear_student(student, tmp_path / "student.onnx")
    real = ONNXStudent(tmp_path / "student.onnx",
        expected_artifact_sha256=artifact["artifact_sha256"], bundle=student.bundle)
    class FlippedBoundary:
        student_id, artifact_sha256, artifact_bytes, session = (
            real.student_id, real.artifact_sha256, real.artifact_bytes, real.session)
        def predict(self, observations):
            result, _ = real.predict(observations)
            result = result.copy()
            if result.ndim == 2:
                result[0, 0] = -1e-8 if result[0, 0] >= 0 else 1e-8
            return result, None
    report = run_measurement(student, FlippedBoundary(), evaluation)
    assert report["numerical_parity"]
    assert report["direction_mismatches"] >= 1
    assert not report["engineering_parity_pass"]
