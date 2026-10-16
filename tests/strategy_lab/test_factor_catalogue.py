"""Bounded, read-only hierarchical research catalogue with >255 factors."""

import copy
import random

import pytest

from trading_gym.strategy_lab.factor_catalogue import (
    FactorCandidate,
    build_factor_catalogue,
    options_at,
    resolve_leaf,
)


def _candidates(count=484):
    return [
        FactorCandidate(
            factor_id=f"ALPHA_{i:04d}",
            family=("factor_zoo", "momentum" if i % 2 else "volatility"),
            source_sha256=f"{i:064x}",
        )
        for i in range(count)
    ]


def _leaves(node, path=()):
    if node["kind"] == "factor":
        yield path, node
    for child in node.get("children", []):
        yield from _leaves(child, (*path, child["id"]))


def test_large_catalogue_reaches_every_factor_with_bounded_options():
    catalogue = build_factor_catalogue(_candidates(), max_fanout=16)
    paths = list(_leaves(catalogue["root"]))
    assert len(paths) == 484
    assert {node["factor_id"] for _, node in paths} == {
        f"ALPHA_{i:04d}" for i in range(484)
    }

    def assert_fanout(node):
        assert len(node.get("children", [])) <= 16
        for child in node.get("children", []):
            assert_fanout(child)

    assert_fanout(catalogue["root"])
    selected_path, selected = paths[370]
    resolved = resolve_leaf(catalogue, selected_path)
    assert resolved["factor_id"] == selected["factor_id"]
    assert resolved["can_execute"] is False
    assert resolved["can_trade"] is False


def test_hash_bound_catalogue_is_order_independent_and_tamper_detected():
    candidates = _candidates(150)
    shuffled = candidates[:]
    random.Random(11).shuffle(shuffled)
    first = build_factor_catalogue(candidates, max_fanout=8)
    assert first == build_factor_catalogue(shuffled, max_fanout=8)
    altered = copy.deepcopy(first)
    altered["root"]["children"][0]["label"] = "modified"
    with pytest.raises(ValueError, match="digest"):
        options_at(altered, ())


def test_partial_or_unknown_selection_never_invents_a_factor():
    catalogue = build_factor_catalogue(_candidates(4))
    top = options_at(catalogue, ())
    assert all(option["can_execute"] is False for option in top)
    with pytest.raises(ValueError, match="leaf"):
        resolve_leaf(catalogue, (top[0]["id"],))
    with pytest.raises(ValueError, match="unknown"):
        resolve_leaf(catalogue, ("nonexistent",))


def test_small_fanout_still_reaches_five_hundred_factors():
    catalogue = build_factor_catalogue(_candidates(500), max_fanout=2)
    assert catalogue["candidate_count"] == 500
    assert len(list(_leaves(catalogue["root"]))) == 500


@pytest.mark.parametrize(
    "identifier,family,digest",
    [
        ("../x", ("zoo",), "a" * 64),
        ("x", ("zoo",), "a" * 64),
        ("ALPHA_010", ("zoo", ".."), "a" * 64),
        ("ALPHA_010", ("zoo",), "INVALID"),
        ("ALPHA_010", ("zoo\n",), "a" * 64),
    ],
)
def test_invalid_factor_identifiers_paths_and_digests_are_rejected(
    identifier, family, digest
):
    with pytest.raises(ValueError):
        build_factor_catalogue(
            [FactorCandidate(identifier, family, digest)]
        )


def test_duplicate_case_variant_factor_id_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        build_factor_catalogue(
            [
                FactorCandidate("FACTOR_A", ("zoo",), "a" * 64),
                FactorCandidate("factor_a", ("zoo",), "b" * 64),
            ]
        )


def test_fanout_must_be_bounded_integer_not_bool_or_float():
    for value in (True, 1, 256, 5.0):
        with pytest.raises(ValueError, match="fanout"):
            build_factor_catalogue(_candidates(4), max_fanout=value)


def test_public_strategy_lab_exports_safe_factor_discovery_only():
    import trading_gym.strategy_lab as public

    assert public.FactorCandidate is FactorCandidate
    assert public.build_factor_catalogue is build_factor_catalogue
    assert public.factor_options_at is options_at
    assert public.resolve_factor_leaf is resolve_leaf


def test_catalogue_options_distinguish_categories_from_leaves():
    catalogue = build_factor_catalogue(_candidates(4), max_fanout=16)
    root = options_at(catalogue, ())
    assert root[0]["kind"] == "branch"
    categories = options_at(catalogue, ("family:factor_zoo",))
    assert all(item["kind"] == "branch" for item in categories)
    factors = options_at(
        catalogue, ("family:factor_zoo", "family:volatility")
    )
    assert factors
    assert all(item["kind"] == "factor" for item in factors)
    assert all(
        item["can_execute"] is False and item["can_trade"] is False
        for item in factors
    )
