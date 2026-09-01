from decimal import Decimal
from trading_gym.agent.recovery import decide_retry
from trading_gym.agent.federation import intersect_permissions,RemoteServerDescriptor,to_gateway_manifest
from trading_gym.market.orderbook import OrderBookSnapshot
from trading_gym.adapters.orderbook_integrity import OrderBookIntegrityGate
from trading_gym.risk.analytics import tail_risk
from trading_gym.economic.payment_rails import PaymentRailPolicy,normalise_usdc_transfer
from trading_gym.accounting.controls import assess_journal_entry
from trading_gym.institutional_os.peer_wave17 import evaluate_wave17_catalog_saturation
from trading_gym.institutional_os.peer_wave18 import evaluate_wave18_catalog_saturation
from axiomforge.next_level.agent_lab import regime_votes
from axiomforge.decision_lifecycle import DecisionRecord,transition

def test_retry_is_fail_closed_for_ambiguous_non_idempotent():
    d=decide_retry('timeout',ambiguous=True,idempotent=False); assert not d.retry and d.reconcile_first

def test_permissions_never_expand(): assert intersect_permissions(['read','trade.execute'],['read','trade.execute'])==['read']
def test_remote_metadata_does_not_admit_network():
    try: RemoteServerDescriptor.model_validate({'name':'x','endpoint':'https://example.com'})
    except ValueError: pass
    else: raise AssertionError

def test_orderbook_gap_requires_snapshot():
    g=OrderBookIntegrityGate(); g.apply_snapshot(4,[(99,1)],[(100,1)])
    try:g.apply_delta(6,[(99,1)],[(100,1)])
    except ValueError: pass
    else: raise AssertionError

def test_tail_risk_is_advisory(): assert tail_risk([-0.1,0,0.1])['authority_granted'] is False
def test_payment_policy_cannot_grant_execution():
    try: normalise_usdc_transfer({'amount':'1','recipient':'0xabc'},PaymentRailPolicy(allow_execution=True))
    except ValueError: pass
    else: raise AssertionError

def test_accounting_exact_balance_and_separation():
    r=assess_journal_entry({'debits':[{'amount':'1.1'}],'credits':[{'amount':'1.1'}],'preparer':'a','reviewer':'b','evidence':['x']}); assert r['ready_for_human_review']
def test_wave17_surface_matrix_is_recovered(): assert evaluate_wave17_catalog_saturation()['ready_for_human_review']
def test_wave18_missing_surface_matrix_fails_closed():
    r=evaluate_wave18_catalog_saturation(); assert not r['ready_for_human_review'] and 'surface_index_incomplete' in r['blockers']
def test_regime_votes_always_have_rationale(): assert all(v.rationale for v in regime_votes({'trend':1,'breadth':-1,'volatility':2}))
def test_decision_approval_requires_independent_actor():
    r=DecisionRecord('d','c',actor='prep'); r=transition(r,'reviewed',expected_revision=0,actor='reviewer'); r=transition(r,'approved',expected_revision=1,actor='approver'); assert r.state=='approved'
