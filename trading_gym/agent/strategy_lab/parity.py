def compare_strategy_traces(*traces):
    def norm(t):
        return [
            {k: v for k, v in x.items() if k not in {"mode", "executed"}} for x in t
        ]

    vals = [norm(t) for t in traces]
    return {
        "equivalent": all(v == vals[0] for v in vals[1:]) if vals else True,
        "executed_order": False,
    }
