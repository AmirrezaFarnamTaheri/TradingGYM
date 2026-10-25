# Container candidate

The Dockerfile builds the Python package and production frontend. The release workflow verifies a candidate, builds an image, runs the installed `tgym doctor` command and archives the image plus generated checksums as a workflow artifact. It does not publish a public image registry release.

```sh
docker build -t tradinggym:candidate .
docker run --rm tradinggym:candidate python -m trading_gym.cli doctor
```

These are maintained commands, not a claim that the image was built locally. `doctor` checks dependencies; it does not exercise browser delivery, external connectors or funded execution. The server binds loopback. Ordinary Docker bridge port publishing does not make an in-container loopback listener available; use an appropriately isolated shared network namespace or in-container client and qualify that deployment separately. Do not weaken the local API boundary merely to expose it publicly.

Persist the entire cockpit account directory for durable state and protect credentials. A Windows DPAPI-protected master key is not a portable Linux container credential; enter keys in the target environment. See [first-run recovery](FIRST_RUN.md), [verification](VALIDATION_RUNBOOK.md) and [current limitations](REBUILD.md).
