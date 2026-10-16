"""Deterministic offline model-route admission and privacy regression tests."""

import copy

import pytest

from trading_gym.intelligence.model_route_preview import preview_route


def _candidate(provider="fast-local", **overrides):
    data = dict(
        provider_id=provider,
        model_id="fast-v1",
        wire_protocol="systemone",
        capabilities=["financial_state", "typed_choice"],
        context_window_tokens=8192,
        p95_latency_ms=120,
        cost_usd_per_1k_tokens=0.01,
        is_remote=False,
        enabled=True,
        health="available",
        remaining_requests=10,
        metadata_sha256="a" * 64,
    )
    data.update(overrides)
    return data


def _request(**overrides):
    data = dict(
        required_capabilities=["financial_state", "typed_choice"],
        required_protocol="systemone",
        input_tokens=3000,
        output_tokens=500,
        max_latency_ms=300,
        max_cost_usd=0.10,
        allow_remote_context=False,
    )
    data.update(overrides)
    return data


def test_cheapest_qualified_model_hint_is_deterministic_and_not_authoritative():
    slow = _candidate("slow", cost_usd_per_1k_tokens=0.02, p95_latency_ms=250)
    fast = _candidate("fast", cost_usd_per_1k_tokens=0.01, p95_latency_ms=100)
    result = preview_route([slow, fast], _request())
    assert result == preview_route([fast, slow], _request())
    assert result["selected_hint"] == "fast"
    assert result["selected_model_id"] == "fast-v1"
    assert result["estimated_usd"] == pytest.approx(0.035)
    assert result["non_authoritative"] is True
    assert result["selection_hint_only"] is True
    assert len(result["report_sha256"]) == 64


def test_remote_privacy_and_unknown_prices_never_masquerade_as_free():
    private = _candidate("local", cost_usd_per_1k_tokens=0.02)
    remote = _candidate(
        "remote", is_remote=True, cost_usd_per_1k_tokens=0,
    )
    unknown = _candidate("unknown", cost_usd_per_1k_tokens=None)
    report = preview_route([remote, unknown, private], _request())
    assert report["selected_hint"] == "local"
    assert "remote_disclosure_denied" in report["excluded"]["remote"]
    assert "price_unknown" in report["excluded"]["unknown"]


def test_unprobed_and_exhausted_providers_are_not_eligible():
    result = preview_route(
        [
            _candidate("alpha", health="unprobed"),
            _candidate("beta", remaining_requests=0),
        ],
        _request(),
    )
    assert result["selected_hint"] is None
    assert "provider_unavailable" in result["excluded"]["alpha"]
    assert "request_budget_exhausted" in result["excluded"]["beta"]


def test_protocol_context_capability_latency_and_cost_requirements_are_hard():
    candidates = [
        _candidate("proto", wire_protocol="openai_chat"),
        _candidate("context", context_window_tokens=2000),
        _candidate("cap", capabilities=["financial_state"]),
        _candidate("latency", p95_latency_ms=500),
        _candidate("cost", cost_usd_per_1k_tokens=1.0),
    ]
    result = preview_route(candidates, _request())
    assert result["selected_hint"] is None
    assert "protocol_mismatch" in result["excluded"]["proto"]
    assert "context_window_too_small" in result["excluded"]["context"]
    assert "missing_capability" in result["excluded"]["cap"]
    assert "latency_budget_exceeded" in result["excluded"]["latency"]
    assert "cost_budget_exceeded" in result["excluded"]["cost"]


def test_invalid_input_metadata_and_duplicate_providers_fail_closed():
    corrupted = [
        _candidate("alpha", cost_usd_per_1k_tokens=float("nan")),
        _candidate("alpha", metadata_sha256="unverified"),
        _candidate("alpha", remaining_requests=True),
        _candidate("alpha", context_window_tokens=0),
        _candidate("alpha", capabilities=["typed_choice", "typed_choice"]),
    ]
    for row in corrupted:
        with pytest.raises(ValueError):
            preview_route([row], _request())
    with pytest.raises(ValueError, match="duplicate"):
        preview_route([_candidate("alpha"), _candidate("alpha")], _request())


def test_evidence_digest_change_changes_report_receipt_without_reordering():
    row = _candidate("local")
    original = preview_route([row], _request())
    new_row = copy.deepcopy(row)
    new_row["metadata_sha256"] = "b" * 64
    altered = preview_route([new_row], _request())
    assert altered["report_sha256"] != original["report_sha256"]
    assert altered["selected_hint"] == original["selected_hint"]


def test_remote_model_is_only_hint_eligible_when_explicitly_consented():
    remote = _candidate(
        "private-cloud",
        is_remote=True,
        cost_usd_per_1k_tokens=0.01,
    )
    result = preview_route([remote], _request(allow_remote_context=True))
    assert result["selected_hint"] == "private-cloud"
    assert result["non_authoritative"] is True


def test_model_id_appears_explicitly_in_suggested_route():
    report = preview_route([_candidate("local")], _request())
    assert report["selected_model_id"] == "fast-v1"


def test_unhashable_external_protocol_and_health_fail_as_validation_errors():
    for row in [
        _candidate("local", wire_protocol=[]),
        _candidate("local", health={}),
    ]:
        with pytest.raises(ValueError):
            preview_route([row], _request())
    with pytest.raises(ValueError):
        preview_route([_candidate("local")], _request(required_protocol={}))


def test_public_preview_export_is_the_same_pure_routing_function():
    from trading_gym.intelligence import preview_model_route

    assert preview_model_route is preview_route

def test_unknown_remaining_request_budget_is_not_assumed_unlimited():
    preview = preview_route(
        [_candidate("unmeasured", remaining_requests=None)], _request()
    )
    assert preview["selected_hint"] is None
    assert preview["excluded"]["unmeasured"] == ["request_budget_unknown"]
