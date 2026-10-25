"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import PolicyModel, evaluate_contract


def evaluate_mcp_runtime_profile(evidence, **kwargs):
    """Profiles use bounded closed transports, explicit argv, secret references, read-only mounts, and closed tool permissions. Negative: No process start, tool call, secret resolution, network connection, Docker socket, or privileged runtime."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("MCP runtime-profile assurance", data)


class McpRuntimeProfilePolicy(PolicyModel):
    """Recovered target-native contract for prompt-injection, credential, mutation, and open-world MCP trust boundaries."""

    pass
