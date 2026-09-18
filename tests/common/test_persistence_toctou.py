"""Regression tests for the read_json symlink TOCTOU window."""

from pathlib import Path

import pytest

from trading_gym.common import persistence


def test_read_json_rejects_symlinks_with_the_lock_held(
    tmp_path: Path, monkeypatch
) -> None:
    """The symlink rejection must happen under the inter-process lock.

    Checking existence and rejecting symlinks *before* taking the lock leaves a
    window in which the path can be swapped for a symlink, letting the read
    follow a link outside the private data home.  This asserts the check runs
    with the lock held rather than trying to win a race.
    """
    real_lock = persistence.FileLock
    depth = {"held": 0, "reject_at": None}

    class CountingLock(real_lock):
        def acquire(self, *args, **kwargs):
            depth["held"] += 1
            return super().acquire(*args, **kwargs)

        def release(self):
            depth["held"] -= 1
            return super().release()

    real_reject = persistence._reject_symlink

    def observing_reject(path: Path) -> None:
        if depth["reject_at"] is None:
            depth["reject_at"] = depth["held"]
        return real_reject(path)

    monkeypatch.setattr(persistence, "FileLock", CountingLock)
    monkeypatch.setattr(persistence, "_reject_symlink", observing_reject)

    victim = tmp_path / "victim.json"
    persistence.atomic_write_json(victim, {"safe": True})
    link = tmp_path / "link.json"
    link.symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        persistence.read_json(link, {})

    assert (
        depth["reject_at"] == 1
    ), "symlink rejection must happen with the inter-process lock held"
    assert persistence.read_json(victim, {}) == {"safe": True}


def test_resolve_data_home_re_verifies_symlink_after_mkdir(
    tmp_path: Path, monkeypatch
) -> None:
    """``resolve_data_home`` must re-check for a symlink *after* ``mkdir``.

    The pre-existence check sees nothing while the directory does not yet
    exist, and ``mkdir(exist_ok=True)`` happily succeeds through a
    link-to-directory, so a swap landing between the check and the creation
    would otherwise point the private data home at an attacker-chosen tree.
    """
    home = tmp_path / "home"
    created = {"done": False}
    real_mkdir = Path.mkdir

    def racing_mkdir(self, *args, **kwargs):
        result = real_mkdir(self, *args, **kwargs)
        if self == home:
            created["done"] = True
        return result

    def fake_is_symlink(self):
        # The swap lands between the pre-check and the mkdir, so the path
        # only *becomes* a link once the directory exists.
        return created["done"] and self == home

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setenv("TRADING_GYM_HOME", str(home))

    with pytest.raises(ValueError, match="symbolic link"):
        persistence.resolve_data_home()


def test_read_json_returns_default_for_missing_file_under_lock(tmp_path: Path) -> None:
    """The missing-file fast path still returns the default and stays inside the
    lock so it cannot be raced into reading a swapped path."""
    missing = tmp_path / "absent.json"
    assert persistence.read_json(missing, {"default": True}) == {"default": True}
