from __future__ import annotations
import json
from pathlib import Path

CAT = Path(__file__).with_name("catalog") / "peer_wave15_corpus.json"


def load_wave15_peer_corpus():
    return json.loads(CAT.read_text(encoding="utf-8"))


def search_wave15_peer_catalog(query: str, *, limit: int = 50):
    if limit < 1 or limit > 200:
        raise ValueError("limit")
    q = query.casefold()
    c = load_wave15_peer_corpus()
    out = []
    for section in ("adoption", "surfaces"):
        for row in c.get(section, []):
            if q in json.dumps(row, sort_keys=True, ensure_ascii=False).casefold():
                out.append({"section": section, **row})
            if len(out) >= limit:
                return out
    return out


def evaluate_wave15_catalog_saturation():
    c = load_wave15_peer_corpus()
    blockers = []
    expected = 3883
    if not c.get("surface_index_complete"):
        blockers.append("surface_index_incomplete")
    if expected is not None and c.get("surface_count") != expected:
        blockers.append("surface_count_mismatch")
    return {
        "wave": 15,
        "version": "27.0.0",
        "surface_count": c.get("surface_count"),
        "surface_index_complete": c.get("surface_index_complete"),
        "blockers": blockers,
        "ready_for_human_review": not blockers,
        "authority_granted": False,
    }
