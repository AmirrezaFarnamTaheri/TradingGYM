from .base import assess


def assess_openlineage(evidence):
    return assess(
        "openlineage",
        evidence,
        (
            "golden event fixtures",
            "custom facet governance",
            "delivery retry and deduplication",
        ),
    )
