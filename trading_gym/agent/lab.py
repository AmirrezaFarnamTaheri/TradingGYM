from dataclasses import dataclass


@dataclass(frozen=True)
class RegimeVote:
    detector: str
    regime: str
    confidence: float
    rationale: str


def regime_votes(features):
    trend = float(features.get("trend", 0))
    breadth = float(features.get("breadth", 0))
    vol = float(features.get("volatility", 0))
    return [
        RegimeVote(
            "trend",
            "risk_on" if trend > 0 else "risk_off",
            min(1, abs(trend)),
            "trend sign and magnitude",
        ),
        RegimeVote(
            "breadth",
            "risk_on" if breadth >= 0 else "risk_off",
            min(1, abs(breadth)),
            "cross-sectional breadth sign",
        ),
        RegimeVote(
            "volatility",
            "risk_off" if vol > 1 else "neutral",
            min(1, max(0, vol / 2)),
            "volatility threshold",
        ),
    ]
