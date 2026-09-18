from datetime import datetime, timedelta, timezone

from trading_gym.governance.credential_rotation import (
    CredentialMetadata,
    CredentialRotationPolicy,
    CredentialRotationRequest,
    CredentialState,
    evaluate_credential_rotation,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def credential(
    *,
    credential_id,
    purpose,
    key_id,
    fingerprint,
    state=CredentialState.ACTIVE,
    activated_days=10,
    expires_days=30,
    successor=None
):
    return CredentialMetadata(
        credential_id=credential_id,
        purpose=purpose,
        key_id=key_id,
        fingerprint_sha256=fingerprint,
        state=state,
        activated_at=NOW - timedelta(days=activated_days),
        expires_at=(
            NOW + timedelta(days=expires_days) if expires_days is not None else None
        ),
        successor_key_id=successor,
        environments=["production"],
    )


def policy():
    return CredentialRotationPolicy(
        required_purposes=["gateway", "audit"],
        maximum_active_age_days=90,
        expiry_warning_days=14,
        minimum_overlap_seconds=3600,
    )


def test_rotation_readiness_is_secret_free_and_non_authoritative():
    report = evaluate_credential_rotation(
        CredentialRotationRequest(
            evaluated_at=NOW,
            credentials=[
                credential(
                    credential_id="cred:gateway-001",
                    purpose="gateway",
                    key_id="key:gateway-001",
                    fingerprint="a" * 64,
                ),
                credential(
                    credential_id="cred:audit-001",
                    purpose="audit",
                    key_id="key:audit-001",
                    fingerprint="b" * 64,
                ),
            ],
            policy=policy(),
        )
    )
    assert report.ready_for_human_review is True
    assert report.secrets_exposed is False
    assert report.rotation_authority_granted is False


def test_missing_required_purpose_and_old_active_key_fail_closed():
    report = evaluate_credential_rotation(
        CredentialRotationRequest(
            evaluated_at=NOW,
            credentials=[
                credential(
                    credential_id="cred:gateway-001",
                    purpose="gateway",
                    key_id="key:gateway-001",
                    fingerprint="a" * 64,
                    activated_days=120,
                )
            ],
            policy=policy(),
        )
    )
    assert report.ready_for_human_review is False
    assert "active_credential_missing:audit" in report.blockers
    assert "active_credential_too_old:cred:gateway-001" in report.blockers


def test_retiring_key_requires_active_same_purpose_successor_and_overlap():
    report = evaluate_credential_rotation(
        CredentialRotationRequest(
            evaluated_at=NOW,
            credentials=[
                credential(
                    credential_id="cred:gateway-old",
                    purpose="gateway",
                    key_id="key:gateway-old",
                    fingerprint="a" * 64,
                    state=CredentialState.RETIRING,
                    activated_days=60,
                    expires_days=1,
                    successor="key:gateway-new",
                ),
                credential(
                    credential_id="cred:gateway-new",
                    purpose="gateway",
                    key_id="key:gateway-new",
                    fingerprint="b" * 64,
                    activated_days=2,
                ),
                credential(
                    credential_id="cred:audit-001",
                    purpose="audit",
                    key_id="key:audit-001",
                    fingerprint="c" * 64,
                ),
            ],
            policy=policy(),
        )
    )
    assert report.ready_for_human_review is True
    assert report.retiring_count == 1


def test_reused_fingerprint_is_rejected():
    report = evaluate_credential_rotation(
        CredentialRotationRequest(
            evaluated_at=NOW,
            credentials=[
                credential(
                    credential_id="cred:gateway-001",
                    purpose="gateway",
                    key_id="key:gateway-001",
                    fingerprint="a" * 64,
                ),
                credential(
                    credential_id="cred:audit-001",
                    purpose="audit",
                    key_id="key:audit-001",
                    fingerprint="a" * 64,
                ),
            ],
            policy=policy(),
        )
    )
    assert "credential_fingerprint_reused" in report.blockers
