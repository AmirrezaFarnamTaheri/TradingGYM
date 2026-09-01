from __future__ import annotations
from dataclasses import dataclass
import hashlib,json
@dataclass(frozen=True)
class IntegrationReport:
    integration_id:str; blockers:tuple[str,...]; evidence_sha256:str; ready:bool; authority_granted:bool=False
    def model_dump(self): return {'integration_id':self.integration_id,'blockers':list(self.blockers),'evidence_sha256':self.evidence_sha256,'ready':self.ready,'authority_granted':False}
def assess(name,evidence,required=()):
    data=dict(evidence); blockers=[f'missing:{k}' for k in required if data.get(k) in (None,'',[],{})]
    for k,v in data.items():
        if any(x in k.lower() for x in ('execute','trade','sign','mutate','network')) and v is True: blockers.append(f'forbidden_authority:{k}')
    dig=hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest(); return IntegrationReport(name,tuple(sorted(set(blockers))),dig,not blockers)
