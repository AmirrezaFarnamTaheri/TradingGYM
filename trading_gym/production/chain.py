"""Strict chain identity normalization before any network-specific operation."""

import re

SUPPORTED_CHAINS = {1: "ethereum", 56: "bsc", 137: "polygon", 42161: "arbitrum"}
_CANONICAL_DECIMAL = re.compile(r"^[1-9][0-9]*$")


def normalize_chain(chain_id):
    """Accept integral IDs or canonical decimal URL strings, never coercion.

    In particular ``True``, ``1.9`` and ``" 1"`` must not silently become
    Ethereum chain 1. This function does not authorize chain mutations.
    """
    if type(chain_id) is int:
        cid = chain_id
    elif isinstance(chain_id, str) and _CANONICAL_DECIMAL.fullmatch(chain_id):
        cid = int(chain_id)
    else:
        raise ValueError("chain_id must be a canonical integer or decimal ID")
    if cid not in SUPPORTED_CHAINS:
        raise ValueError("unsupported chain")
    return {"chain_id": cid, "name": SUPPORTED_CHAINS[cid]}
