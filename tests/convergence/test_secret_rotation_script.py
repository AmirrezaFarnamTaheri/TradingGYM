from pathlib import Path

import pytest

from scripts.rotate_secrets import SecretRotationError, rotate_env_key


def test_check_mode_never_mutates_or_returns_secret(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("API_KEY=changeme-insecure-default\n", encoding="utf-8")
    before = env.read_bytes()
    result = rotate_env_key(env, key_name="API_KEY")
    assert result.secure is False and result.changed is False
    assert env.read_bytes() == before
    assert not hasattr(result, "secret")


def test_apply_rotates_atomically_and_preserves_other_authorities(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "API_KEY=old-insecure\nWORKER_API_KEY=" + "w" * 40 + "\n", encoding="utf-8"
    )
    result = rotate_env_key(env, key_name="API_KEY", apply=True)
    content = env.read_text(encoding="utf-8")
    assert result.changed and result.secure
    assert "old-insecure" not in content
    assert "WORKER_API_KEY=" + "w" * 40 in content
    assert env.stat().st_mode & 0o777 == 0o600


def test_symlink_and_duplicate_definitions_fail_closed(tmp_path: Path):
    real = tmp_path / "real.env"
    real.write_text("API_KEY=" + "a" * 40 + "\n", encoding="utf-8")
    link = tmp_path / ".env"
    link.symlink_to(real)
    with pytest.raises(SecretRotationError):
        rotate_env_key(link, key_name="API_KEY", apply=True)

    duplicate = tmp_path / "duplicate.env"
    duplicate.write_text(
        "API_KEY=" + "a" * 40 + "\nAPI_KEY=" + "b" * 40 + "\n", encoding="utf-8"
    )
    with pytest.raises(SecretRotationError):
        rotate_env_key(duplicate, key_name="API_KEY", apply=True)


def test_missing_key_requires_explicit_create(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OTHER=value\n", encoding="utf-8")
    result = rotate_env_key(env, key_name="EMERGENCY_API_KEY")
    assert result.present is False
    created = rotate_env_key(env, key_name="EMERGENCY_API_KEY", apply=True, create=True)
    assert created.changed is True
    assert "EMERGENCY_API_KEY=" in env.read_text(encoding="utf-8")
