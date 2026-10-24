"""Forward-only context recorder and opt-in restriction experiment.

Advice is recorded against an existing decision identity. The default mode
never changes allocation. Restriction mode can only narrow its supplied
candidate and never grants permission. Expiry/revocation blocks that mode;
it does not restore a previous risk-off multiplier to a neutral multiplier.
"""
from dataclasses import asdict, dataclass, replace
import math
from typing import Literal

from trading_gym.intelligence.decision_journal import DecisionJournal, _id, _time, _SHA
from trading_gym.intelligence.types import ActionKind, DecisionSignal, InvalidDecisionSignal, UncertainDecision


@dataclass(frozen=True)
class ContextAdvice:
    advice_id: str
    source_id: str
    model_id: str
    scope_id: str
    input_sha256: str
    input_cutoff_at: float
    received_at: float
    expires_at: float
    max_abs_leverage: float
    allow_long: bool = True
    allow_short: bool = True

    def __post_init__(self):
        for value in (self.advice_id,self.source_id,self.model_id,self.scope_id): _id(value)
        if not isinstance(self.input_sha256,str) or not _SHA.fullmatch(self.input_sha256):
            raise ValueError("advice requires an input digest")
        cutoff,received,expiry=(_time(x) for x in (self.input_cutoff_at,self.received_at,self.expires_at))
        if not cutoff<=received<expiry:
            raise ValueError("advice availability/expiry chronology is invalid")
        if type(self.max_abs_leverage) not in (int,float) or not math.isfinite(self.max_abs_leverage) or not 0<=self.max_abs_leverage<=20:
            raise ValueError("advice cap must be finite within [0,20]")
        if type(self.allow_long) is not bool or type(self.allow_short) is not bool:
            raise ValueError("direction restrictions must be explicit booleans")


@dataclass(frozen=True)
class ContextIntervention:
    signal: DecisionSignal
    advice_id: str | None
    applied: bool
    mode: str


class ContextAdviceCache:
    def __init__(self, journal: DecisionJournal): self.journal=journal

    def record(self, *, decision_id: str, advice: ContextAdvice) -> bool:
        return self.journal.append_event(decision_id=decision_id,event_id=f"advice:{advice.advice_id}",
            kind="advice",occurred_at=advice.received_at,payload=asdict(advice))

    def revoke(self, *, decision_id: str, advice_id: str, revoked_at: float) -> bool:
        _id(advice_id)
        return self.journal.append_event(decision_id=decision_id,event_id=f"revoke:{advice_id}",
            kind="advice_revocation",occurred_at=_time(revoked_at),payload={"advice_id":advice_id})

    def latest(self, *, source_id: str, scope_id: str, as_of: float) -> ContextAdvice:
        _id(source_id);_id(scope_id);_time(as_of)
        candidates=[]
        for event in self.journal.events(kind="advice",as_of=as_of):
            advice=ContextAdvice(**event["payload"])
            if advice.source_id==source_id and advice.scope_id==scope_id:
                candidates.append(advice)
        if not candidates:raise UncertainDecision("no available context advice")
        latest=max(candidates,key=lambda a:(a.received_at,a.advice_id))
        if any(a.received_at==latest.received_at and a!=latest for a in candidates):
            raise InvalidDecisionSignal("ambiguous simultaneous context advice")
        revoked={e["payload"]["advice_id"] for e in self.journal.events(kind="advice_revocation",as_of=as_of)}
        if latest.advice_id in revoked or not latest.input_cutoff_at<=latest.received_at<=as_of<latest.expires_at:
            # Do not resurrect an older recommendation after a newer one
            # expires or is revoked. Operator policy must resolve the loss.
            raise UncertainDecision("context advice expired or revoked")
        return latest

    def evaluate(self, candidate: DecisionSignal, *, source_id: str, scope_id: str,
                 as_of: float, mode: Literal["record_only","restriction_only"]="record_only") -> ContextIntervention:
        if mode not in {"record_only","restriction_only"}:raise ValueError("unknown intervention stage")
        if mode=="record_only":
            return ContextIntervention(candidate,None,False,mode)
        advice=self.latest(source_id=source_id,scope_id=scope_id,as_of=as_of)
        if (candidate.action_kind is ActionKind.LONG and candidate.target_leverage<=0) or (candidate.action_kind is ActionKind.SHORT and candidate.target_leverage>=0) or (candidate.action_kind is ActionKind.HOLD and candidate.target_leverage!=0):
            raise InvalidDecisionSignal("incoherent context-intervention candidate")
        magnitude=min(abs(candidate.target_leverage),advice.max_abs_leverage)
        if (candidate.action_kind is ActionKind.LONG and not advice.allow_long) or (candidate.action_kind is ActionKind.SHORT and not advice.allow_short):
            magnitude=0.0
        target=math.copysign(magnitude,candidate.target_leverage) if magnitude else 0.0
        action=ActionKind.HOLD if not target else candidate.action_kind
        signal=replace(candidate,target_leverage=target,action_kind=action,
            rationale=f"[context restriction {advice.advice_id}] {candidate.rationale}"[:200])
        return ContextIntervention(signal,advice.advice_id,True,mode)
