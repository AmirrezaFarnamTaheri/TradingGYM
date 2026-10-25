"""In-process, non-executable quarantine for human-reviewed skill metadata."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re

from trading_gym.agent.skills.static_assurance import assess_skill_static

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PRINCIPAL = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_MAX_RECORDS = 1024


class SkillQuarantineRepository:
    def __init__(self):
        self._items = {}

    def admit(self, name, record, *, reviewer, preparer):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("invalid skill name")
        for principal in (reviewer, preparer):
            if (
                not isinstance(principal, str)
                or not _PRINCIPAL.fullmatch(principal.lower())
                or principal != principal.strip()
            ):
                raise ValueError("invalid review principal")
        if reviewer.casefold() == preparer.casefold():
            raise ValueError("role separation required")
        if not isinstance(record, Mapping):
            raise ValueError("invalid skill record")
        if record.get("risk_markers"):
            raise ValueError("risky package cannot activate")
        assurance = assess_skill_static(record)
        if assurance["blockers"]:
            raise ValueError("invalid quarantined skill metadata")
        if name in self._items:
            raise ValueError("duplicate skill identity")
        if len(self._items) >= _MAX_RECORDS:
            raise ValueError("skill quarantine capacity exceeded")
        result = deepcopy(
            {
                **record,
                "reviewer": reviewer,
                "preparer": preparer,
                "quarantined": True,
                "activated": False,
            }
        )
        self._items[name] = result
        return deepcopy(result)

    def list(self):
        return deepcopy(self._items)
