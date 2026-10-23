"""Platform and failure-path contracts for directory durability."""

from pathlib import Path
from unittest.mock import patch

import pytest

from trading_gym.common.persistence import _fsync_directory


def test_directory_sync_is_a_noop_off_posix(tmp_path: Path) -> None:
    with patch("os.name", "nt"), patch("os.open") as open_directory:
        _fsync_directory(tmp_path)
    open_directory.assert_not_called()


def test_directory_sync_closes_the_directory_descriptor(tmp_path: Path) -> None:
    with (
        patch("os.name", "posix"),
        patch("os.open", return_value=13) as open_directory,
        patch("os.fsync") as fsync,
        patch("os.close") as close,
    ):
        _fsync_directory(tmp_path)
    assert open_directory.call_args.args[0] == tmp_path
    fsync.assert_called_once_with(13)
    close.assert_called_once_with(13)


def test_directory_open_failure_is_best_effort(tmp_path: Path) -> None:
    with (
        patch("os.name", "posix"),
        patch("os.open", side_effect=OSError),
        patch("os.fsync") as fsync,
        patch("os.close") as close,
    ):
        _fsync_directory(tmp_path)
    fsync.assert_not_called()
    close.assert_not_called()


@pytest.mark.parametrize("descriptor", [13, -1])
def test_directory_sync_failure_still_releases_valid_handles(
    tmp_path: Path, descriptor: int
) -> None:
    with (
        patch("os.name", "posix"),
        patch("os.open", return_value=descriptor),
        patch("os.fsync", side_effect=OSError),
        patch("os.close") as close,
    ):
        _fsync_directory(tmp_path)
    if descriptor >= 0:
        close.assert_called_once_with(descriptor)
    else:
        close.assert_not_called()
