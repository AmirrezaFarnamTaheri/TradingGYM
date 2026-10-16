# ADR-046 — Venue-local settlement dates and as-of evidence integrity

**Date:** 2026-10-15  
**Branch:** `audit/2026-10-11-execution-ci-hardening`, draft PR #2  
**Safety:** non-mutating, analytical settlement model; no brokerage,
cash-posting, margin or execution authority added.

## Source-level finding

TradingGYM's `trading_gym/accounting/settlement.py` computes each
obligation's contractual settlement date in its market calendar timezone,
but previously compared it with the *UTC calendar date* at report as-of.
That mixes incompatible date bases and causes both false overdue
blocks and missed overdue items.

Two reproduced examples with contractual due date July 7, 2026:

- **New York:** at July 8, 01:00 UTC, the exchange-local date is
  still July 7 (21:00 EDT), so a pending obligation is **not**
  overdue. Previously reported overdue=true.
- **Tokyo:** at July 7, 18:00 UTC, the exchange-local date is already
  July 8 (03:00 JST), so the pending item **is** overdue.
  Previously reported overdue=false.

At one as-of instant (July 8, 01:00 UTC), New York and Tokyo must
report different statuses for otherwise identical July 7 due dates.
A global UTC date cannot represent both.

### Additional hindsight leak

The evaluator also accepted source obligations where `trade_at > as_of`,
subtracting the future trade's cash payable from historical available
cash. A `SETTLED` obligation with a confirmation timestamp later than
the report `as_of` could similarly make historical pending exposure
disappear prematurely. Finally, a confirmation was allowed before its
trade timestamp.

## Implemented fix

- Move `as_of_date` calculation *inside* the obligation loop:
  `as_of.astimezone(ZoneInfo(item.convention.calendar.timezone_name)).date()`.
  Compute `days_overdue` against the same venue-local date.
- Require `trade_at <= as_of` for all analytical obligations.
- Require `observed_settlement_at <= as_of` when a confirmation exists.
- Require settlement confirmation at/after its trade timestamp in the
  validated obligation model.
- Preserve the original UTC-normalized report timestamp, observed
  business-day/holiday convention and payable cash accounting.
- Preserve `settlement_authority_granted=False` and
  `mutation_performed=False`. No automatic posting or settlement action.

## RED-to-GREEN evidence

The original production file was reproduced byte-for-byte in a local
Python 3.13 source mirror. Its original Git blob
`3c0cadd582d8df7de7f968ad5b8573fd45c24a75` matched GitHub.

- **RED 1:** four local timezone boundary cases: **3 failed,
  1 passed**, due to incorrect global UTC status.
- **GREEN 1:** **4/4** after per-obligation timezone correction.
- **RED 2:** added temporal-integrity cases: **3 failed, 4 passed**,
  allowing future trade, future settlement, and pre-trade confirmation.
- **GREEN 2:** **7/7** after timestamp and model checks.
- Four replicated existing settlement scenarios (business-day/holiday
  T+2, pending receivables/payables, failed/overdue/cash-shortfall
  blockers, settled-state/duplicate-id validation) passed.
- **18/18** portfolio + settlement focused tests passed.
- The complete available isolated source mirror passed **73/73** and
  `python -m compileall` returned exit code 0.

The pushed final source matched the locally tested version by Git blob
`564934e4b0c13611d62fa063e705cfaadbc74f52`.
Its generated manifest SHA-256 is
`822abdc53e77f4751feaa1677f3838031b83768f35cb815d3cb0a3dd021fdca6`.

The local mirror substituted a minimal `MarketCalendarSpec` Pydantic
object for the full calendar implementation. The added GitHub tests use
the real calendar's constructor fields, but **exact-head repository
integration and its full calendar parser have not been executed**.
Do not misrepresent isolated unit tests as final system or funded-live
qualification.

## Second-order accounting and financial authority

1. **Event-time and source plane:** contract dates and report as-of
   must use consistent venue calendars, with broker source hashes and
   independently witnessed observation timestamps.
2. **Settlement state reconstruction:** `FAILED` and `CANCELLED`
   states currently have no state-transition observation timestamp.
   Historical as-of backfills may still need a versioned event ledger
   to prevent hindsight leakage from those transitions; this fix
   closes only the timestamped trade and settled-event paths.
3. **Multi-currency cash:** settlement available cash is grouped by
   currency; FX conversion requires separately verified rate, time and
   pricing evidence and must not silently net unlike cash currencies.
4. **Plan-to-fill parity:** reconcile projected rebalance cost and
   actual fills/fee cashflows separately; market fill acceptance remains
   independent of the deterministic accounting model.
5. **Release:** exact-PR-head Python 3.11/3.12, frontend, Rust,
   OpenAPI/source manifests, actual exchange ledger readback and
   gross-flat recovery still block funded-live approval.

**Disposition:** keep PR #2 draft, unmerged and funded-live **NO-GO**.
