# Read-only observation admission

Inject an existing `EnhancedAbanClient`-compatible read adapter into
`trading_gym.adapters.observation_recorder.capture`. No client, credential or
network session is constructed by this module. Its only calls are one ticker
read and a fee/limit read for each side. Tests use a client with no other
capability; no actual venue request was made.

Each observation records source/version/instrument, declared and independently
observed quote currency when supplied, a mapping reference, fee units, UTC
receive times per read, source event time/provenance if available, monotonic read
duration, whitelisted normalized fields and their digest. These adapter models
currently contain no event timestamp or quote-currency field, so the default
record keeps both unknown and requires the declared modeled-cost fallback.
Receive time is not substituted for event time. The field digest is not claimed
to hash the original HTTP response. Exceptions retain only their type, no body.

Research admission requires valid non-inverted positive quotes, matching
instrument/currency, a declared mapping, usable limits/advertised fees, bounded
read duration and known fresh event time. Missing/invalid fields produce explicit
downgrade reasons. A supplied mapping reference is provenance, not independent
verification that the venue currently honors the contract. Even an admitted
record is advertised market evidence, not an own-order fill, executable depth,
fee receipt, latency measurement for an order, or funded authority.

`persist` writes immutable bounded records under a private root, refuses links
and overwrite, and stops at a record limit. `coverage` accepts bounded batches
and reports time coverage, downgrade reasons and the median spread among admitted
observations. It does not claim continuous historical coverage. Use a separate
collection approval and sampling plan before enabling network collection; retain
timestamps and units when extending the adapter models. Pair observations with
the same market/time bars before comparing them to high/low range proxies.
This patch does not change execution costs or replace any broker adapter.
