"""
AbanTether Asset Registry
=========================

Defines the universe of tradeable assets based on actual platform capabilities.

CRITICAL TRADING RULES:
1. Short Selling (max 5x): ONLY BTC and ETH
2. Credit Trading (max 20x): Only specific tokens listed below
3. Regular Trading (1x only): All other tokens including tokenized stocks/ETFs

This registry is based on direct analysis of Abantether platform (Nov 2025).
"""

import json
from pathlib import Path
from typing import Dict, FrozenSet

# =============================================================================
# SHORTABLE ASSETS (max 5x leverage for shorts)
# Only BTC and ETH can be short sold on Abantether
# =============================================================================
_ABAN_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "adapters" / "aban_tether_policy.json"
)
with _ABAN_POLICY_PATH.open("r", encoding="utf-8") as _policy_file:
    _ABAN_POLICY = json.load(_policy_file)

SHORTABLE_ASSETS: FrozenSet[str] = frozenset(
    str(symbol).upper() for symbol in _ABAN_POLICY["shortable"]
)

# Maximum quantities for short positions.  These values share the same
# authoritative policy source as the live Aban adapter.
SHORT_BORROW_CAPS: Dict[str, float] = {
    str(symbol).upper(): float(value)
    for symbol, value in _ABAN_POLICY["short_borrow_caps"].items()
}
_MAX_SHORT_LEVERAGE = float(_ABAN_POLICY["max_short_leverage"])
_MAX_CREDIT_LEVERAGE = float(_ABAN_POLICY["max_credit_leverage"])

# =============================================================================
# CREDIT TRADABLE ASSETS (max 20x leverage for longs)
# ONLY these tokens support leveraged long positions (Credit Trading)
# All other tokens can only be traded at 1x (spot)
# =============================================================================
CREDIT_TRADABLE_ASSETS: FrozenSet[str] = frozenset(
    {
        # --- Major Layer 1 & Payment ---
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "XRP",
        "ADA",
        "TRX",
        "AVAX",
        "DOT",
        "TON",
        "FTM",
        # --- Stablecoins ---
        "USDT",
        "USDC",
        # --- Meme Coins (High Liquidity) ---
        "DOGE",
        "SHIB",
        "PEPE",
        "WIF",
        "BONK",
        "FLOKI",
        "BOME",
        "TURBO",
        "AIDOGE",
        "MEW",
        "DOGS",
        "NOT",
        "X",
        # --- DeFi & L2 ---
        "ARB",
        "DYDX",
        "PENDLE",
        "ONDO",
        "AEVO",
        "GALA",
        "JASMY",
        "OM",
        "CAKE",
        "LUNA",
        "CFX",
        "MNT",
        # --- AI & Data ---
        "WLD",
        "NEAR",
        "FIL",
        # --- Gaming & Utility ---
        "SLP",
        "HMSTR",
        "TRB",
        "SEI",
        "AMP",
        "VELO",
        # --- Other Supported ---
        "RARE",
        "NEIRO",
        "DIA",
        "CLV",
    }
)

# =============================================================================
# TOKENIZED STOCKS & ETFs (1x only - NO leverage)
# These are available for trading but do NOT support Credit Trading
# =============================================================================
TOKENIZED_ASSETS: FrozenSet[str] = frozenset(
    {
        # Stocks
        "AAPLX",
        "GOOGLX",
        "AMZNX",
        "NVDAX",
        "TSLAX",
        "METAX",
        "COINX",
        "HOODX",
        "MSTRX",
        "CRCLX",
        "MCDX",
        # ETFs
        "QQQX",
        "SPYX",
        "GLDX",
        # Investment Boxes
        "AST_FIXBOX",
        "AST_CRYBOX",
        "AST_PROBOX",
        "AST_DFIBOX",
        "AST_XAUBOX",
    }
)

# =============================================================================
# SECTOR CLASSIFICATIONS
# =============================================================================
ASSET_SECTORS: Dict[str, str] = {
    # Layer 1
    "BTC": "L1",
    "ETH": "L1",
    "SOL": "L1",
    "BNB": "L1",
    "ADA": "L1",
    "AVAX": "L1",
    "DOT": "L1",
    "TON": "L1",
    "FTM": "L1",
    "NEAR": "L1",
    "TRX": "L1",
    "SEI": "L1",
    # Meme
    "DOGE": "MEME",
    "SHIB": "MEME",
    "PEPE": "MEME",
    "WIF": "MEME",
    "BONK": "MEME",
    "FLOKI": "MEME",
    "BOME": "MEME",
    "TURBO": "MEME",
    "AIDOGE": "MEME",
    "MEW": "MEME",
    "DOGS": "MEME",
    "NOT": "MEME",
    # DeFi
    "ARB": "DEFI",
    "DYDX": "DEFI",
    "PENDLE": "DEFI",
    "ONDO": "DEFI",
    "AEVO": "DEFI",
    "CAKE": "DEFI",
    "LUNA": "DEFI",
    "CFX": "DEFI",
    "MNT": "DEFI",
    "OM": "DEFI",
    # AI
    "WLD": "AI",
    "FIL": "AI",
    # Gaming
    "GALA": "GAMING",
    "JASMY": "GAMING",
    "SLP": "GAMING",
    "HMSTR": "GAMING",
    # Payment/Utility
    "XRP": "PAYMENT",
    "USDT": "STABLE",
    "USDC": "STABLE",
    # Tokenized Assets (No leverage)
    "QQQX": "ETF",
    "SPYX": "ETF",
    "GLDX": "ETF",
    "TSLAX": "STOCK",
    "NVDAX": "STOCK",
    "AAPLX": "STOCK",
    "GOOGLX": "STOCK",
    "AMZNX": "STOCK",
    "METAX": "STOCK",
}


def get_asset_sector(symbol: str) -> str:
    """Returns the sector classification of an asset."""
    base = symbol.replace("USDT", "").replace("-USDT", "")
    return ASSET_SECTORS.get(base, "OTHER")


def is_shortable(symbol: str) -> bool:
    """
    Check if an asset can be short sold.

    ONLY BTC and ETH are shortable on Abantether (max 5x leverage).
    """
    base = symbol.replace("USDT", "").replace("-USDT", "")
    return base in SHORTABLE_ASSETS


def is_credit_tradable(symbol: str) -> bool:
    """
    Check if an asset supports Credit Trading (leveraged longs).

    Only ~60 specific tokens support up to 20x leverage.
    Tokenized stocks/ETFs and unlisted tokens are 1x only.
    """
    base = symbol.replace("USDT", "").replace("-USDT", "")
    return base in CREDIT_TRADABLE_ASSETS


def is_tokenized_asset(symbol: str) -> bool:
    """Check if an asset is a tokenized stock/ETF (no leverage)."""
    base = symbol.replace("USDT", "").replace("-USDT", "")
    return base in TOKENIZED_ASSETS


def get_max_leverage(symbol: str, is_short: bool = False) -> float:
    """
    Get the maximum allowed leverage for a symbol.

    Returns:
        - 5.0 for short positions (BTC/ETH only)
        - 20.0 for long positions on credit-tradable assets
        - 1.0 for all other assets (spot only)
    """
    base = symbol.replace("USDT", "").replace("-USDT", "")

    if is_short:
        return (
            _MAX_SHORT_LEVERAGE if base in SHORTABLE_ASSETS else 0.0
        )  # Can't short non-shortable

    if base in CREDIT_TRADABLE_ASSETS:
        return _MAX_CREDIT_LEVERAGE

    return 1.0  # Spot only


def get_short_borrow_cap(symbol: str) -> float:
    """Get the maximum quantity that can be borrowed for shorting."""
    base = symbol.replace("USDT", "").replace("-USDT", "")
    return SHORT_BORROW_CAPS.get(base, 0.0)


# =============================================================================
# CONVENIENCE LISTS FOR CONFIGURATION
# =============================================================================


def get_recommended_training_symbols() -> list:
    """
    Get recommended symbols for RL training.

    Criteria:
    - Must support Credit Trading (leverage)
    - Good liquidity
    - Diverse sectors
    - Includes both shortable (BTC, ETH) for hedging
    """
    return [
        # Shortable (for hedging strategies)
        "BTCUSDT",
        "ETHUSDT",
        # High liquidity L1
        "SOLUSDT",
        "BNBUSDT",
        "AVAXUSDT",
        "TONUSDT",
        # Payment
        "XRPUSDT",
        # Meme (high volatility, good for learning)
        "DOGEUSDT",
        "PEPEUSDT",
        # DeFi
        "ARBUSDT",
        "PENDLEUSDT",
        # AI
        "WLDUSDT",
        "NEARUSDT",
    ]
