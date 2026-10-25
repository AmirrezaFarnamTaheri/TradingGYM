from __future__ import annotations

from pathlib import Path

from trading_gym.core.supply_chain import (
    build_dependency_inventory,
    verify_dependency_policy,
)


def test_project_dependency_inventory_is_hash_backed_and_policy_clean():
    root = Path(__file__).resolve().parents[2]
    inventory = build_dependency_inventory(root)
    policy = verify_dependency_policy(root)
    assert len(inventory["inventory_sha256"]) == 64
    assert inventory["summary"]["python_direct"] > 10
    assert inventory["summary"]["npm_packages"] > 10
    assert policy["valid"], policy["critical"]


def test_inventory_includes_all_poetry_locked_packages():
    project_root = Path(__file__).resolve().parents[2]
    inventory = build_dependency_inventory(project_root)
    locked = [
        item
        for item in inventory["components"]
        if item.get("ecosystem") == "pypi" and item.get("direct") is False
    ]
    assert len(locked) == inventory["summary"]["python_locked"]
    assert len(locked) >= 100
    assert all(item["artifact_hashes"] for item in locked)
    assert all(
        digest.startswith("sha256:") and len(digest) == 71
        for item in locked
        for digest in item["artifact_hashes"]
    )
