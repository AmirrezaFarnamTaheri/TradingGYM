from dataclasses import dataclass
from decimal import Decimal
import hashlib,json
@dataclass(frozen=True)
class TreasuryImportSnapshot: source_sha256:str; accounts:tuple; transactions:tuple
def snapshot_from_mapping(data):
    raw=json.dumps(data,sort_keys=True,default=str).encode(); return TreasuryImportSnapshot(hashlib.sha256(raw).hexdigest(),tuple(data.get('accounts',())),tuple(data.get('transactions',())))
def evaluate_treasury_import(snapshot):
    s=snapshot if isinstance(snapshot,TreasuryImportSnapshot) else snapshot_from_mapping(snapshot)
    ids=[str(x.get('external_id','')) for x in s.transactions]; blockers=[]
    if '' in ids: blockers.append('missing_external_id')
    if len(ids)!=len(set(ids)): blockers.append('duplicate_external_id')
    return {'blockers':blockers,'ready_for_human_review':not blockers,'mutation_performed':False,'funds_moved':False}
