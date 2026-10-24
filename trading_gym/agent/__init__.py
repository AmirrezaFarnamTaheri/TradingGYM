"""Provider hub and curated agent tooling."""

from trading_gym.agent.providers import ProviderConfig, ProviderRepository

__all__ = ["ProviderConfig", "ProviderRepository", "SafeToolRegistry"]


def __getattr__(name: str):
    # Importing a planner or provider must not initialize the unrelated
    # convergence/research tool stack (and its optional runtime packages).
    if name == "SafeToolRegistry":
        from trading_gym.agent.tools import SafeToolRegistry
        return SafeToolRegistry
    raise AttributeError(name)
