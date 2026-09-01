# Core Financial Engine

The `ExecutionEngine` is the simulation kernel of the Trading Gym. It is responsible for simulating the physics of the market with high fidelity. If the simulation is wrong, the Agent learns a fantasy. The system relies on a unified engine that powers both the simulation (backtesting) and production (live trading), ensuring strict parity.

## 1. The Execution Engine (`trading_gym.execution.execution_engine.py`)

The `ExecutionEngine` is the mathematical kernel of the system. It is a pure, stateless logic component that calculates the outcome of a trade given a market snapshot and a target position.

### 1.1 Decimal Precision

To prevent "floating point drift" (e.g., `0.1 + 0.2 = 0.30000000000000004`), the engine uses Python's `decimal.Decimal` for **ALL** state variables:
*   Equity
*   Position Size
*   Borrowed Amounts
*   Entry Prices
*   Realized PnL

**Config:**
*   Precision is defined in `VenueDecimals` (e.g., BTC price: 2 decimals, BTC qty: 8 decimals).
*   Rounding mode: `ROUND_DOWN` for quantities (conservative), `ROUND_HALF_UP` for prices.

### 1.2 Inputs
*   **State:** Current Position, Equity, Borrowed Amounts.
*   **Action:** Target Position (in Units).
*   **Market:** Mid Price, Spread, Liquidity depth, Volatility.

### 1.3 The Execution Pipeline
The `apply_trade` method follows a rigorous 6-step pipeline:

1.  **Boundary Conversion:**
    *   Converts float inputs to `decimal.Decimal` for precision.
    *   Handles "Dust" (positions smaller than `min_qty` are treated as 0).

2.  **Liquidity Capping:**
    *   Checks if the required trade size exceeds the available liquidity (`buy_cap_quote` / `sell_cap_quote`).
    *   If exceeded, the trade is scaled down to the maximum available liquidity. This prevents the agent from fantasizing about filling huge orders instantly.

3.  **Minimum Size Checks:**
    *   Verifies if the trade meets `min_notional` (e.g., 5 USDT) and `min_qty` requirements.
    *   If not met, the trade is rejected (No-Op).

4.  **Pricing & Costs Calculation:**
    *   **Executed Price:** `MidPrice +/- (Spread / 2)`.
    *   **Slippage:** Calculated using a two-component market impact model (see Section 2.4).
    *   **Fees:** `TradeValue * TakerFeeRate`.

5.  **Leverage Enforcement (Asymmetric):**
    *   **Long:** Checks against `max_leverage` (5x per Abantether limit).
    *   **Short:** Checks against `max_short_leverage` (5x).
    *   **Clipping:** If the target position exceeds these limits given the *post-cost* equity, the position is strictly clipped to the maximum allowable size.

6.  **Average Entry Price Update (WAP):**
    *   Calculates the Weighted Average Price (WAP) when increasing a position.
    *   Resets the Average Entry Price when flipping position direction (e.g., Long -> Short).
    *   Maintains the existing Average Entry Price when simply reducing a position.

7.  **Borrow State Update:**
    *   Updates `borrowed_base` (for shorts) and `borrowed_quote` (for longs).
    *   Enforces Short Whitelisting (BTC/ETH only).

### 1.4 Output
Returns an `ExecutionResult` containing:
*   `New State`: Updated position, equity, average entry price.
*   `Costs`: Breakdown of fees, slippage, and crossing costs.
*   `Flags`: Whether the trade was capped by liquidity.

## 2. The Live Execution Adapter (`trading_gym.execution.live_execution_adapter.py`)

The `LiveExecutionAdapter` serves as the translation layer between the generic `ExecutionEngine` logic and the specific API calls of the Abantether exchange.

### 2.1 State Machine for Position Flipping
One of the most complex challenges in automated execution is "flipping" a position from Long to Short (or vice versa) atomically. The adapter handles this via a state machine:

**Scenario: Flipping from Long (1.0 BTC) to Short (-0.5 BTC)**

1.  **Identify State:** Current = Long, Target = Short.
2.  **Step 1 (Close Long):**
    *   Calculate `Sell Amount = 1.0 BTC`.
    *   Call `place_order(side=SELL, quantity=1.0)`.
    *   Wait for fill.
3.  **Step 2 (Open Short):**
    *   Calculate `Short Amount = 0.5 BTC`.
    *   Call `create_short_position(quantity=0.5)`.
    *   Wait for fill.

### 2.2 Net Position Calculation
The exchange API usually reports two separate balances: "Spot Wallet" and "Short Positions". The Adapter aggregates these into a single "Net Position" for the agent:

$$ \text{Net Position} = \text{Spot Balance}_{Base} - \sum |\text{Short Obligations}_{Base}| $$

*   If Net Position > 0: The agent is **Net Long**.
*   If Net Position < 0: The agent is **Net Short**.

### 2.3 Concurrency & Safety
*   **Race Condition Prevention:** The `reconcile` method uses an `asyncio.Lock` to ensure that only one reconciliation cycle runs at a time, preventing double-orders during rapid updates.
*   **Validation:** Strict type checking and value validation (checking against `min_qty`) prevents API errors.

## 3. Cost Models & Financing

The engine simulates three types of transaction costs.

### 3.1 Trading Fees
*   **Model:** Linear fee on Notional Value.
*   **Formula:** `Cost = Abs(Delta) * Price * Fee_Rate`
*   **Default:** 0.1% (Taker) - Configurable in `CostConfig`.

### 3.2 Crossing Cost (Spread)
*   **Model:** You always pay the spread when crossing the book (Taker).
*   **Formula:** `Cost = Abs(Delta) * (Spread / 2)`
*   **Data Source:** Real historical spread from the `spread_bps` column in data.

### 3.3 Financing (Margin Interest)
*   **Model:** Continuous compound interest on borrowed funds.
*   **Rate:** Defined in `FinancingConfig` (e.g., 0.05% daily = 0.0005 in decimal).
*   **Formula:** `Interest = Borrowed_Amount * ((1 + Daily_Rate_Decimal)^(Minutes/1440) - 1)`
*   **Trigger:** Interest is paid on *Borrowed Quote* (Longs > Equity) and *Borrowed Base* (Shorts).

**Note:** The `daily_pct` config value is in percentage form (0.05 = 0.05%), which is converted to decimal (0.0005) in the calculation.

### 3.4 Slippage Model (Market Impact)

The engine implements a theoretically-grounded two-component market impact model:

**1. Volatility Impact (Kyle Model):**
```
slip_vol = volatility_lambda × |trade_size| × volatility
```
Based on Kyle (1985), market impact is proportional to trade size and increases with volatility. The `volatility_lambda` parameter represents market sensitivity to informed trading.

**2. Liquidity Impact (Almgren-Chriss Model):**
```
slip_liq = liquidity_lambda × sqrt(notional / liquidity_cap)
```
Based on Almgren & Chriss (2001), market impact follows a square-root relationship with trade size relative to available liquidity.

**Total Slippage:**
```
slippage_cost = |trade_size| × price × (slip_vol + slip_liq)
```

**Calibration Notes:**
- `volatility_lambda`: Default 0.1 (typical range: 0.05-0.20)
- `liquidity_lambda`: Default 0.1 (typical range: 0.05-0.15)

**References:**
- Kyle, A. S. (1985). "Continuous Auctions and Insider Trading"
- Almgren, R., & Chriss, N. (2001). "Optimal Execution of Portfolio Transactions"

## 4. Trading Loops

The system implements two trading loops:

### 4.1 The Async Trade Loop (`apps/common/trade_loop.py`)
Used by `apps/live` and `apps/paper`.
*   **Non-Blocking:** Uses `asyncio` for concurrency.
*   **Robust Error Handling:** Handles transient errors (retry with backoff) and critical errors (trigger Panic Protocol).

### 4.2 The Simulation Loop (`apps/simulator/main.py`)
Used for backtesting.
*   **Vectorized:** Iterates through historical DataFrame efficiently.
*   **Zero Latency:** Assumes instant execution (verified by "Sim == Prod" logic).

## 5. Abantether Integration (`EnhancedAbanClient`)

We utilize the `abantether-python-sdk`, wrapped in an `EnhancedAbanClient` to provide:
1.  **Rate Limiting:** Enforces the 60 req/min limit using a Token Bucket.
2.  **Pydantic Validation:** Ensures data integrity.
3.  **Retry Logic:** Automatically retries idempotent requests.
4.  **Secret Sanitization:** Strips API keys from logs.
