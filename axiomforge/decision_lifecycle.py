from dataclasses import dataclass,replace
ALLOWED={'proposed':{'reviewed','cancelled'},'reviewed':{'approved','rejected','cancelled'},'approved':{'executed','expired','cancelled'},'rejected':set(),'executed':set(),'expired':set(),'cancelled':set()}
@dataclass(frozen=True)
class DecisionRecord: decision_id:str; capability:str; state:str='proposed'; revision:int=0; actor:str='system'
def transition(record, new_state, *, expected_revision, actor):
    if record.revision!=expected_revision: raise RuntimeError('revision_conflict')
    if new_state not in ALLOWED.get(record.state,set()): raise ValueError('invalid_transition')
    if actor==record.actor and new_state=='approved': raise PermissionError('independent_approval_required')
    return replace(record,state=new_state,revision=record.revision+1,actor=actor)
