"""Chain IDs are routing identities; reject silent numeric coercion."""

import pytest

from trading_gym.execution.production.chain import normalize_chain


@pytest.mark.parametrize("chain_id", [1, 56, 137, 42161, "1", "56", "137"])
def test_canonical_chain_ids_retained(chain_id):
    record = normalize_chain(chain_id)
    assert record["chain_id"] == int(chain_id)
    assert isinstance(record["name"], str)


@pytest.mark.parametrize(
    "untrusted",
    [True, False, 1.0, 1.2, 56.9, "01", " 1", None, -1, 999999],
)
def test_coercible_chain_id_does_not_redirect_orders(untrusted):
    with pytest.raises(ValueError, match="chain_id|unsupported chain"):
        normalize_chain(untrusted)
