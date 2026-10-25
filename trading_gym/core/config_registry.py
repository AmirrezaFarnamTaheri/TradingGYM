from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = deepcopy(value)
    return out


class ConfigRegistry:
    """Compose ordered configuration layers into one hash-addressed snapshot."""

    def __init__(self, *layers: Mapping[str, Any]):
        self.layers = [deepcopy(dict(layer)) for layer in layers]

    def snapshot(self) -> dict[str, Any]:
        composed: dict[str, Any] = {}
        for layer in self.layers:
            composed = _deep_merge(composed, layer)
        raw = json.dumps(
            composed,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        return {
            "config": composed,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "layer_count": len(self.layers),
        }

    def verify(self, sha256: str) -> bool:
        return self.snapshot()["sha256"] == sha256
