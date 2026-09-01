from dataclasses import dataclass
@dataclass(frozen=True)
class NativeCryptoManifest: source_sha256:str; compiler:str|None=None; vectors_executed:int=0; built:bool=False
def evaluate_crypto_evidence(evidence):
    m=evidence if isinstance(evidence,NativeCryptoManifest) else NativeCryptoManifest(**evidence)
    blockers=[]
    if m.built and not m.compiler: blockers.append('compiler_required')
    if m.vectors_executed<=0: blockers.append('no_executed_vectors')
    return {'blockers':blockers,'ready_for_human_review':not blockers,'constant_time_claim':False,'audit_claim':False}
