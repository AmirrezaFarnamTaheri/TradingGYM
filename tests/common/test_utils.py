import numpy as np
import pytest

from trading_gym.common.utils import TimeSeriesSplit


def test_time_series_split():
    data = np.arange(10)
    splitter = TimeSeriesSplit(n_splits=5, train_fraction=0.5)
    splits = list(splitter.split(data))

    assert len(splits) == 5

    # Check the first split
    train_indices, test_indices = splits[0]
    np.testing.assert_array_equal(train_indices, np.arange(5))
    np.testing.assert_array_equal(test_indices, np.arange(5, 6))

    # Check the last split
    train_indices, test_indices = splits[-1]
    np.testing.assert_array_equal(train_indices, np.arange(9))
    np.testing.assert_array_equal(test_indices, np.arange(9, 10))


def test_time_series_split_invalid_fraction():
    with pytest.raises(ValueError):
        TimeSeriesSplit(n_splits=5, train_fraction=0.0)

    with pytest.raises(ValueError):
        TimeSeriesSplit(n_splits=5, train_fraction=1.0)


def test_time_series_split_invalid_splits():
    with pytest.raises(ValueError):
        TimeSeriesSplit(n_splits=0, train_fraction=0.5)
