from .base import assess


def assess_apache_arrow(evidence):
    return assess(
        "apache-arrow",
        evidence,
        ("schema fingerprint", "IPC round-trip", "version compatibility"),
    )


def assess_duckdb(evidence):
    return assess(
        "duckdb",
        evidence,
        (
            "read-only query policy",
            "extension allowlist",
            "memory and row bounds",
            "differential benchmark",
        ),
    )


def assess_pandera(evidence):
    return assess(
        "pandera",
        evidence,
        ("schema parity fixtures", "validation error mapping", "large-frame bounds"),
    )


def assess_polars(evidence):
    return assess(
        "polars",
        evidence,
        (
            "representative benchmark",
            "differential result parity",
            "wheel and CPU compatibility",
        ),
    )
