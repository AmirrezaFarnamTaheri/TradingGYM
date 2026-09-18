//! Deterministic playground simulation kernel (ADR-035).
//!
//! This is a bit-exact reimplementation of the hot loop in
//! `trading_gym/playground/runner.py::run_playground` — the long-only
//! next-open fill model with per-change costs. The Python function stays
//! the *reference semantics*; this crate exists because the strategy
//! forge evaluates (candidates × sealed windows) full simulations per
//! generation, and that loop is not vectorisable (it carries position
//! state across bars).
//!
//! Contract, enforced by `tests/playground/test_kernel_parity.py`:
//!
//! * Every arithmetic expression is written in the **same association
//!   order** as the Python reference, so IEEE-754 double results are
//!   bit-identical, not merely close. Reordering an expression here is a
//!   behaviour change even when it is algebraically equivalent.
//! * The kernel is pure: no allocation beyond caller-provided buffers on
//!   the FFI path, no I/O, no clock, no randomness.
//! * The FFI entry point is fail-closed: null pointers, absurd lengths,
//!   non-finite prices, or a full trade buffer return a negative status
//!   and write nothing.
//!
//! Authority: none. The kernel computes an equity curve and trades from
//! numbers it is handed; venue access, risk gates, and governance live
//! in Python and are not reachable from here.

use core::slice;

/// Position intent for a bar: `1` opens, `-1` closes, `0` holds.
pub type Intent = i8;

/// One completed round trip, indices into the input window.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Trade {
    pub entry_bar: usize,
    pub exit_bar: usize,
    pub entry_price: f64,
    pub exit_price: f64,
    pub pnl_quote: f64,
    pub cost_quote: f64,
}

/// Simulation inputs; all slices must share the same length.
#[derive(Debug, Clone, Copy)]
pub struct SimInputs<'a> {
    pub intent: &'a [Intent],
    pub open: &'a [f64],
    pub close: &'a [f64],
    pub spread_bps: &'a [f64],
    pub initial_capital: f64,
    pub fee_bps: f64,
    pub position_fraction: f64,
}

/// Simulation outputs mirroring the Python reference's returns.
#[derive(Debug, Clone, PartialEq)]
pub struct SimOutputs {
    pub equity: Vec<f64>,
    pub trades: Vec<Trade>,
    /// Bars held with a non-zero position (the reference's `exposure` numerator).
    pub bars_in_position: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SimError {
    LengthMismatch,
    EmptyWindow,
    BadCapital,
    BadFee,
    BadFraction,
    NonFinitePrice,
}

impl SimError {
    /// Stable negative status codes for the C ABI.
    pub const fn code(self) -> i64 {
        match self {
            SimError::LengthMismatch => -1,
            SimError::EmptyWindow => -2,
            SimError::BadCapital => -3,
            SimError::BadFee => -4,
            SimError::BadFraction => -5,
            SimError::NonFinitePrice => -6,
        }
    }
}

#[inline]
fn cost_rate(fee_bps: f64, spread_bps: f64) -> f64 {
    // Mirrors: (spec.fee_bps + spread[bar] / 2.0) / 10_000.0
    (fee_bps + spread_bps / 2.0) / 10_000.0
}

fn validate(inputs: &SimInputs<'_>) -> Result<usize, SimError> {
    let n = inputs.intent.len();
    if n == 0 {
        return Err(SimError::EmptyWindow);
    }
    if inputs.open.len() != n || inputs.close.len() != n || inputs.spread_bps.len() != n {
        return Err(SimError::LengthMismatch);
    }
    if !inputs.initial_capital.is_finite() || inputs.initial_capital <= 0.0 {
        return Err(SimError::BadCapital);
    }
    if !inputs.fee_bps.is_finite() || inputs.fee_bps < 0.0 {
        return Err(SimError::BadFee);
    }
    if !(inputs.position_fraction > 0.0 && inputs.position_fraction <= 1.0) {
        return Err(SimError::BadFraction);
    }
    for bar in 0..n {
        if !inputs.open[bar].is_finite()
            || !inputs.close[bar].is_finite()
            || !inputs.spread_bps[bar].is_finite()
            || inputs.open[bar] <= 0.0
        {
            return Err(SimError::NonFinitePrice);
        }
    }
    Ok(n)
}

/// Run one deterministic simulation.
///
/// Expression order is load-bearing: it matches the Python reference
/// statement for statement so both produce identical doubles.
pub fn simulate(inputs: SimInputs<'_>) -> Result<SimOutputs, SimError> {
    let n = validate(&inputs)?;

    let mut cash = inputs.initial_capital;
    let mut units = 0.0f64;
    let mut entry_price = 0.0f64;
    let mut entry_bar: usize = 0;
    let mut entry_cost = 0.0f64;
    let mut pending_entry = false;
    let mut pending_exit = false;
    let mut bars_in_position: usize = 0;

    let mut equity = vec![0.0f64; n];
    let mut trades: Vec<Trade> = Vec::new();

    // The bar index addresses five parallel arrays and is itself recorded on
    // every trade (`entry_bar`/`exit_bar`), and the loop deliberately mirrors
    // the Python reference's `for bar in range(n)` statement for statement.
    // An iterator rewrite would hide both properties.
    #[allow(clippy::needless_range_loop)]
    for bar in 0..n {
        if pending_entry {
            let price = inputs.open[bar];
            let notional = cash * inputs.position_fraction;
            let cost = notional * cost_rate(inputs.fee_bps, inputs.spread_bps[bar]);
            units = (notional - cost) / price;
            cash -= notional;
            entry_price = price;
            entry_cost = cost;
            entry_bar = bar;
            pending_entry = false;
        } else if pending_exit && units > 0.0 {
            let price = inputs.open[bar];
            let proceeds = units * price;
            let cost = proceeds * cost_rate(inputs.fee_bps, inputs.spread_bps[bar]);
            let pnl = (units * (price - entry_price)) - cost - entry_cost;
            cash += proceeds - cost;
            trades.push(Trade {
                entry_bar,
                exit_bar: bar,
                entry_price,
                exit_price: price,
                pnl_quote: pnl,
                cost_quote: cost + entry_cost,
            });
            units = 0.0;
            pending_exit = false;
        }

        // Decide from this bar's signal for the NEXT bar's open (no lookahead).
        if bar + 1 < n {
            if units == 0.0 && !pending_entry && inputs.intent[bar] == 1 {
                pending_entry = true;
            } else if units > 0.0 && !pending_exit && inputs.intent[bar] == -1 {
                pending_exit = true;
            }
        }

        equity[bar] = cash + units * inputs.close[bar];
        if units > 0.0 {
            bars_in_position += 1;
        }
    }

    if units > 0.0 {
        // Force-close at the final close (mark-to-close), as the reference does.
        let price = inputs.close[n - 1];
        let proceeds = units * price;
        let cost = proceeds * cost_rate(inputs.fee_bps, inputs.spread_bps[n - 1]);
        let pnl = (units * (price - entry_price)) - cost - entry_cost;
        cash += proceeds - cost;
        trades.push(Trade {
            entry_bar,
            exit_bar: n - 1,
            entry_price,
            exit_price: price,
            pnl_quote: pnl,
            cost_quote: cost + entry_cost,
        });
        equity[n - 1] = cash;
    }

    Ok(SimOutputs {
        equity,
        trades,
        bars_in_position,
    })
}

// ---------------------------------------------------------------------------
// C ABI — consumed by trading_gym/playground/kernel.py through ctypes.
// ---------------------------------------------------------------------------

/// Fill caller-owned buffers with one simulation's results.
///
/// Returns the number of trades written (`>= 0`), or a negative
/// [`SimError::code`] on any validation failure. On failure nothing is
/// written. `trade_capacity` bounds the trade arrays; exceeding it is an
/// error (`-7`) rather than a silent truncation.
///
/// # Safety
/// Every pointer must be non-null and point to at least `n` (or
/// `trade_capacity`) readable/writable `f64`/`i8`/`u64` elements.
#[no_mangle]
pub unsafe extern "C" fn axiom_simulate(
    intent: *const i8,
    open: *const f64,
    close: *const f64,
    spread_bps: *const f64,
    n: u64,
    initial_capital: f64,
    fee_bps: f64,
    position_fraction: f64,
    out_equity: *mut f64,
    out_entry_bar: *mut u64,
    out_exit_bar: *mut u64,
    out_entry_price: *mut f64,
    out_exit_price: *mut f64,
    out_pnl: *mut f64,
    out_cost: *mut f64,
    trade_capacity: u64,
    out_bars_in_position: *mut u64,
) -> i64 {
    if intent.is_null()
        || open.is_null()
        || close.is_null()
        || spread_bps.is_null()
        || out_equity.is_null()
        || out_entry_bar.is_null()
        || out_exit_bar.is_null()
        || out_entry_price.is_null()
        || out_exit_price.is_null()
        || out_pnl.is_null()
        || out_cost.is_null()
        || out_bars_in_position.is_null()
    {
        return -8;
    }
    if n == 0 || n > (isize::MAX as u64) {
        return SimError::EmptyWindow.code();
    }
    let len = n as usize;
    let inputs = SimInputs {
        intent: slice::from_raw_parts(intent, len),
        open: slice::from_raw_parts(open, len),
        close: slice::from_raw_parts(close, len),
        spread_bps: slice::from_raw_parts(spread_bps, len),
        initial_capital,
        fee_bps,
        position_fraction,
    };
    let outputs = match simulate(inputs) {
        Ok(value) => value,
        Err(error) => return error.code(),
    };
    if outputs.trades.len() as u64 > trade_capacity {
        return -7;
    }

    let equity_out = slice::from_raw_parts_mut(out_equity, len);
    equity_out.copy_from_slice(&outputs.equity);

    let capacity = trade_capacity as usize;
    let entry_bar_out = slice::from_raw_parts_mut(out_entry_bar, capacity);
    let exit_bar_out = slice::from_raw_parts_mut(out_exit_bar, capacity);
    let entry_price_out = slice::from_raw_parts_mut(out_entry_price, capacity);
    let exit_price_out = slice::from_raw_parts_mut(out_exit_price, capacity);
    let pnl_out = slice::from_raw_parts_mut(out_pnl, capacity);
    let cost_out = slice::from_raw_parts_mut(out_cost, capacity);
    for (slot, trade) in outputs.trades.iter().enumerate() {
        entry_bar_out[slot] = trade.entry_bar as u64;
        exit_bar_out[slot] = trade.exit_bar as u64;
        entry_price_out[slot] = trade.entry_price;
        exit_price_out[slot] = trade.exit_price;
        pnl_out[slot] = trade.pnl_quote;
        cost_out[slot] = trade.cost_quote;
    }
    *out_bars_in_position = outputs.bars_in_position as u64;
    outputs.trades.len() as i64
}

/// ABI version; Python refuses to load a kernel it does not understand.
#[no_mangle]
pub extern "C" fn axiom_kernel_abi_version() -> u32 {
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flat_window(n: usize) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
        (vec![100.0; n], vec![100.0; n], vec![0.0; n])
    }

    #[test]
    fn empty_window_is_rejected() {
        let inputs = SimInputs {
            intent: &[],
            open: &[],
            close: &[],
            spread_bps: &[],
            initial_capital: 1000.0,
            fee_bps: 0.0,
            position_fraction: 1.0,
        };
        assert_eq!(simulate(inputs), Err(SimError::EmptyWindow));
    }

    #[test]
    fn length_mismatch_is_rejected() {
        let inputs = SimInputs {
            intent: &[0, 0],
            open: &[1.0, 1.0],
            close: &[1.0],
            spread_bps: &[0.0, 0.0],
            initial_capital: 1000.0,
            fee_bps: 0.0,
            position_fraction: 1.0,
        };
        assert_eq!(simulate(inputs), Err(SimError::LengthMismatch));
    }

    #[test]
    fn zero_cost_flat_market_preserves_capital() {
        let (open, close, spread) = flat_window(6);
        let inputs = SimInputs {
            intent: &[1, 0, 0, -1, 0, 0],
            open: &open,
            close: &close,
            spread_bps: &spread,
            initial_capital: 1_000.0,
            fee_bps: 0.0,
            position_fraction: 1.0,
        };
        let out = simulate(inputs).expect("simulation");
        assert_eq!(out.trades.len(), 1);
        assert_eq!(out.equity[5], 1_000.0);
        assert_eq!(out.trades[0].pnl_quote, 0.0);
        assert_eq!(out.trades[0].cost_quote, 0.0);
    }

    #[test]
    fn fill_happens_at_the_next_open_never_the_signal_bar() {
        let open = vec![10.0, 20.0, 20.0, 20.0];
        let close = vec![10.0, 20.0, 20.0, 20.0];
        let spread = vec![0.0; 4];
        let inputs = SimInputs {
            intent: &[1, 0, 0, 0],
            open: &open,
            close: &close,
            spread_bps: &spread,
            initial_capital: 100.0,
            fee_bps: 0.0,
            position_fraction: 1.0,
        };
        let out = simulate(inputs).expect("simulation");
        // Entry filled at bar 1's open (20.0), not bar 0's (10.0).
        assert_eq!(out.trades.len(), 1);
        assert_eq!(out.trades[0].entry_bar, 1);
        assert_eq!(out.trades[0].entry_price, 20.0);
    }

    #[test]
    fn costs_are_charged_on_both_sides() {
        let (open, close, spread) = flat_window(5);
        let inputs = SimInputs {
            intent: &[1, 0, -1, 0, 0],
            open: &open,
            close: &close,
            spread_bps: &spread,
            initial_capital: 1_000.0,
            fee_bps: 10.0,
            position_fraction: 1.0,
        };
        let out = simulate(inputs).expect("simulation");
        assert_eq!(out.trades.len(), 1);
        assert!(out.trades[0].cost_quote > 0.0);
        assert!(out.equity[4] < 1_000.0);
        assert!(out.trades[0].pnl_quote < 0.0);
    }

    #[test]
    fn open_position_is_force_closed_at_the_final_close() {
        let open = vec![100.0, 100.0, 100.0];
        let close = vec![100.0, 100.0, 110.0];
        let spread = vec![0.0; 3];
        let inputs = SimInputs {
            intent: &[1, 0, 0],
            open: &open,
            close: &close,
            spread_bps: &spread,
            initial_capital: 1_000.0,
            fee_bps: 0.0,
            position_fraction: 1.0,
        };
        let out = simulate(inputs).expect("simulation");
        assert_eq!(out.trades.len(), 1);
        assert_eq!(out.trades[0].exit_bar, 2);
        assert_eq!(out.trades[0].exit_price, 110.0);
        assert_eq!(out.equity[2], 1_100.0);
    }

    #[test]
    fn abi_version_is_pinned() {
        assert_eq!(axiom_kernel_abi_version(), 1);
    }
}
