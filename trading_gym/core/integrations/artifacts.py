from .base import assess


def assess_lakefs(evidence):
    return assess(
        "lakefs",
        evidence,
        (
            "metadata backup",
            "object restore",
            "commit immutability",
            "retention and garbage collection",
        ),
    )
