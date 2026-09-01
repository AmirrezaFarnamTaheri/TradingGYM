from dataclasses import dataclass
from datetime import datetime,timezone
@dataclass(frozen=True)
class LiveConsent: actor:str; scope:str; expires_at:str; nonce:str
def validate_live_consent(consent, *, actor, scope, now=None):
    c=consent if isinstance(consent,LiveConsent) else LiveConsent(**consent); now=now or datetime.now(timezone.utc)
    exp=datetime.fromisoformat(c.expires_at.replace('Z','+00:00'))
    if c.actor!=actor or c.scope!=scope or exp<=now or not c.nonce: raise PermissionError('invalid live consent')
    return True
