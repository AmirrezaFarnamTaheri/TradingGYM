from __future__ import annotations

from decimal import Decimal

import pytest

from trading_gym.adapters.enhanced_aban_client import EnhancedAbanClient, NetworkInfo


def client() -> EnhancedAbanClient:
    return object.__new__(EnhancedAbanClient)


def network(*, memo: bool = False) -> NetworkInfo:
    return NetworkInfo(
        id="n1",
        name="TRC20",
        memo=memo,
        fee=Decimal("1"),
        feeType="fixed",
        minTransfer=Decimal("10"),
        maxTransfer=Decimal("1000"),
    )


def test_withdrawal_fails_closed_when_network_admission_is_unavailable(monkeypatch) -> None:
    instance = client()
    monkeypatch.setattr(instance, "get_networks", lambda *args: (_ for _ in ()).throw(TimeoutError("secret")))
    called = False

    def request(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True

    monkeypatch.setattr(instance, "_make_request", request)
    with pytest.raises(RuntimeError, match="admission is unavailable"):
        instance.submit_withdrawal("USDT", "TRC20", 20, "T1234567890abcdef")
    assert called is False


def test_withdrawal_requires_memo_and_enforces_bounds(monkeypatch) -> None:
    instance = client()
    monkeypatch.setattr(instance, "get_networks", lambda *args: [network(memo=True)])
    monkeypatch.setattr(instance, "_make_request", lambda *args, **kwargs: {"id": "never"})
    with pytest.raises(ValueError, match="requires a memo"):
        instance.submit_withdrawal("USDT", "TRC20", 20, "T1234567890abcdef")
    with pytest.raises(ValueError, match="outside"):
        instance.submit_withdrawal("USDT", "TRC20", 5, "T1234567890abcdef", memo="123")


def test_withdrawal_is_sent_once_and_returns_only_safe_identifier(monkeypatch) -> None:
    instance = client()
    monkeypatch.setattr(instance, "get_networks", lambda *args: [network()])
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def request(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((args, kwargs))
        return {"success": True, "id": "withdrawal-123", "message": "authorization=venue-secret"}

    monkeypatch.setattr(instance, "_make_request", request)
    result = instance.submit_withdrawal(
        "usdt",
        "TRC20",
        Decimal("20"),
        "T1234567890abcdef",
    )
    assert result == "withdrawal-123"
    assert len(calls) == 1
    assert calls[0][0][:2] == ("POST", "users/withdraw/crypto")


def test_withdrawal_rejects_nonfinite_and_unsafe_fields(monkeypatch) -> None:
    instance = client()
    monkeypatch.setattr(instance, "get_networks", lambda *args: [network()])
    monkeypatch.setattr(instance, "_make_request", lambda *args, **kwargs: {"id": "x"})
    for amount in (float("nan"), float("inf"), -1):
        with pytest.raises(ValueError):
            instance.submit_withdrawal("USDT", "TRC20", amount, "T1234567890abcdef")
    with pytest.raises(ValueError):
        instance.submit_withdrawal("USDT", "../TRC20", 20, "T1234567890abcdef")
    with pytest.raises(ValueError):
        instance.submit_withdrawal("USDT", "TRC20", 20, "address with spaces")
