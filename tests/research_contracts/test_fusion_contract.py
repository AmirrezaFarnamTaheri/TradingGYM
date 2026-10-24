import numpy as np
import pytest

from trading_gym.features.feature_fusion import fuse_features, preserve_base_context


def test_unit_conversion_changes_only_that_feature_not_routing():
    values = np.array([[[1.0, 0.0001]], [[2.0, 0.001]]])
    masks = np.zeros((2, 1), dtype=bool)
    first = fuse_features(values, masks)
    converted = values.copy()
    converted[:, :, 1] *= 10000
    second = fuse_features(converted, masks)
    np.testing.assert_allclose(second[:, 0], first[:, 0], rtol=0, atol=0)
    np.testing.assert_allclose(second[:, 1], first[:, 1] * 10000, rtol=1e-6)
    assert first[0, 0] == 1.5


def test_missing_frame_is_masked_before_arithmetic_and_weights_renormalize():
    values = np.array([[[1.0, 2.0], [1.0, 2.0]], [[3.0, 4.0], [np.nan, np.inf]]])
    masks = np.array([[False, False], [False, True]])
    actual = fuse_features(values, masks, weights=np.array([1.0, 3.0]))
    np.testing.assert_array_equal(actual, [[2.5, 3.5], [1.0, 2.0]])
    with pytest.raises(ValueError, match="positive-weight"):
        fuse_features(values, np.ones((2, 2), dtype=bool))
    with pytest.raises(ValueError, match="exact"):
        fuse_features(values, np.zeros((2, 1), dtype=bool))
    with pytest.raises(ValueError, match="nonnegative"):
        fuse_features(values, masks, weights=np.array([1.0, -1.0]))


def test_execution_units_and_categorical_context_remain_base_values():
    columns = ["log_ret", "volatility", "spread_ratio", "sector", "can_short"]
    values = np.array([[[1.0, 0.01, 0.001, 2.0, 1.0]], [[3.0, 0.2, 0.1, 9.0, 0.0]]])
    fused = fuse_features(values, np.zeros((2, 1), dtype=bool))
    actual = preserve_base_context(fused, values[0], columns)
    np.testing.assert_allclose(actual, [[2.0, 0.01, 0.001, 2.0, 1.0]])
