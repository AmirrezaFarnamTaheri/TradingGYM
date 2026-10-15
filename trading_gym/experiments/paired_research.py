"""Read-only, as-of paired strategy comparison with multiplicity correction.

Derived from AI-Trader's challenge scoring and Vibe-Trading's explicit cost
accounting, without importing autonomous execution, auto-promotion, or
unverified fill/fee data. p-values are nominal diagnostics; independent
trials and a valid null model must be separately established.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import asdict, dataclass
from typing import Sequence

_SHA = re.compile(r'^[0-9a-f]{64}$')
_NAME = re.compile(r'^[a-z][a-z0-9._-]{1,79}$')
_MAX_MODELS = 32
_MAX_PAIRS = 256


@dataclass(frozen=True)
class ResearchRun:
    candidate_id: str
    seed: int
    regime: str
    tape_sha256: str
    source_sha256: str
    started_at_unix: int
    ended_at_unix: int
    label_available_at_unix: int
    gross_return: float
    cost_fraction: float
    split: str = 'holdout'


def _bounded_int(v: object) -> bool:
    return type(v) is int and 0 <= v < 10**11


def _finite(v: object, lo: float, hi: float) -> bool:
    return type(v) in (int, float) and math.isfinite(v) and lo <= v <= hi


def _validated(run: ResearchRun, *, as_of_unix: int) -> None:
    if not isinstance(run, ResearchRun):
        raise ValueError('unknown challenge run structure')
    if not _NAME.fullmatch(run.candidate_id) or not _NAME.fullmatch(run.regime):
        raise ValueError('invalid candidate or regime identifier')
    if not isinstance(run.tape_sha256, str) or not _SHA.fullmatch(
        run.tape_sha256
    ) or not isinstance(run.source_sha256, str) or not _SHA.fullmatch(
        run.source_sha256
    ):
        raise ValueError('sealed research tape/source hash missing')
    if type(run.seed) is not int or not 0 <= run.seed <= 2**32 - 1:
        raise ValueError('invalid independent challenge seed')
    if run.split != 'holdout':
        raise ValueError('training/evaluation overlap forbidden')
    if not all(_bounded_int(x) for x in (
        run.started_at_unix, run.ended_at_unix, run.label_available_at_unix
    )) or not (run.started_at_unix < run.ended_at_unix
              <= run.label_available_at_unix <= as_of_unix):
        raise ValueError('time-leaking or unavailable research label')
    if not _finite(run.gross_return, -10.0, 10.0):
        raise ValueError('gross return must be finite and bounded')
    if not _finite(run.cost_fraction, 0.0, 10.0):
        raise ValueError('non-negative explicit cost fraction required')


def _one_sided_sign_p(wins: int, non_ties: int) -> float:
    if non_ties == 0:
        return 1.0
    return sum(math.comb(non_ties, k) for k in range(wins, non_ties + 1)) / (
        2 ** non_ties
    )


def compare_paired_research(
    *, baseline_id: str,
    runs: Sequence[ResearchRun],
    as_of_unix: int,
    minimum_pairs: int = 3,
    minimum_regimes: int = 2,
) -> dict:
    """Compare identical sealed seed/regime/time scenarios on net returns.

    No independent model selection, promotion, or broker authorization. The
    nominal sign p-values may be invalid if seeds or regimes are correlated.
    Re-use with newly observed results requires a new hash-bound report.
    """
    if not isinstance(baseline_id, str) or not _NAME.fullmatch(baseline_id):
        raise ValueError('invalid benchmark candidate identity')
    if not _bounded_int(as_of_unix):
        raise ValueError('invalid comparison as-of')
    if type(minimum_pairs) is not int or not 2 <= minimum_pairs <= _MAX_PAIRS:
        raise ValueError('invalid minimum pair count')
    if type(minimum_regimes) is not int or not 1 <= minimum_regimes <= 128:
        raise ValueError('invalid minimum regime count')
    if (
        not isinstance(runs, (list, tuple)) or not runs
        or len(runs) > _MAX_MODELS * _MAX_PAIRS
    ):
        raise ValueError('research comparison input exceeds bounded size')

    groups: dict[str, dict[tuple[int, str], ResearchRun]] = {}
    for run in runs:
        _validated(run, as_of_unix=as_of_unix)
        rows = groups.setdefault(run.candidate_id, {})
        key = (run.seed, run.regime)
        if key in rows:
            raise ValueError('duplicate seed/regime in research candidate')
        if len(rows) >= _MAX_PAIRS:
            raise ValueError('too many candidate scenarios')
        rows[key] = run
    if len(groups) < 2 or len(groups) > _MAX_MODELS:
        raise ValueError('baseline and bounded candidate roster required')
    baseline = groups.get(baseline_id)
    if not baseline:
        raise ValueError('baseline missing paired research evidence')
    baseline_keys = set(baseline)
    if len(baseline_keys) < minimum_pairs:
        raise ValueError('insufficient completed holdout pair samples')
    if len({regime for _, regime in baseline_keys}) < minimum_regimes:
        raise ValueError('insufficient distinct holdout regimes')
    for name, rows in groups.items():
        if set(rows) != baseline_keys:
            raise ValueError(f'incomparable scenario coverage:{name}')
        for key, sample in rows.items():
            ref = baseline[key]
            if (
                sample.tape_sha256 != ref.tape_sha256
                or sample.started_at_unix != ref.started_at_unix
                or sample.ended_at_unix != ref.ended_at_unix
            ):
                raise ValueError('incompatible holdout tape or time window')

    pairs = sorted(baseline_keys)
    results: list[dict] = []
    for name in sorted(set(groups) - {baseline_id}):
        differences = [
            (groups[name][key].gross_return - groups[name][key].cost_fraction)
            - (baseline[key].gross_return - baseline[key].cost_fraction)
            for key in pairs
        ]
        wins = sum(1 for d in differences if d > 1e-12)
        losses = sum(1 for d in differences if d < -1e-12)
        non_ties = wins + losses
        results.append({
            'candidate_id': name,
            'paired_scenarios': len(pairs),
            'non_tied_pairs': non_ties,
            'wins': wins,
            'losses': losses,
            'median_net_excess': statistics.median(differences),
            'nominal_one_sided_p_value': _one_sided_sign_p(wins, non_ties),
        })
    # Holm-Bonferroni controls familywise error under the stated null,
    # even with dependence among tests. It does not establish independent
    # samples or a causal trading advantage.
    rank = sorted(results, key=lambda r: (
        r['nominal_one_sided_p_value'], r['candidate_id']
    ))
    previous = 0.0
    for i, result in enumerate(rank):
        adjusted = min(
            1.0, max(previous, (len(rank) - i) * result['nominal_one_sided_p_value'])
        )
        result['holm_adjusted_p_value'] = adjusted
        previous = adjusted
    sorted_runs = [asdict(groups[name][key])
                   for name in sorted(groups) for key in pairs]
    body = {
        'schema': 'tradinggym.paired-holdout.v1',
        'baseline_id': baseline_id,
        'as_of_unix': as_of_unix,
        'candidate_count': len(results),
        'paired_scenarios': len(pairs),
        'regimes': sorted({regime for _, regime in pairs}),
        'candidates': sorted(results, key=lambda r: r['candidate_id']),
        'source_records_sha256': hashlib.sha256(json.dumps(
            sorted_runs, sort_keys=True, separators=(',', ':'), allow_nan=False
        ).encode()).hexdigest(),
        'statistical_independence_verified': False,
        'selection_authority_granted': False,
        'promotion_authority_granted': False,
        'funded_live_eligible': False,
    }
    body['report_sha256'] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(',', ':'), allow_nan=False
    ).encode()).hexdigest()
    return body
