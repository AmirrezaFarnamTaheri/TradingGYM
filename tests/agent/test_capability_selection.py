"""Pure, versioned and non-executing research capability admission tests."""

import copy

import pytest

from trading_gym.agent.capability_selection import (
    preview_capabilities,
    seal_capability,
)


def candidate(name="portfolio.review", **changes):
    payload = {
        "capability_id": name,
        "kind": "tool",
        "version": 2,
        "capabilities": ["risk.read", "portfolio.explain"],
        "permissions": ["portfolio.read"],
        "remote_context": False,
        "enabled": True,
        "retired": False,
    }
    payload.update(changes)
    return seal_capability(payload)


def task(**changes):
    payload = {
        "required_capabilities": ["risk.read"],
        "allowed_permissions": ["portfolio.read"],
        "allow_remote_context": False,
        "limit": 3,
    }
    payload.update(changes)
    return payload


def test_approved_read_only_tool_is_hint_not_invocation():
    report = preview_capabilities([candidate()], task())
    assert report["selected"] == ["portfolio.review"]
    assert report["non_authoritative"] is True
    assert report["invocations"] == 0
    assert len(report["report_sha256"]) == 64


def test_retired_and_disabled_candidates_are_not_selected():
    report = preview_capabilities(
        [
            candidate("retired", retired=True),
            candidate("disabled", enabled=False),
        ],
        task(),
    )
    assert report["selected"] == []
    assert "retired" in report["excluded"]["retired"]
    assert "disabled" in report["excluded"]["disabled"]


def test_write_and_execute_permissions_are_never_admitted():
    report = preview_capabilities(
        [
            candidate("unsafe", permissions=["trade.execute"]),
            candidate("shell", permissions=["shell.execute"]),
        ],
        task(allowed_permissions=["trade.execute", "shell.execute"]),
    )
    assert report["selected"] == []
    assert all(
        "forbidden_authority" in reasons for reasons in report["excluded"].values()
    )


def test_remote_context_requires_explicit_request_consent():
    remote = candidate("remote", remote_context=True)
    blocked = preview_capabilities([remote], task())
    assert blocked["selected"] == []
    assert "remote_context_denied" in blocked["excluded"]["remote"]
    allowed = preview_capabilities([remote], task(allow_remote_context=True))
    assert allowed["selected"] == ["remote"]


def test_required_capability_and_scope_filters_are_hard():
    report = preview_capabilities(
        [
            candidate("wrong-domain", capabilities=["data.read"]),
            candidate("outside-scope", permissions=["account.read"]),
        ],
        task(),
    )
    assert "missing_capability" in report["excluded"]["wrong-domain"]
    assert "permission_not_granted" in report["excluded"]["outside-scope"]


def test_manifest_seal_prevents_undetected_mutation():
    sealed = candidate()
    poisoned = copy.deepcopy(sealed)
    poisoned["permissions"] = ["trade.execute"]
    with pytest.raises(ValueError, match="manifest"):
        preview_capabilities([poisoned], task())


def test_duplicate_ids_or_uncanonical_version_denied():
    with pytest.raises(ValueError, match="duplicate"):
        preview_capabilities([candidate(), candidate()], task())
    with pytest.raises(ValueError):
        seal_capability(
            {
                "capability_id": "x",
                "kind": "tool",
                "version": True,
                "capabilities": [],
                "permissions": [],
                "remote_context": False,
                "enabled": True,
                "retired": False,
            }
        )


def test_deterministic_receipt_and_input_order_invariance():
    first = candidate("alpha")
    second = candidate("beta")
    a = preview_capabilities([first, second], task())
    b = preview_capabilities([second, first], task())
    assert a == b
    assert a["selected"] == ["alpha", "beta"]


def test_reject_unbounded_inventory_and_ambiguous_permissions():
    with pytest.raises(ValueError):
        preview_capabilities([candidate(f"p{i}") for i in range(65)], task())
    with pytest.raises(ValueError):
        seal_capability(
            {
                "capability_id": "x",
                "kind": "tool",
                "version": 1,
                "capabilities": ["risk.read"],
                "permissions": ["portfolio.read", "portfolio.read"],
                "remote_context": False,
                "enabled": True,
                "retired": False,
            }
        )


def test_cancel_permissions_are_never_treated_as_read_only():
    report = preview_capabilities(
        [candidate("cancel", permissions=["order.cancel"])],
        task(allowed_permissions=["order.cancel"]),
    )
    assert report["selected"] == []
    assert "forbidden_authority" in report["excluded"]["cancel"]


def test_unhashable_kind_is_rejected_as_domain_error_not_type_error():
    details = {
        "capability_id": "read-only",
        "kind": [],
        "version": 1,
        "capabilities": [],
        "permissions": [],
        "remote_context": False,
        "enabled": True,
        "retired": False,
    }
    with pytest.raises(ValueError, match="capability"):
        seal_capability(details)
