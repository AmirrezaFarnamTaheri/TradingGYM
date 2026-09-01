class SkillQuarantineRepository:
    def __init__(self): self._items={}
    def admit(self, name, record, *, reviewer, preparer):
        if reviewer==preparer: raise ValueError('role separation required')
        if record.get('risk_markers'): raise ValueError('risky package cannot activate')
        self._items[name]={**record,'reviewer':reviewer,'activated':False}; return self._items[name]
    def list(self): return dict(self._items)
