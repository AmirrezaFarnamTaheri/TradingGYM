# Settings, credentials and decision evidence

The current implementation is `trading_gym/api/main.py`, `agent/daemon.py`, `agent/gateway.py`, `agent/decision_history.py` and `core/local_secrets.py` beneath the package root.

## Credentials

The local API accepts secret fields, validates them without echoing input values, encrypts values with Fernet, and returns only roster identifiers and counts. The Windows master key is protected with DPAPI; POSIX uses a private master file. Protect the account directory and local user session. Environment fallback keys are read server-side; this application does not automatically load `.env`.

Each model role supports up to 16 distinct single-line keys. CredentialPool selects keys in rotation and tracks endpoint-scoped admission/cooldown state. Shared-provider limits suppress retry storms across keys; a quota failure must not be bypassed by trying every credential. Authentication and hard-quota failures cannot yield a risk-on planner fallback. A rotated key ID in the route identifies the slot used, never the secret.

Both current cockpit gateways use OpenAI-compatible chat JSON responses. An endpoint URL must pass validation and must not contain credentials. Enabling a gateway is not evidence that a provider request succeeded. Native System-One research backends have separate typed protocols and are not automatically selected by this UI.

## Decision records

SQLite records immutable decision IDs with market observations, each participating head's signal and confidence, fusion weights, planner output, model/key identifiers, risk references, block reasons, target and outcome. Paper fills carry the matching ID. Broker imports link only when the exchange tracking ID matches a known local decision; otherwise the explanation is unknown. The displayed rationale is recorded model/signal output, not an independent proof that the decision was correct.

History endpoints are paginated. The compact runtime chart is bounded, while the journal preserves execution/decision history. Broker and paper history are separate filters. Empty records, unknown fees and unavailable profit values stay empty or unknown.

## Runtime topology

Live monitor distinguishes enabled heads from fresh, active contributors. Available tools report actual connectivity/read status. Gateway routes show configured endpoint/model, available slots and last used key identifier. The last decision route ends in a block, observation-only outcome or paper eligibility; it must not imply a confirmed exchange order.

## API

The loopback API is under `/api/v1/agent`. Use [the generated contract](contracts/openapi.json) for exact request fields and responses. It includes settings, credentials, broker refresh, controls, paginated history/decisions and decision detail. Browser origins, host and peer are restricted to the supported local boundary. Never expose this unauthenticated local service through a public tunnel.
