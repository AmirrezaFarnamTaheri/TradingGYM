# LightRAG vs the institutional_os catalog — evaluation (2026-08-25)

Matrix candidate: "LightRAG graph-RAG ingestion/query mechanisms — semantic-mining
plane upgrade; evaluate against existing institutional_os catalog first."
Disposition per `institutional_os/source_mining.py` vocabulary: **REFERENCE**
(ideas borrowed at design level; no dependency adopted). Rationale follows.

## What LightRAG contributes

1. Dual-level retrieval: entities are extracted into a knowledge graph; queries
   hit both **low-level** (specific entity/fact) and **high-level** (topic/theme)
   neighborhoods, and results are fused before generation.
2. Incremental ingestion: new documents merge into the existing graph without a
   full re-index; entity/provenance deduplication on merge.
3. Cheap re-rank layer over graph neighborhoods instead of raw vector top-k.

## What the catalog already provides

- `institutional_os/catalog/*.json`: versioned peer-wave corpora, skill catalogs,
  resource catalogs — every entry carries deterministic SHA-256 provenance.
- `institutional_os/source_mining.py`: typed surfaces with `semantic_units`
  (pre-extracted semantic atoms), dispositions (`adopted` … `rejected-with-reason`),
  high-risk flags, and evidence-completeness gates — i.e. the corpus is already
  **semantically chunked and adjudicated**, which is the input LightRAG's LLM
  extraction stage would have to manufacture.
- `institutional_os/signal_graph.py` plus the local `graphify` knowledge-graph
  toolchain: deterministic, AST-based graph construction over code/docs with
  community detection and path queries — no model in the loop.
- Sealed experiment specs bind evaluations to tape hashes: any retrieval upgrade
  must be replayable bit-for-bit.

## Why not adopt the dependency

| Concern | Detail |
|---|---|
| Determinism | LightRAG's entity extraction is LLM-mediated → non-deterministic graphs; breaks sealed-spec replayability and the sha256-provenance discipline of the catalog. |
| Cost plane | Per-document LLM extraction adds an unbounded inference cost to ingestion; the catalog's `semantic_units` already encode that signal deterministically. |
| Overlap | Dual-level retrieval degenerates to "search semantic units, then their parent surfaces/themes" — expressible as two ranked queries over existing fields plus fusion, without a graph database. |
| Redundancy | Local `graphify` covers the graph side deterministically where a real graph is warranted. |

## What IS worth absorbing (queued as ideas)

1. **Dual-level query fan-out** over the catalog: resolve a request against both
   `semantic_units` (fact level) and surface/category rollups (theme level),
   fuse by reciprocal rank — pure function over JSON, fully replayable.
2. **Incremental merge semantics**: catalog waves arrive append-only; formalize
   dedup-on-insert keyed by content hash so wave N+1 never invalidates wave N
   citations (the catalog already behaves this way informally).
3. **Neighborhood re-ranking**: after initial hit, expand to same-category /
   same-donor surfaces before final ranking.

Verdict recorded in `source_mining` terms: LightRAG = `reference-only`;
items 1–3 above = candidates for the next absorption pass if retrieval quality
gaps are demonstrated by an evaluation set.
