from dataclasses import dataclass


@dataclass(frozen=True)
class TokenHolderSnapshot:
    holders: tuple
    page: int = 1


def normalise_page(rows, *, page=1, limit=1000):
    if limit < 1 or limit > 10000:
        raise ValueError("limit")
    vals = sorted(
        ({"address": str(r["address"]), "amount": float(r["amount"])} for r in rows),
        key=lambda r: (-r["amount"], r["address"]),
    )[:limit]
    return TokenHolderSnapshot(tuple(vals), page)


def analyse_onchain(interactions, *, threshold=1):
    graph = {}
    for e in interactions:
        a, b = str(e["from"]), str(e["to"])
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    seen = set()
    clusters = []
    for n in sorted(graph):
        if n in seen:
            continue
        stack = [n]
        comp = []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack.extend(graph.get(x, ()))
        if len(comp) >= threshold:
            clusters.append(sorted(comp))
    return {
        "clusters": clusters,
        "identity_attribution": False,
        "authority_granted": False,
    }
