from __future__ import annotations
from dataclasses import dataclass
import re
SAFE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
FORBIDDEN={'*','wallet.sign','wallet.private_key','payment.send','trade.execute','shell.exec'}
def safe_tool_identifier(name:str)->str:
    value=re.sub(r'[^A-Za-z0-9_.-]+','-',name.strip()).strip('-')[:80]
    if not value or not SAFE.fullmatch(value): raise ValueError('unsafe tool name')
    return value
def intersect_permissions(requested,allowed):
    req=set(requested); allow=set(allowed)
    if '*' in req or req & FORBIDDEN: return sorted((req&allow)-FORBIDDEN)
    return sorted(req&allow)
@dataclass(frozen=True)
class RemoteServerDescriptor:
    name:str; tools:tuple[str,...]=(); endpoint:str='local-metadata-only'
    @classmethod
    def model_validate(cls,v):
        if isinstance(v,cls): return v
        name=safe_tool_identifier(str(v['name']))
        endpoint=str(v.get('endpoint','local-metadata-only'))
        if endpoint.startswith(('http://','https://')): raise ValueError('remote endpoint not admitted by metadata intake')
        return cls(name,tuple(safe_tool_identifier(x) for x in v.get('tools',())),endpoint)
def to_gateway_manifest(desc):
    d=RemoteServerDescriptor.model_validate(desc)
    return {'name':d.name,'tools':list(d.tools),'endpoint':d.endpoint,'executable':False,'authority':'none'}
