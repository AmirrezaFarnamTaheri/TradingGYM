from .base import assess


def assess_temporal(evidence):
    return assess(
        "temporal",
        evidence,
        (
            "deterministic replay",
            "worker versioning",
            "persistence restore",
            "backpressure",
            "history retention",
        ),
    )
