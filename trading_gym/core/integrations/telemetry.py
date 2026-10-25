from .base import assess


def assess_opentelemetry_collector(evidence):
    return assess(
        "opentelemetry-collector",
        evidence,
        (
            "secret redaction",
            "bounded cardinality",
            "exporter failure behavior",
            "trace correlation",
        ),
    )
