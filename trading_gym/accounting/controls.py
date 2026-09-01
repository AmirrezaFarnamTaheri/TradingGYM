from decimal import Decimal
import hashlib,random
def assess_journal_entry(entry):
    deb=sum((Decimal(str(x['amount'])) for x in entry.get('debits',())),Decimal(0)); cre=sum((Decimal(str(x['amount'])) for x in entry.get('credits',())),Decimal(0))
    blockers=[]
    if deb!=cre: blockers.append('unbalanced')
    if entry.get('preparer')==entry.get('reviewer'): blockers.append('reviewer_not_independent')
    if not entry.get('evidence'): blockers.append('missing_evidence')
    return {'blockers':blockers,'ready_for_human_review':not blockers,'posted':False}
def assess_reconciliation(data):
    residual=Decimal(str(data.get('source_total',0)))-Decimal(str(data.get('ledger_total',0)))
    return {'residual':str(residual),'balanced':residual==0,'mutation_performed':False}
def assess_close_plan(plan):
    deps={x['id']:set(x.get('depends_on',())) for x in plan.get('tasks',())}; blockers=[]
    for k,v in deps.items():
        if not v<=deps.keys(): blockers.append(f'unknown_dependency:{k}')
    return {'blockers':blockers,'ready_for_human_review':not blockers,'closed':False}
def select_control_sample(items, *, size, seed=0):
    vals=list(items); rnd=random.Random(seed); idx=list(range(len(vals))); rnd.shuffle(idx); return [vals[i] for i in sorted(idx[:min(size,len(vals))])]
