> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](README.md). Historical commands, endpoints and verification counts below apply to that component only.

# Durable reference-run vertical slice

The authenticated run API accepts only an idempotency key and one fixed synthetic
SMA replay. It has no executable command, arbitrary path, exchange request,
training configuration or live/paper authority input. A POST creates/reuses an
ExperimentRepository record, writes a launch intent and reserved artifact name,
then delegates spawn to the existing bounded supervisor. Repeating the key never
starts a second worker. A launch with missing identity is unresolved and requires
operator inspection, not an automatic retry.

The worker claims its exact experiment (the normal generic claim behavior is
unchanged), owns a 120-second lease and heartbeats every 15 seconds. There is one
attempt and a 60-second worker-process watchdog. It runs the existing playground engine over 96 synthetic five-minute
bars and modeled costs. Before durable completion, it writes the candidate to the
reserved location, checks the artifact bound and commits its SHA-256 through the
repository's revision/owner/lease compare-and-set. A file left behind by failed
completion remains unpublished. Downloads recheck identity and hash.

Every worker transition retains the repository's normal atomic audit outbox.
The worker deliberately cannot deliver/seal it. GET reconciliation lets the API
deliver with its existing key; the response reports pending audit delivery.
No gateway, provider, broker or audit credential is delegated to this worker.

On API restart, verified survivor identities are adopted into the existing
supervisor quota. Lost identities remain explicit and leases eventually fail;
no candidate file is treated as proof of completion. Cancellation first persists
intent, then uses the supervisor's verified process-tree termination. Remaining
children or an unknown launch prevent a confirmed cancellation. A reused PID is
never signalled. Graceful API shutdown retains the existing worker cleanup policy.

This is a complete fixture workflow, not a general durable training or paper
worker and not market evidence. Extending it requires per-workload configuration
and artifact admission, resource/deadline policies, cancellation semantics and
restart fault tests. Model promotion and financial session authority remain with
their existing governed services. The deployment is still one API supervisor per
data home; this bridge introduces no distributed queue or multi-host guarantee.
