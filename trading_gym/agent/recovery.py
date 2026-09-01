from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class RetryDecision: retry: bool; reconcile_first: bool; reason: str
TRANSIENT={'timeout','rate_limit','temporarily_unavailable','connection_reset'}
AMBIGUOUS={'timeout_after_send','unknown_commit'}
def classify_agent_error(error):
    text=str(error).lower()
    if any(x in text for x in ('rate limit','429')): return 'rate_limit'
    if any(x in text for x in ('timeout','timed out')): return 'timeout'
    if any(x in text for x in ('auth','401','403')): return 'authentication'
    return 'permanent'
def decide_retry(error, *, attempt=0, max_attempts=3, idempotent=True, ambiguous=False):
    kind=error if isinstance(error,str) and error in TRANSIENT|AMBIGUOUS else classify_agent_error(error)
    if ambiguous or kind in AMBIGUOUS:
        return RetryDecision(False, True, 'ambiguous_side_effect_requires_reconciliation')
    if not idempotent: return RetryDecision(False, False, 'non_idempotent')
    if kind not in TRANSIENT: return RetryDecision(False, False, 'permanent')
    if attempt>=max_attempts: return RetryDecision(False, False, 'retry_budget_exhausted')
    return RetryDecision(True, False, kind)
