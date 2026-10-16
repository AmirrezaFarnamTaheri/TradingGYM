"""Offline, provenance-bound System-One probability and abstention evaluation.

No network, broker, checkpoint changes, or promotion authority. Ground-truth
labels must be independently established and available after the decision.
Raw market states, prompts, account secrets and responses are not inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Sequence

from trading_gym.intelligence.systemone_protocol import _probability

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_LABELS = ("hold", "long", "short")
_COLUMNS = frozenset({
    "model_id", "context_sha256", "source_sha256", "observed_at_unix",
    "label_available_at_unix", "split", "probabilities", "risk_off",
    "truth", "latency_ms",
})
_MAX_ROWS = 10_000


def _finite_positive(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("invalid finite evaluation metric")
    n = float(value)
    if not math.isfinite(n) or n < 0 or n > 3_600_000:
        raise ValueError("invalid finite evaluation metric")
    return n


def _validate(row: object, *, as_of_unix: int | None) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != _COLUMNS:
        raise ValueError("evaluation record has unrecognized or missing columns")
    if row["split"] != "holdout":
        raise ValueError("evaluation requires independently held-out labels")
    for key in ("context_sha256", "source_sha256"):
        if not isinstance(row[key], str) or not _SHA256.fullmatch(row[key]):
            raise ValueError("evaluation source hash is invalid")
    if not isinstance(row["model_id"], str) or not 1 <= len(row["model_id"]) <= 200:
        raise ValueError("evaluation model id is invalid")
    start, end = row["observed_at_unix"], row["label_available_at_unix"]
    if (
        type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
        or (as_of_unix is not None and end > as_of_unix)
    ):
        raise ValueError("evaluation label unavailable or time-leaking")
    if row["truth"] not in _LABELS or type(row["truth"]) is not str:
        raise ValueError("evaluation truth must be an independently labeled direction")
    probs = row["probabilities"]
    if not isinstance(probs, dict) or set(probs) != set(_LABELS):
        raise ValueError("evaluation probability keys are invalid")
    distribution = {label: _probability(probs[label]) for label in _LABELS}
    if abs(math.fsum(distribution.values()) - 1) > 0.001:
        raise ValueError("evaluation probability distribution is invalid")
    return {
        "model_id": row["model_id"],
        "context_sha256": row["context_sha256"],
        "source_sha256": row["source_sha256"],
        "observed_at_unix": start,
        "label_available_at_unix": end,
        "split": "holdout",
        "probabilities": distribution,
        "risk_off": _probability(row["risk_off"]),
        "truth": row["truth"],
        "latency_ms": _finite_positive(row["latency_ms"]),
    }


def evaluate_records(
    records: Sequence[dict[str, Any]],
    *,
    confidence_floor: float = 0.65,
    as_of_unix: int | None = None,
) -> dict[str, Any]:
    """Evaluate labeled, sealed records; never certify trading or model alpha.

    ECE compares raw winning probability to observed classification accuracy.
    It does not perform calibration itself, establish PnL, or prove a causal
    benefit from model-driven trades. Source hashes are mandatory.
    """
    confidence_floor = _probability(confidence_floor)
    if as_of_unix is not None and (type(as_of_unix) is not int or as_of_unix < 0):
        raise ValueError("invalid evaluation as-of timestamp")
    if not isinstance(records, (list, tuple)) or not 0 < len(records) <= _MAX_ROWS:
        raise ValueError("evaluation requires a bounded nonempty record sequence")
    normalized = [_validate(r, as_of_unix=as_of_unix) for r in records]
    normalized.sort(
        key=lambda r: (
            r["model_id"], r["observed_at_unix"], r["context_sha256"],
        )
    )
    if len({r["model_id"] for r in normalized}) != 1:
        raise ValueError("evaluation cannot combine unrelated models")
    seen: set[tuple[str, int, str]] = set()
    for record in normalized:
        identity = (
            record["model_id"], record["observed_at_unix"],
            record["context_sha256"],
        )
        if identity in seen:
            raise ValueError("duplicate model context in evaluation")
        seen.add(identity)

    accepted = correct = risk_vetoes = weak_or_tied = 0
    brier_total = 0.0
    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    latencies: list[float] = []
    for record in normalized:
        distribution = record["probabilities"]
        peak = max(distribution.values())
        winners = [
            label for label in _LABELS
            if abs(distribution[label] - peak) < 1e-9
        ]
        if record["risk_off"] >= 0.5:
            risk_vetoes += 1
        elif len(winners) != 1 or peak < confidence_floor:
            weak_or_tied += 1
        else:
            accepted += 1
            correct += int(winners[0] == record["truth"])
        correct_raw = int(
            len(winners) == 1 and winners[0] == record["truth"]
        )
        bins[min(9, int(peak * 10))].append((peak, correct_raw))
        brier_total += math.fsum(
            (
                distribution[label]
                - (1.0 if label == record["truth"] else 0.0)
            ) ** 2
            for label in _LABELS
        )
        latencies.append(record["latency_ms"])
    n = len(normalized)
    ece = math.fsum(
        (len(bucket) / n) * abs(
            math.fsum(int(right) for _, right in bucket) / len(bucket)
            - math.fsum(prob for prob, _ in bucket) / len(bucket)
        )
        for bucket in bins if bucket
    )
    latencies.sort()
    result: dict[str, Any] = {
        "schema": "tradinggym.systemone.holdout-evaluation.v1",
        "model_id": normalized[0]["model_id"],
        "samples": n,
        "accepted": accepted,
        "coverage": accepted / n,
        "accuracy_on_accepted": correct / accepted if accepted else None,
        "brier_score": brier_total / n,
        "ece": ece,
        "latency_p95_ms": latencies[math.ceil(0.95 * n) - 1],
        "risk_vetoes": risk_vetoes,
        "weak_or_tied": weak_or_tied,
        "confidence_floor": confidence_floor,
        "as_of_verified": as_of_unix is not None,
        "eligible_for_funded_live": False,
        "source_records_sha256": hashlib.sha256(
            json.dumps(
                normalized, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest(),
    }
    result["report_sha256"] = hashlib.sha256(
        json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return result
