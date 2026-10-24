"""Frozen preprocessing and hash-bound JSON metadata, without RL test stubs."""
import hashlib
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import load_app_config
from trading_gym.pipelines.model_bundle import (
    BUNDLE_ATTRIBUTE, FrozenNormalizer, ModelBundle, attach_model_bundle,
    bundle_for_model, fit_model_bundle, read_model_bundle,
)


def frame(values=(0.0, 1.0, 2.0, 3.0)):
    return pd.DataFrame({"spread_ratio": values, "volatility": np.ones(len(values))},
                        index=pd.date_range("2025-01-01", periods=len(values), freq="5min", tz="UTC"))


def cfg():
    return load_app_config("config/app.yaml")


def write_zip(path, payload):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data", json.dumps(payload))
        archive.writestr("policy.pth", b"not-a-pickle-do-not-load")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixed_training_transform_is_invariant_to_unavailable_evaluation_tail():
    normalizer = FrozenNormalizer.fit(frame())
    evaluation = frame((10, 11, 12, 13))
    changed = evaluation.copy()
    changed.iloc[-1, 0] = 30000
    first = normalizer.transform(evaluation)[0]
    np.testing.assert_array_equal(first, normalizer.transform(changed)[0])
    assert normalizer.center == (1.5, 1.0)
    assert normalizer.scale == (1.5, 1.0)
    assert FrozenNormalizer.fit(changed).center != normalizer.center


def test_complete_schema_finite_values_and_positive_scale_are_required():
    normalizer = FrozenNormalizer.fit(frame())
    for candidate in (frame()[["volatility", "spread_ratio"]], frame().assign(extra=1), frame().drop(columns="volatility")):
        with pytest.raises(ValueError, match="identity/order"):
            normalizer.transform(candidate)
    with pytest.raises(ValueError, match="finite"):
        normalizer.transform(frame((0, 1, 2, float("nan"))))
    with pytest.raises(ValueError, match="positive"):
        FrozenNormalizer.from_stats(frame().columns, {"center":{"spread_ratio":0,"volatility":0},
                                                     "scale":{"spread_ratio":0,"volatility":1}})
    with pytest.raises(ValueError, match="complete feature schema"):
        FrozenNormalizer.from_stats(frame().columns, {"center":{"spread_ratio":0},"scale":{"spread_ratio":1}})


def test_bundle_round_trip_and_model_attribute_preserve_transform():
    bundle = fit_model_bundle(cfg(), frame())
    loaded = ModelBundle.from_dict(json.loads(json.dumps(bundle.to_dict())))
    assert loaded == bundle
    np.testing.assert_array_equal(loaded.normalizer.transform(frame()), bundle.normalizer.transform(frame()))
    model = SimpleNamespace()
    attach_model_bundle(model, bundle)
    assert bundle_for_model(model) == bundle
    first_stats = bundle.training_stats
    first_stats["center"]["spread_ratio"] = 900
    assert bundle.training_stats["center"]["spread_ratio"] == 1.5


def test_zip_inspector_checks_hash_and_does_not_deserialize_policy(tmp_path):
    bundle = fit_model_bundle(cfg(), frame())
    path = tmp_path / "model.zip"
    digest = write_zip(path, {BUNDLE_ATTRIBUTE: bundle.to_dict(),
                              "unused":{":serialized:":"this is deliberately not pickle"}})
    assert read_model_bundle(path, expected_model_sha256=digest) == bundle
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        read_model_bundle(path, expected_model_sha256="0" * 64)


def test_legacy_or_changed_config_fails_with_recovery_guidance(tmp_path):
    path = tmp_path / "legacy.zip"
    digest = write_zip(path, {})
    with pytest.raises(ValueError, match="retrain"):
        read_model_bundle(path, expected_model_sha256=digest)
    config = cfg()
    bundle = fit_model_bundle(config, frame())
    config.features.rolling_window += 1
    with pytest.raises(ValueError, match="configuration differs"):
        bundle.validate_config(config)


def test_duplicate_or_nonfinite_metadata_is_rejected(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data", '{"a":1,"a":2}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="duplicate"):
        read_model_bundle(path, expected_model_sha256=digest)
    data = fit_model_bundle(cfg(), frame()).to_dict()
    data["normalizer"]["scale"]["volatility"] = float("nan")
    digest = write_zip(path, {BUNDLE_ATTRIBUTE:data})
    with pytest.raises(ValueError, match="nonfinite"):
        read_model_bundle(path, expected_model_sha256=digest)


def test_environment_factory_requires_explicit_fitted_statistics():
    # This check precedes importing the optional Gym implementation.
    from trading_gym.pipelines.environment import create_environment
    with pytest.raises(ValueError, match="frozen training_stats required"):
        create_environment(cfg(), {"features":frame()})
