"""Schema publishing must preserve Pydantic defaults, references, and caching."""

from decimal import Decimal
from functools import partial
from unittest.mock import patch

from fastapi import FastAPI
from pydantic import BaseModel, Field


class SchemaExample(BaseModel):
    required_nullable: str | None
    optional_nullable: str | None = None
    label: str = Field(default="ready", alias="display_label")
    amount: Decimal = Decimal("1.25")
    generated: list[str] = Field(default_factory=list)


def make_app():
    app = FastAPI(title="Schema example", version="test")

    @app.post("/example", response_model=SchemaExample)
    def example(value: SchemaExample):
        return value

    return app


def test_live_gateway_preserves_provider_null_defaults():
    from trading_gym.core.legacy_api.main import app
    from trading_gym.agent.providers import ProviderConfig

    live = app.openapi()["components"]["schemas"]["ProviderConfig"]["properties"]
    expected = ProviderConfig.model_json_schema()["properties"]
    for field in ("api_key_env", "base_url", "command", "max_requests_per_hour"):
        assert "default" in live[field], field
        assert live[field]["default"] == expected[field]["default"]


def test_publisher_preserves_required_nullable_and_factory_semantics():
    from trading_gym.core.legacy_api.openapi import build_openapi

    app = make_app()
    app.openapi = partial(build_openapi, app)
    document = app.openapi()
    assert document["info"] == {"title": "Schema example", "version": "test"}
    for name, schema in document["components"]["schemas"].items():
        if not name.startswith("SchemaExample"):
            continue
        properties = schema["properties"]
        assert properties["optional_nullable"]["default"] is None
        assert "default" not in properties["required_nullable"]
        assert "required_nullable" in schema["required"]
        assert "optional_nullable" not in schema["required"]
        assert properties["display_label"]["default"] == "ready"
        assert "default" not in properties["generated"]
    assert document["paths"]["/example"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"].startswith("#/components/schemas/")


def test_publisher_caches_document_without_regenerating_schema_definitions():
    from trading_gym.core.legacy_api import openapi

    app = make_app()
    with patch.object(
        openapi, "get_definitions", wraps=openapi.get_definitions
    ) as generate:
        first = openapi.build_openapi(app)
        second = openapi.build_openapi(app)
    assert second is first
    assert app.openapi_schema is first
    assert generate.call_count == 1


def test_preexisting_custom_schema_is_respected():
    from trading_gym.core.legacy_api.openapi import build_openapi

    app = make_app()
    custom = {"openapi": "3.1.0", "paths": {"/custom": {}}}
    app.openapi_schema = custom
    assert build_openapi(app) is custom


def test_served_document_keeps_defaults_without_weakening_request_validation():
    from fastapi.testclient import TestClient
    from trading_gym.core.legacy_api.openapi import build_openapi

    app = make_app()
    app.openapi = partial(build_openapi, app)
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        input_ref = schema["paths"]["/example"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        output_ref = schema["paths"]["/example"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        definitions = schema["components"]["schemas"]
        incoming = definitions[input_ref.rsplit("/", 1)[1]]
        outgoing = definitions[output_ref.rsplit("/", 1)[1]]
        assert incoming["properties"]["optional_nullable"]["default"] is None
        assert outgoing["properties"]["optional_nullable"]["default"] is None
        assert incoming["properties"]["amount"] != outgoing["properties"]["amount"]
        assert client.post("/example", json={}).status_code == 422
        response = client.post("/example", json={"required_nullable": None})
        assert response.status_code == 200
        assert response.json()["optional_nullable"] is None
        assert response.json()["amount"] == "1.25"


def test_empty_application_keeps_native_document_metadata():
    from trading_gym.core.legacy_api.openapi import build_openapi

    app = FastAPI(title="Empty API", version="empty")
    schema = build_openapi(app)
    assert schema["paths"] == {}
    assert schema["info"]["title"] == "Empty API"


def test_published_contract_includes_upstream_reconciliation_and_probe_budget():
    import json
    from pathlib import Path

    document = json.loads(Path("docs/contracts/research-openapi.json").read_text())
    assert "/api/v1/production/reconciliation" in document["paths"]
    parameters = document["paths"]["/api/v1/intelligence/status"]["get"]["parameters"]
    assert any(
        item["in"] == "query" and item["name"] == "probe_limit" for item in parameters
    )
