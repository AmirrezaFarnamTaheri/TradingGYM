# Abantether Broker Rules

> **Last Updated:** 2025-11-25
> **Status:** Verified against platform documentation

This project is specifically tailored for the **Abantether** exchange. This document details the **confirmed** regulations, limitations, and operational mechanics for Credit Trading and Short Selling.

## ⚠️ CRITICAL COMPLIANCE NOTICE

**These rules are enforced in both simulation and live trading.** The "Sim = Prod" philosophy demands that we simulate the *limitations* of the broker, not just the opportunities.

---

## 1. Credit Trading (Long Positions) - اعتبار معاملاتی

Credit Trading allows users to borrow funds (Toman or Tether) to increase purchasing power, betting on price increases.

### 1.1 Leverage Limits

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Minimum Leverage** | 1.5x | Platform slider minimum |
| **Maximum Leverage** | **20.0x** | Platform maximum for LONG positions |
| **Leverage Step** | 0.5x | Increments on slider |

**Example:** With 20x leverage, a 1,000 USDT deposit allows trading up to 20,000 USDT worth of assets.

### 1.2 Credit Limits

| Currency | Minimum | Maximum |
|----------|---------|---------|
| **Toman (IRT)** | 1,000,000 | 5,000,000,000 |
| **Tether (USDT)** | 10 | 300,000 |

### 1.3 Collateral & Margin

- **Collateral:** Purchased assets + initial margin are locked in "Credit Wallet"
- **Cannot withdraw** until credit is settled
- **Profits:** 100% belong to user
- **Losses:** Deducted from user's initial margin

### 1.4 Fees

- **Daily Interest:** Applied to borrowed amount
- **Settlement Fee:** Paid when closing position
- **Trading Fee:** Standard taker fee (7.5 bps)

### 1.5 Supported Assets for Credit Trading (Leverage)

**⚠️ CRITICAL:** NOT all tokens support leverage! Only ~60 specific tokens can use Credit Trading.

**Tokens WITH Leverage Support (up to 20x):**
- **Layer 1:** BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOT, TON, FTM, TRX, SEI, NEAR
- **Meme:** DOGE, SHIB, PEPE, WIF, BONK, FLOKI, BOME, TURBO, AIDOGE, MEW, DOGS, NOT
- **DeFi:** ARB, DYDX, PENDLE, ONDO, AEVO, GALA, JASMY, OM, CAKE, LUNA, CFX, MNT
- **AI/Utility:** WLD, FIL, HMSTR, TRB, AMP, VELO, RARE, NEIRO, DIA, CLV, SLP
- **Stablecoins:** USDT, USDC

**Tokens WITHOUT Leverage (1x spot only):**
- **Tokenized Stocks:** TSLAX, NVDAX, AAPLX, GOOGLX, AMZNX, METAX, etc.
- **Tokenized ETFs:** QQQX, SPYX, GLDX
- **Unlisted Cryptos:** LINK, UNI, AAVE, and many others

---

## 2. Short Selling (Short Positions) - فروش استقراضی

Short Selling allows users to profit from falling prices by borrowing cryptocurrency, selling it, and buying back at a lower price.

### 2.1 Critical Restrictions

| Parameter | Value |
|-----------|-------|
| **Available Assets** | **BTC and ETH ONLY** |
| **Max BTC Short** | 0.5 BTC per position |
| **Max ETH Short** | 10.0 ETH per position |
| **Max Leverage** | **5.0x** (lower than long due to squeeze risk) |

**⚠️ IMPORTANT:** Unlike Credit Trading, Short Selling is **strictly limited** to Bitcoin and Ethereum. Other cryptocurrencies **cannot be shorted**.

### 2.2 Short Selling Process

1. **Deposit Margin:** User deposits Toman or Tether as collateral
2. **Borrow Crypto:** System lends BTC or ETH from exchange inventory
3. **Auto-Sell:** System immediately sells at current market price
4. **Wait:** User waits for price to drop
5. **Close Position:** User buys back at (hopefully) lower price
6. **Return Loan:** Borrowed crypto returned, profit is the difference

### 2.3 Liquidation Risk

If price **rises** instead of falling:
- Losses are deducted from margin
- **Warning Level:** Margin call notification
- **Liquidation Level:** Position auto-closed, margin lost

---

## 3. Risk Management & Safety Protocols

### 3.1 Margin Requirements

| Leverage | Initial Margin | Maintenance Margin |
|----------|---------------|-------------------|
| 1.5x | 66.7% | ~60% |
| 2.0x | 50.0% | ~45% |
| 3.0x | 33.3% | ~28% |
| 5.0x | 20.0% | ~15% |

### 3.2 Liquidation Mechanics

```
Liquidation occurs when:
  Equity / Position Value < Liquidation Threshold (~5%)

Warning occurs when:
  Equity / Position Value < Warning Threshold (~10%)
```

**Action on Liquidation:**
1. Position is automatically closed at market price
2. Borrowed funds are returned to exchange
3. User loses initial margin (minus any remaining equity)

### 3.3 Duration Limits

- **Maximum Duration:** Platform-specific, typically 30-90 days
- **Daily Fee:** Interest accrues daily on borrowed amount
- **Extension:** May be possible before expiration

---

## 4. API Rate Limits

| Limit | Value | Notes |
|-------|-------|-------|
| **Requests/Minute** | 60 | Per API key |
| **Our Safety Buffer** | 50 | Internal limit |

**Implementation:** `GlobalRateLimiter` uses Token Bucket algorithm. If limit reached, requests are delayed, not rejected.

---

## 5. Minimum Trade Sizes

| Parameter | Typical Value |
|-----------|--------------|
| **Min Notional** | ~10 USDT |
| **Min BTC Qty** | 0.0001 |
| **Min ETH Qty** | 0.001 |

**Dust Handling:**
- Trades below minimum are rejected (No-Op)
- Positions below minimum on partial close are fully closed

---

## 6. Implementation in Code

### 6.1 Configuration (`config/app.yaml`)

```yaml
env:
  leverage:
    min_leverage: 1.5
    max_leverage: 5.0        # NOT 20x!
    max_short_leverage: 5.0  # Same as long

  shorts:
    enabled: true
    allowed_symbols:
      - BTC
      - ETH
    borrow_caps:
      BTC: 0.5   # Max 0.5 BTC
      ETH: 10.0  # Max 10 ETH
```

### 6.2 Enforcement Points

| Rule | Enforcement Location |
|------|---------------------|
| Leverage Limits | `ExecutionEngine._clip_target_position()` |
| Short Whitelist | `ExecutionEngine._enforce_short_rules()` |
| Borrow Caps | `BorrowCapActionClipper.clip_action()` |
| Rate Limits | `GlobalRateLimiter.acquire()` |
| Min Size | `ExecutionEngine._check_minimum_size()` |

---

## 7. Supported Asset Categories

Based on Abantether platform data:

### 7.1 Cryptocurrencies (Long Only for Most)

| Category | Examples | Shortable |
|----------|----------|-----------|
| **Major L1** | BTC, ETH, SOL, BNB | BTC, ETH only |
| **Stablecoins** | USDT, USDC, DAI | ❌ |
| **Meme** | DOGE, SHIB, PEPE, WIF | ❌ |
| **DeFi** | UNI, AAVE, LINK, MKR | ❌ |
| **AI** | FET, RENDER, TAO, GRT | ❌ |
| **L2** | ARB, OP, MATIC, STRK | ❌ |
| **Gaming** | IMX, GALA, SAND, AXS | ❌ |

### 7.2 Tokenized Stocks & ETFs

| Asset | Symbol | Type |
|-------|--------|------|
| Apple | AAPLX | Stock |
| Tesla | TSLAX | Stock |
| NVIDIA | NVDAX | Stock |
| Google | GOOGLX | Stock |
| Amazon | AMZNX | Stock |
| Meta | METAX | Stock |
| Coinbase | COINX | Stock |
| Nasdaq ETF | QQQX | ETF |
| S&P 500 ETF | SPYX | ETF |
| Gold ETF | GLDX | ETF |

---

## 8. Summary Comparison

| Feature | Credit Trading (Long) | Short Selling |
|---------|----------------------|---------------|
| **Strategy** | Profit from price ↑ | Profit from price ↓ |
| **What you borrow** | Toman/USDT | BTC or ETH |
| **Max Leverage** | **20x** | **5x** |
| **Max Volume** | 300k USDT | 0.5 BTC / 10 ETH |
| **Asset Variety** | High (100+ coins) | Low (BTC/ETH only) |
| **Liquidation Trigger** | Price drops | Price rises |

**⚠️ ASYMMETRIC LEVERAGE:** Long positions can use up to 20x leverage, but short positions are limited to 5x due to higher squeeze risk.

---

## 9. Confirmed Leverage Rules (from Platform Screenshots)

| Position Type | Leverage Range | Confirmed |
|---------------|---------------|-----------|
| **Long (Credit Trading)** | 1.5x - **20x** | ✅ Verified from UI |
| **Short (BTC/ETH only)** | 1.5x - **5x** | ✅ Verified from UI |

**ASYMMETRIC LEVERAGE CONFIRMED:** The platform clearly shows different leverage limits:
- Credit Trading (اعتبار معاملاتی): Slider goes from 1.5x to 20x
- Short Selling (فروش استقراضی): Slider goes from 1.5x to 5x only

This asymmetry exists because short squeezes are more violent than long liquidations.
