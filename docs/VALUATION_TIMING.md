# Historical valuation timing and model compatibility

The historical environment identifies its transition semantics as
`next-valuation.v1` inside the model bundle's observation contract. An action
chosen from completed market row `t` belongs to the holding interval ending at
row `t + 1`. The returned observation, equity, reward and risk state all use that
ending valuation.

## One transition

1. The observation contains the admitted, frozen-normalized features for row
   `t` and an account already marked at that row's price.
2. The target-leverage action executes against row `t`'s recorded mid. When
   independent contemporaneous quotes are unavailable, this remains a modeled
   close-price proxy with configured costs, not an observed executable quote.
3. The resulting inventory is marked to row `t + 1`. Financing uses the actual
   positive difference between the two market timestamps and the configured
   compound daily rate. A missing interval therefore increases elapsed financing;
   the legacy `periods_per_day` count does not override the market clock.
4. The next row's OHLC envelope supplies the bar-level margin scenario. The
   current completed row's high and low cannot retrospectively liquidate inventory
   opened by the new action. The environment applies financing before this risk
   scenario, then checks ending margin and circuit-breaker conditions.
5. Reward is the configured bounded log change from the pre-action equity to
   this settled equity, including execution costs, financing and any stop-out.
   The next observation's account features use the same settled account.

This ordering is an explicit simulation convention. OHLC bars do not reveal
intrabar event order, the exact time of a liquidation, or the overlap caused by
the feature recipe's two-second publication delay. The contract records these
limitations. No change here qualifies a bar-close proxy as a venue fill.

## Episode boundaries and comparison

An episode with one decision needs two available rows. Its final row is marked
and financed before truncation, so the last action receives its holding-period
return. Repeated calls after completion return no additional reward and do not
mutate the account. A risk termination on the final interval remains a risk
termination rather than being relabeled as horizon truncation.

Ordinary horizon truncation leaves any open inventory marked at the last row;
it does not add an assumed closing transaction or closing fee. Replay records
decision indices separately from valuation indices. Simulator equity charts
include the initial account and every ending valuation; position-change markers
describe settled position changes, including stop-outs, rather than precise
venue execution times. Periodic annualized metrics require a regular clock
including the final valuation interval. Irregular tapes can be settled by the
environment, but must be explicitly resampled before these periodic metrics are
reported.

Native stochastic baselines own an independent NumPy generator. Evaluation
derives its seed deterministically from the configured experiment seed, separately
from the environment's episode-start generator. Global NumPy randomness and the
order in which other baselines run do not control their actions.

## Existing model bundles

Bundles produced under the former delayed-account transition are incompatible.
Their missing or different transition identity fails strict configuration
admission before evaluation or simulation. The old policy was optimized against
a different reward-to-action relationship and account state, even when the
feature columns and network dimensions match.

Retrain the policy under this timing contract and produce a new bundle using
preprocessing fitted only to the admitted training partition. A previous
normalizer may be reused only when its feature recipe, fitted data and other
admission conditions remain valid; changing the old policy's manifest alone does
not qualify that policy. Recompute selection scores under the revised contract
and preserve the separation of training, selection and untouched final-test
evidence.
