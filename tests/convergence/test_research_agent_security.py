from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind, ProviderRepository
from trading_gym.agent.tools import SafeToolRegistry, ToolRequest
from trading_gym.common.security import redact_secrets, validate_base_url
from trading_gym.governance.ledger import AuditLedger
from trading_gym.governance.service import GovernanceService
from trading_gym.research.dossier import build_dossier
from trading_gym.research.models import EvidenceItem, EvidenceQuality
from trading_gym.research.reflection import audit_claims
from trading_gym.strategy_lab.repository import StrategyRepository


def test_dossier_declares_missing_data_and_reflection_rejects_absence_claim() -> None:
    evidence = [
        EvidenceItem(
            evidence_id="market:1",
            title="Price snapshot",
            source="exchange",
            observed_at=datetime.now(timezone.utc),
            quality=EvidenceQuality.PRIMARY,
            facts=["Closing price was 100."],
            metadata={"category": "market"},
        )
    ]
    dossier = build_dossier(subject="TEST", evidence=evidence)
    assert "news" in dossier.missing_data
    result = audit_claims(["There was no news [market:1]."], dossier)
    assert any(item["code"] == "absence_from_missing_data" for item in result["findings"])
    assert audit_claims(["Closing price was 100 [market:1]."], dossier)["valid"] is True


def test_security_redacts_and_blocks_ssrf_targets() -> None:
    assert redact_secrets({"nested": {"api-key": "secret"}})["nested"]["api-key"] == "***"
    with pytest.raises(ValueError):
        validate_base_url("http://169.254.169.254/latest", resolve_dns=False)
    with pytest.raises(ValueError):
        validate_base_url("file:///etc/passwd", resolve_dns=False)
    assert validate_base_url("http://127.0.0.1:11434", allow_loopback=True, resolve_dns=False) == "http://127.0.0.1:11434"


def test_provider_repository_stores_only_env_reference(tmp_path: Path) -> None:
    repository = ProviderRepository(tmp_path / "providers.json")
    provider = ProviderConfig(
        provider_id="openai",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        display_name="OpenAI compatible",
        model="model",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    )
    repository.save(provider)
    content = (tmp_path / "providers.json").read_text()
    assert "OPENAI_API_KEY" in content
    assert "sk-" not in content


def test_safe_tools_expose_no_direct_live_trade(tmp_path: Path) -> None:
    governance = GovernanceService(tmp_path / "proposals.json", AuditLedger(tmp_path / "audit.jsonl"))
    registry = SafeToolRegistry(strategies=StrategyRepository(tmp_path / "strategies.json"), governance=governance)
    names = {item["name"] for item in registry.describe()}
    assert "trade.execute" not in names
    proposal = registry.invoke(
        ToolRequest(
            tool="proposal.create",
            actor="agent",
            arguments={
                "operation": "trade.live.start",
                "summary": "Request live trading start",
                "risk_tier": "critical",
                "idempotency_key": "live-start-001",
            },
        )
    )
    assert proposal["state"] == "pending"
