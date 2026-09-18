from __future__ import annotations
from statistics import mean, pstdev, NormalDist


def tail_risk(returns, *, confidence=0.99, method="gaussian", provenance="local"):
    xs = [float(x) for x in returns]
    if len(xs) < 2:
        raise ValueError("at least two observations required")
    if not 0.5 < confidence < 1:
        raise ValueError("confidence out of range")
    mu = mean(xs)
    sigma = pstdev(xs)
    z = NormalDist().inv_cdf(1 - confidence)
    q = mu + z * sigma
    if method == "cornish-fisher":
        m3 = mean([(x - mu) ** 3 for x in xs])
        skew = m3 / (sigma**3) if sigma else 0
        q = mu + (z + (z * z - 1) * skew / 6) * sigma
    elif method != "gaussian":
        raise ValueError("unsupported method")
    return {
        "method": method,
        "confidence": confidence,
        "var": max(0.0, -q),
        "assumptions": ["iid approximation", "historical moments"],
        "provenance": provenance,
        "advisory_only": True,
        "authority_granted": False,
    }
