from .base import assess


def assess_evidently(evidence):
    return assess(
        "evidently",
        evidence,
        (
            "metric provenance",
            "report reproducibility",
            "threshold ownership",
            "large-input bounds",
        ),
    )


def assess_great_expectations(evidence):
    return assess(
        "great-expectations",
        evidence,
        ("suite versioning", "validation result hashing", "connector isolation"),
    )
