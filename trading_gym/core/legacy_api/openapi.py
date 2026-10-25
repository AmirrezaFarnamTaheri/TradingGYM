"""Publish Pydantic component schemas without dropping explicit null defaults.

FastAPI 0.109 serializes the OpenAPI envelope with ``exclude_none=True``, which
also strips ``default: null`` from arbitrary component-schema dictionaries.
Keep its route/security generation, but use the same Pydantic definitions before
that lossy envelope encoding. Both the HTTP document and release exporter use
this one cached publisher. Compatibility with the pinned FastAPI helpers is
covered by the schema contract tests.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi._compat import (
    get_definitions,
    get_flat_models_from_fields,
    get_model_name_map,
)
from fastapi.openapi.utils import get_fields_from_routes


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """Preserve model defaults and aliases, retaining native routes and metadata."""
    if app.openapi_schema is not None:
        return app.openapi_schema

    fields = get_fields_from_routes([*app.routes, *app.webhooks.routes])
    _, definitions = get_definitions(
        fields=fields,
        model_name_map=get_model_name_map(
            get_flat_models_from_fields(fields, known_models=set())
        ),
        separate_input_output_schemas=app.separate_input_output_schemas,
    )
    native = FastAPI.openapi(app)
    components = native.get("components", {})
    app.openapi_schema = {
        **native,
        "components": {
            **components,
            "schemas": {**components.get("schemas", {}), **definitions},
        },
    }
    return app.openapi_schema
