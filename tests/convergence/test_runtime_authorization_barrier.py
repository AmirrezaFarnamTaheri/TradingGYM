from __future__ import annotations

import os
from pathlib import Path

import pytest

from trading_gym.governance.runtime_authorization import (
    cleanup_runtime_authorization,
    commit_runtime_authorization,
    consume_runtime_authorization,
    issue_runtime_authorization,
)


def test_runtime_authorization_is_committed_and_consumed_once(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    authorization = issue_runtime_authorization(
        proposal_id="a" * 32,
        reservation_id="b" * 32,
        model_sha256="c" * 64,
    )
    commit_runtime_authorization(authorization, pid=os.getpid())
    receipt = consume_runtime_authorization(
        authorization.start_path,
        token=authorization.token,
        expected_model_sha256="c" * 64,
        commit_timeout_seconds=2,
    )
    assert receipt["authorization_id"] == authorization.authorization_id
    assert receipt["proposal_id"] == "a" * 32
    assert receipt["reservation_id"] == "b" * 32
    assert receipt["live_consent"]["actor"] == "system"
    assert receipt["live_consent"]["scope"] == "live.execute"
    assert receipt["live_consent"]["nonce"] == authorization.authorization_id
    with pytest.raises((FileNotFoundError, ValueError)):
        consume_runtime_authorization(
            authorization.start_path,
            token=authorization.token,
            expected_model_sha256="c" * 64,
            commit_timeout_seconds=1,
        )


def test_runtime_authorization_rejects_wrong_token_and_model(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    authorization = issue_runtime_authorization(
        proposal_id="a" * 32,
        reservation_id="b" * 32,
        model_sha256="c" * 64,
    )
    try:
        commit_runtime_authorization(authorization, pid=os.getpid())
        with pytest.raises(ValueError, match="token"):
            consume_runtime_authorization(
                authorization.start_path,
                token="wrong",
                expected_model_sha256="c" * 64,
                commit_timeout_seconds=1,
            )
    finally:
        cleanup_runtime_authorization(authorization)

    authorization = issue_runtime_authorization(
        proposal_id="a" * 32,
        reservation_id="b" * 32,
        model_sha256="c" * 64,
    )
    try:
        commit_runtime_authorization(authorization, pid=os.getpid())
        with pytest.raises(ValueError, match="model identity"):
            consume_runtime_authorization(
                authorization.start_path,
                token=authorization.token,
                expected_model_sha256="d" * 64,
                commit_timeout_seconds=1,
            )
    finally:
        cleanup_runtime_authorization(authorization)
