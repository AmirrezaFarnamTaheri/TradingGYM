from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib,json
@dataclass(frozen=True)
class FinanceInsightBundle: claims:tuple; evidence:tuple
def evaluate_finance_bundle(bundle):
    b=bundle if isinstance(bundle,FinanceInsightBundle) else FinanceInsightBundle(tuple(bundle.get('claims',())),tuple(bundle.get('evidence',())))
    ids={e.get('id') for e in b.evidence}; blockers=[]
    for c in b.claims:
        if c.get('evidence_id') not in ids: blockers.append('ungrounded_claim')
    return {'blockers':sorted(set(blockers)),'ready_for_human_review':not blockers,'authority_granted':False}
