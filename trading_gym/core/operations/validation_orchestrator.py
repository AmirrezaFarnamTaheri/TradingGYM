"""GO/NO-GO validation orchestrator.

Absorbed pattern: nepse-quant-terminal `validation/run_all.py` — a typed
criteria table, per-phase error isolation (one crashed phase must not kill the
battery), and a machine-readable verdict.  Target-native port, generalised:
criteria are plain callables so any plane can compose gates without
importing this module's domain assumptions.

Advisory only: the verdict is evidence for humans and dashboards; it grants no
execution authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

VERDICT_GO = "GO"
VERDICT_NO_GO = "NO-GO"


@dataclass(frozen=True)
class PhaseReport:
    name: str
    passed: bool
    detail: str
    crashed: bool


@dataclass(frozen=True)
class Criterion:
    """A single gate: ``check`` returns True/False plus an observed value."""

    name: str
    check: Callable[[], tuple[bool, str]]

    def evaluate(self) -> tuple[bool, str]:
        return self.check()


@dataclass(frozen=True)
class ValidationPhase:
    """One runnable phase; may itself raise — the orchestrator isolates it."""

    name: str
    run: Callable[[], dict]
    criteria: tuple[Criterion, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OrchestratorVerdict:
    verdict: str  # GO | NO-GO
    phase_reports: tuple[PhaseReport, ...]
    failures: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        return 0 if self.verdict == VERDICT_GO else 1


def run_validation(
    phases: list[ValidationPhase],
) -> OrchestratorVerdict:
    """Run every phase in isolation and aggregate a GO/NO-GO verdict."""
    reports: list[PhaseReport] = []
    failures: list[str] = []

    for phase in phases:
        try:
            produced = phase.run() or {}
            if not isinstance(produced, dict):
                raise TypeError("phase run() must return a Mapping or None")
            crashed = False
            detail = f"ok:{sorted(produced)}" if produced else "ok"
        except Exception as exc:  # noqa: BLE001 - isolation boundary by design
            produced = {}
            crashed = True
            detail = f"{type(exc).__name__} at phase boundary"
            failures.append(f"{phase.name}:phase_crashed:{type(exc).__name__}")

        reports.append(
            PhaseReport(
                name=phase.name, passed=not crashed, detail=detail, crashed=crashed
            )
        )

        for criterion in phase.criteria:
            try:
                passed, observed = criterion.evaluate()
            except Exception as exc:  # noqa: BLE001
                passed, observed = False, f"criterion-raised:{type(exc).__name__}"
            if not passed:
                failures.append(f"{phase.name}:{criterion.name}:{observed}")
            if passed and not crashed:
                detail_note = f"{criterion.name}=pass({observed})"
                if reports[-1].detail == "ok":
                    reports[-1] = PhaseReport(
                        name=phase.name,
                        passed=True,
                        detail=detail_note,
                        crashed=False,
                    )

    # A crashed phase fails its own report but does not veto the whole run by
    # itself — only recorded *failures* (including its own) do that above.
    verdict = VERDICT_GO if not failures else VERDICT_NO_GO
    return OrchestratorVerdict(
        verdict=verdict,
        phase_reports=tuple(reports),
        failures=tuple(failures),
    )


def format_verdict(verdict: OrchestratorVerdict) -> str:
    """Human-readable one-page summary suitable for logs/evidence packs."""
    lines = [f"verdict={verdict.verdict}", f"failures={len(verdict.failures)}"]
    for phase in verdict.phase_reports:
        status = "PASS" if phase.passed else "FAIL"
        lines.append(f"  [{status}] {phase.name}: {phase.detail}")
    for failure in verdict.failures:
        lines.append(f"  ! {failure}")
    return "\n".join(lines)


def verdict_to_json(verdict: OrchestratorVerdict) -> str:
    """Machine-readable artifact for evidence packs."""
    return json.dumps(
        {
            "verdict": verdict.verdict,
            "exit_code": verdict.exit_code,
            "failures": list(verdict.failures),
            "phases": [
                {
                    "name": p.name,
                    "passed": p.passed,
                    "crashed": p.crashed,
                    "detail": p.detail,
                }
                for p in verdict.phase_reports
            ],
        },
        sort_keys=True,
    )
