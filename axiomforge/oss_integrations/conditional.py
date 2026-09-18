from .base import assess


def assess_dagster(evidence):
    return assess(
        "dagster",
        evidence,
        ("authority separation", "asset identity mapping", "no duplicate scheduler"),
    )


def assess_dvc(evidence):
    return assess(
        "dvc",
        evidence,
        ("command pinning", "remote allowlist", "workspace confinement"),
    )


def assess_feast(evidence):
    return assess(
        "feast",
        evidence,
        (
            "point-in-time correctness",
            "online/offline parity",
            "registry restore",
            "tenant isolation",
        ),
    )


def assess_nats_jetstream(evidence):
    return assess(
        "nats-jetstream",
        evidence,
        (
            "patched version",
            "mTLS",
            "account isolation",
            "deduplication",
            "restore drill",
        ),
    )
