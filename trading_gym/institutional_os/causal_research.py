"""Assumption-explicit causal graph and synthetic-control evidence."""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import sha256_payload

MAX_CAUSAL_OBSERVATIONS = 5_000_000
MAX_CAUSAL_MAGNITUDE = 1e30


class CausalEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cause: Annotated[str, Field(min_length=1, max_length=120)]
    effect: Annotated[str, Field(min_length=1, max_length=120)]


class DonorSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    series_id: Annotated[str, Field(min_length=1, max_length=120)]
    pre: list[float] = Field(min_length=2, max_length=100_000)
    post: list[float] = Field(min_length=1, max_length=100_000)


class CausalResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    treatment_id: Annotated[str, Field(min_length=1, max_length=120)]
    variables: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        min_length=2, max_length=10_000
    )
    edges: list[CausalEdge] = Field(max_length=100_000)
    outcome_variable: Annotated[str, Field(min_length=1, max_length=120)]
    treatment_pre: list[float] = Field(min_length=2, max_length=100_000)
    treatment_post: list[float] = Field(min_length=1, max_length=100_000)
    donors: list[DonorSeries] = Field(min_length=1, max_length=10_000)
    unobserved_confounding_acknowledged: bool = False

    @model_validator(mode="after")
    def validate_inputs(self) -> "CausalResearchRequest":
        if len(self.variables) != len(set(self.variables)):
            raise ValueError("causal variables must be unique")
        donor_ids = [item.series_id for item in self.donors]
        if len(donor_ids) != len(set(donor_ids)):
            raise ValueError("causal donor IDs must be unique")
        edge_keys = [(item.cause, item.effect) for item in self.edges]
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("causal edges must be unique")
        variables = set(self.variables)
        if self.outcome_variable not in variables:
            raise ValueError("outcome variable is missing")
        for edge in self.edges:
            if edge.cause not in variables or edge.effect not in variables:
                raise ValueError("causal edge references unknown variable")
        lengths = {len(self.treatment_pre), *(len(item.pre) for item in self.donors)}
        post_lengths = {
            len(self.treatment_post),
            *(len(item.post) for item in self.donors),
        }
        if len(lengths) != 1 or len(post_lengths) != 1:
            raise ValueError("synthetic-control series must be aligned")
        observation_count = len(self.treatment_pre) + len(self.treatment_post)
        observation_count += sum(len(item.pre) + len(item.post) for item in self.donors)
        if observation_count > MAX_CAUSAL_OBSERVATIONS:
            raise ValueError("causal observation work budget exceeded")
        values = [*self.treatment_pre, *self.treatment_post]
        for donor in self.donors:
            values.extend(donor.pre)
            values.extend(donor.post)
        if any(not math.isfinite(item) for item in values):
            raise ValueError("causal series must be finite")
        if any(abs(item) > MAX_CAUSAL_MAGNITUDE for item in values):
            raise ValueError("causal series magnitude is unbounded")
        return self


class CausalResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    treatment_id: str
    graph_acyclic: bool
    donor_weights: dict[str, float]
    pre_treatment_rmse: float
    average_treatment_effect: float
    post_treatment_effects: list[float]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    causal_conclusion_authority_granted: bool = False
    intervention_performed: bool = False


def evaluate_causal_research(request: CausalResearchRequest) -> CausalResearchReport:
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in request.edges:
        outgoing[edge.cause].append(edge.effect)
    indegree = {node: 0 for node in request.variables}
    for targets in outgoing.values():
        for target in targets:
            indegree[target] += 1
    queue = [node for node in request.variables if indegree[node] == 0]
    heapq.heapify(queue)
    visited = 0
    while queue:
        node = heapq.heappop(queue)
        visited += 1
        for target in sorted(outgoing[node]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(queue, target)
    acyclic = visited == len(request.variables)
    blockers = []
    warnings = []
    if not acyclic:
        blockers.append("causal_graph_cycle")
    if not request.unobserved_confounding_acknowledged:
        blockers.append("unobserved_confounding_not_acknowledged")
    errors = []
    for donor in request.donors:
        mse = sum((a - b) ** 2 for a, b in zip(request.treatment_pre, donor.pre)) / len(
            request.treatment_pre
        )
        errors.append((mse, donor))
    inverse = [1.0 / max(item[0], 1e-12) for item in errors]
    total = sum(inverse)
    weights = {
        item[1].series_id: inverse[index] / total for index, item in enumerate(errors)
    }
    synthetic_pre = [
        sum(weights[donor.series_id] * donor.pre[index] for donor in request.donors)
        for index in range(len(request.treatment_pre))
    ]
    synthetic_post = [
        sum(weights[donor.series_id] * donor.post[index] for donor in request.donors)
        for index in range(len(request.treatment_post))
    ]
    pre_rmse = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(request.treatment_pre, synthetic_pre))
        / len(synthetic_pre)
    )
    effects = [
        actual - counterfactual
        for actual, counterfactual in zip(request.treatment_post, synthetic_post)
    ]
    if pre_rmse > max(
        1e-12, abs(sum(request.treatment_pre) / len(request.treatment_pre)) * 0.2
    ):
        warnings.append("weak_pre_treatment_fit")
    payload = {
        "treatment_id": request.treatment_id,
        "graph_acyclic": acyclic,
        "donor_weights": dict(sorted(weights.items())),
        "pre_treatment_rmse": pre_rmse,
        "average_treatment_effect": sum(effects) / len(effects),
        "post_treatment_effects": effects,
        "blockers": blockers,
        "warnings": warnings,
    }
    return CausalResearchReport(**payload, report_sha256=sha256_payload(payload))
