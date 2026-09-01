"""Shared helpers for router-surface tests.

Starlette >= 0.46 wraps ``include_router`` mounts in opaque route objects that
do not expose ``.path``.  The OpenAPI schema always contains the concrete
flattened paths, so derive the surface from it instead.
"""


def app_route_paths(app) -> set[str]:
    """Return every concrete API path registered on ``app``."""
    schema = app.openapi()
    return set(schema.get("paths", {}))
