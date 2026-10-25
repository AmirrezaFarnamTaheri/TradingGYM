from __future__ import annotations
import json


def export_openapi(app):
    schema = app.openapi()
    return (
        json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    )
