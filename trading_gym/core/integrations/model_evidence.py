from .base import assess


def assess_mlflow(evidence):
    return assess(
        "mlflow",
        evidence,
        (
            "tenant tags",
            "dataset and code digests",
            "artifact immutability",
            "restore drill",
        ),
    )
