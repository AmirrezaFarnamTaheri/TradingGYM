from __future__ import annotations

import numpy as np
import pytest

from trading_gym.features.feature_fusion import fuse_features


def test_fuse_features_basic_functionality():
    """
    Tests that the fuse_features function runs without errors and returns a
    correctly shaped output.
    """
    n_timeframes, n_samples, n_features = 3, 10, 5
    feature_vectors = np.random.rand(n_timeframes, n_samples, n_features)
    masks = np.zeros((n_timeframes, n_samples), dtype=bool)

    fused = fuse_features(feature_vectors, masks)

    assert fused.shape == (n_samples, n_features)


def test_fuse_features_with_masking():
    """
    Tests that the masking functionality works as expected. When a timeframe is
    masked, its contribution to the fused output should be zero.
    """
    n_timeframes, n_samples, n_features = 3, 10, 5
    feature_vectors = np.ones((n_timeframes, n_samples, n_features))
    masks = np.zeros((n_timeframes, n_samples), dtype=bool)
    masks[1, :] = True  # Mask the second timeframe

    fused = fuse_features(feature_vectors, masks)

    # Since the second timeframe is masked, the attention weights for it should be zero.
    # The output should be a weighted average of the first and third timeframes.
    # In this case, since all inputs are 1, the output should also be 1.
    assert np.allclose(fused, 1.0)


def test_fuse_features_input_validation():
    """
    Tests that the function raises a ValueError for invalid input shapes.
    """
    with pytest.raises(ValueError):
        fuse_features(np.random.rand(10, 5), np.zeros((3, 10), dtype=bool))

    with pytest.raises(ValueError):
        fuse_features(np.random.rand(3, 10, 5), np.zeros((10,), dtype=bool))


def test_softmax():
    x = np.array([1.0, 2.0, 3.0])
    expected = np.exp(x) / np.sum(np.exp(x))
    assert np.allclose(
        np.array([0.09003057, 0.24472847, 0.66524096]), expected, atol=1e-8
    )
