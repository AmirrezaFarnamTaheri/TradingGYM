"""Request budget authorization is shared across credentials and fail-closed."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.intelligence.llm_request_budget import (
    ProviderRequestBudget, RequestBudgetExhausted, RequestBudgetUnavailable,
)
from trading_gym.intelligence.llm_backend import httpx_transport


def test_attempts_share_one_state_and_count_failures(tmp_path):
    path = tmp_path / "requests.json"
    first = ProviderRequestBudget(path, limit=2, now=lambda: 7200)
    second = ProviderRequestBudget(path, limit=2, now=lambda: 7200)
    first.reserve()
    second.reserve()
    with pytest.raises(RequestBudgetExhausted):
        first.reserve()
    assert first.status()["used"] == 2
    assert first.status()["remaining"] == 0
    assert path.stat().st_mode & 0o077 == 0


def test_reset_and_rewind(tmp_path):
    clock = [3599]
    budget = ProviderRequestBudget(tmp_path / "requests.json", limit=1, now=lambda: clock[0])
    budget.reserve()
    with pytest.raises(RequestBudgetExhausted):
        budget.reserve()
    clock[0] = 3600
    budget.reserve()
    clock[0] = 3599
    with pytest.raises(RequestBudgetUnavailable, match="clock"):
        budget.reserve()


def test_lowest_operator_ceiling_wins(tmp_path):
    path = tmp_path / "requests.json"
    high = ProviderRequestBudget(path, limit=9, now=lambda: 10)
    low = ProviderRequestBudget(path, limit=2, now=lambda: 10)
    high.reserve()
    low.reserve()
    with pytest.raises(RequestBudgetExhausted):
        high.reserve()
    assert high.status()["ceiling"] == 2


def test_corrupt_state_denies_instead_of_reset(tmp_path):
    path = tmp_path / "requests.json"
    budget = ProviderRequestBudget(path, limit=3, now=lambda: 10)
    budget.reserve()
    path.write_text("{")
    with pytest.raises(RequestBudgetUnavailable):
        budget.reserve()
    path.unlink()
    dest = tmp_path / "unrelated.json"
    dest.write_text("unchanged")
    path.symlink_to(dest)
    with pytest.raises(RequestBudgetUnavailable):
        budget.reserve()
    assert dest.read_text() == "unchanged"


def test_invalid_cap_rejected():
    for cap in (0, -1, True, 100001, 1.5):
        with pytest.raises(ValueError):
            ProviderRequestBudget(Path("/irrelevant"), limit=cap)


def test_provider_configuration_has_strict_optional_cap():
    config = ProviderConfig(
        provider_id="fast-one", kind=ProviderKind.LOCAL,
        display_name="Fast model", model="fast",
        base_url="http://127.0.0.1:11434/v1",
        max_requests_per_hour=2,
    )
    assert config.max_requests_per_hour == 2
    with pytest.raises(ValueError):
        ProviderConfig.model_validate(config.model_dump() | {"max_requests_per_hour": True})


def test_transport_charges_each_attempt_and_blocks_second(monkeypatch, tmp_path):
    import httpx
    import trading_gym.intelligence.llm_backend as backend

    monkeypatch.setattr(
        backend, "default_request_budget_path",
        lambda *_: tmp_path / "budget.json",
    )
    # No actual network calls: the admission seam is tested against a fake
    # client, but the real code is responsible for invoking reserve().
    attempted = []
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def post(self, url, **kwargs):
            attempted.append(url)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}]
            }, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "Client", FakeClient)
    cfg = ProviderConfig(
        provider_id="fast", kind=ProviderKind.LOCAL,
        display_name="Fast model", model="fast",
        base_url="http://127.0.0.1:11434/v1",
        max_requests_per_hour=1,
    )
    transport = httpx_transport(cfg)
    assert transport(cfg.base_url, []).content == "ok"
    with pytest.raises(PermissionError, match="provider_request_budget_denied"):
        transport(cfg.base_url, [])
    assert len(attempted) == 1
