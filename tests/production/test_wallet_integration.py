import pytest

from trading_gym.execution.production.chain import normalize_chain
from trading_gym.execution.production.wallets import NonCustodialWalletRegistry


def test_wallet_registry_is_non_custodial_and_chain_bounded() -> None:
    wallet = NonCustodialWalletRegistry().admit(
        chain_id=1,
        address="0x" + "a" * 40,
    )
    assert wallet.provider == "trust-wallet"
    assert wallet.connected is True
    assert not hasattr(wallet, "private_key")
    assert normalize_chain(wallet.chain_id) == {"chain_id": 1, "name": "ethereum"}


def test_wallet_registry_rejects_invalid_address_and_chain() -> None:
    with pytest.raises(ValueError):
        NonCustodialWalletRegistry().admit(chain_id=1, address="bad")
    with pytest.raises(ValueError):
        normalize_chain(999999)
