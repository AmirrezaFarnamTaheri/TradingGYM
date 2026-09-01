from __future__ import annotations
def evaluate_harness_component(evidence):
    before=float(evidence.get('baseline_metric',0)); after=float(evidence.get('candidate_metric',0)); cost=float(evidence.get('operational_cost',0))
    improvement=after-before
    return {'retain': improvement>0 and improvement>cost, 'improvement':improvement, 'operational_cost':cost, 'executed':False}
