from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import hashlib,re
@dataclass(frozen=True)
class PaymentRailPolicy:
    allow_secret_resolution:bool=False; allow_seed_persistence:bool=False; allow_execution:bool=False
@dataclass(frozen=True)
class PaymentOperation:
    kind:str; amount:str; recipient:str=''; batch_id:str=''; callback:str=''; expiry:str=''; executed:bool=False
def _safe(policy):
    if policy and (policy.allow_secret_resolution or policy.allow_seed_persistence or policy.allow_execution): raise ValueError('authority expanding payment policy rejected')
def normalise_usdc_transfer(intent, policy=PaymentRailPolicy()):
    _safe(policy); amount=Decimal(str(intent['amount']));
    if amount<=0: raise ValueError('amount')
    if any(k in intent for k in ('mnemonic','private_key','seed')): raise ValueError('secret material')
    return PaymentOperation('usdc_transfer',str(amount),str(intent['recipient']),expiry=str(intent.get('expiry','')))
def normalise_lightning_invoice(invoice, *, amount=None, expiry=''):
    if not re.match(r'(?i)^ln[a-z0-9]{8,}$',invoice): raise ValueError('invalid bolt11 syntax')
    return {'invoice_sha256':hashlib.sha256(invoice.encode()).hexdigest(),'amount':None if amount is None else str(Decimal(str(amount))),'expiry':expiry,'paid':False}
def evaluate_payment_rail(evidence, policy=PaymentRailPolicy()):
    _safe(policy); return {'evidence_sha256':hashlib.sha256(repr(sorted(evidence.items())).encode()).hexdigest(),'ready_for_human_review':True,'executed':False,'signed':False}
