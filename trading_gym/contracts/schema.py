"""Bounded schema-evolution compatibility analysis.

The evaluator is evidence-only: it does not register schemas, migrate data, or
change producer/consumer authority. It identifies concrete compatibility risks
between two explicit contract versions.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CompatibilityMode = Literal["backward", "forward", "full"]
ScalarType = Literal["string", "integer", "number", "boolean", "datetime", "decimal", "object", "array"]
Classification = Literal["public", "internal", "confidential", "restricted"]


class FieldContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")]
    data_type: ScalarType
    required: bool = False
    nullable: bool = True
    default_present: bool = False
    enum_values: tuple[str, ...] = Field(default_factory=tuple, max_length=1000)
    semantic: Annotated[str, Field(min_length=1, max_length=500)]
    classification: Classification = "internal"

    @model_validator(mode="after")
    def coherent(self) -> "FieldContract":
        if self.required and self.nullable:
            raise ValueError("required fields must not be nullable")
        if len(set(self.enum_values)) != len(self.enum_values):
            raise ValueError("enum_values must be unique")
        if self.enum_values and self.data_type != "string":
            raise ValueError("enum_values are supported only for string fields")
        return self


class SchemaContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
    version: Annotated[int, Field(ge=1, le=1_000_000)]
    fields: list[FieldContract] = Field(min_length=1, max_length=10_000)
    primary_key: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    event_time_field: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_structure(self) -> "SchemaContract":
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("schema field names must be unique")
        known = set(names)
        if not set(self.primary_key).issubset(known):
            raise ValueError("primary_key references unknown fields")
        if self.event_time_field and self.event_time_field not in known:
            raise ValueError("event_time_field references an unknown field")
        by_name = {field.name: field for field in self.fields}
        for name in self.primary_key:
            field = by_name[name]
            if not field.required or field.nullable:
                raise ValueError("primary key fields must be required and non-nullable")
        if self.event_time_field:
            event_field = by_name[self.event_time_field]
            if event_field.data_type != "datetime" or not event_field.required or event_field.nullable:
                raise ValueError("event_time_field must be required, non-nullable datetime")
        return self


class SchemaCompatibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current: SchemaContract
    candidate: SchemaContract
    mode: CompatibilityMode = "full"
    allow_classification_relaxation: bool = False

    @model_validator(mode="after")
    def same_contract(self) -> "SchemaCompatibilityRequest":
        if self.current.contract_id != self.candidate.contract_id:
            raise ValueError("contracts must share contract_id")
        if self.candidate.version <= self.current.version:
            raise ValueError("candidate version must be greater than current version")
        return self


class SchemaCompatibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str
    current_version: int
    candidate_version: int
    mode: CompatibilityMode
    compatible: bool
    backward_breaks: list[str]
    forward_breaks: list[str]
    warnings: list[str]
    current_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    candidate_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    registration_authority_granted: bool = False
    migration_authority_granted: bool = False
    mutation_performed: bool = False


def _digest(model: BaseModel) -> str:
    payload = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def evaluate_schema_compatibility(request: SchemaCompatibilityRequest) -> SchemaCompatibilityReport:
    current = {field.name: field for field in request.current.fields}
    candidate = {field.name: field for field in request.candidate.fields}
    backward: list[str] = []
    forward: list[str] = []
    warnings: list[str] = []

    for name, old in current.items():
        new = candidate.get(name)
        if new is None:
            if old.required:
                backward.append(f"required_field_removed:{name}")
            else:
                warnings.append(f"optional_field_removed:{name}")
            continue
        if old.data_type != new.data_type:
            backward.append(f"type_changed:{name}:{old.data_type}->{new.data_type}")
            forward.append(f"type_changed:{name}:{old.data_type}->{new.data_type}")
        if old.nullable and not new.nullable and not new.default_present:
            backward.append(f"nullable_became_nonnullable:{name}")
        if not old.nullable and new.nullable:
            forward.append(f"nonnullable_became_nullable:{name}")
        if old.required and not new.required:
            forward.append(f"required_became_optional:{name}")
        if not old.required and new.required and not new.default_present:
            backward.append(f"optional_became_required_without_default:{name}")
        old_enum, new_enum = set(old.enum_values), set(new.enum_values)
        if old_enum and new_enum:
            removed = old_enum - new_enum
            added = new_enum - old_enum
            if removed:
                backward.append(f"enum_values_removed:{name}:{','.join(sorted(removed))}")
            if added:
                forward.append(f"enum_values_added:{name}:{','.join(sorted(added))}")
        if old.semantic != new.semantic:
            warnings.append(f"semantic_changed:{name}")
        levels = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
        if levels[new.classification] < levels[old.classification] and not request.allow_classification_relaxation:
            finding = f"classification_relaxed:{name}:{old.classification}->{new.classification}"
            # Confidentiality is a veto, not a directional compatibility trade-off.
            backward.append(finding)
            forward.append(finding)

    for name, new in candidate.items():
        if name in current:
            continue
        if new.required and not new.default_present:
            backward.append(f"required_field_added_without_default:{name}")
        else:
            warnings.append(f"field_added:{name}")

    if request.current.primary_key != request.candidate.primary_key:
        backward.append("primary_key_changed")
        forward.append("primary_key_changed")
    if request.current.event_time_field != request.candidate.event_time_field:
        backward.append("event_time_field_changed")
        forward.append("event_time_field_changed")

    backward = sorted(set(backward))
    forward = sorted(set(forward))
    warnings = sorted(set(warnings))
    applicable = backward if request.mode == "backward" else forward if request.mode == "forward" else backward + forward
    current_sha = _digest(request.current)
    candidate_sha = _digest(request.candidate)
    receipt = {
        "contract_id": request.current.contract_id,
        "current": current_sha,
        "candidate": candidate_sha,
        "mode": request.mode,
        "backward": backward,
        "forward": forward,
        "warnings": warnings,
    }
    report_sha = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return SchemaCompatibilityReport(
        contract_id=request.current.contract_id,
        current_version=request.current.version,
        candidate_version=request.candidate.version,
        mode=request.mode,
        compatible=not applicable,
        backward_breaks=backward,
        forward_breaks=forward,
        warnings=warnings,
        current_sha256=current_sha,
        candidate_sha256=candidate_sha,
        report_sha256=report_sha,
    )
