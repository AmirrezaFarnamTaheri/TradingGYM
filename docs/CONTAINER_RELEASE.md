# Supported container candidate

The first packaged deployment is one API supervisor plus the built frontend in one container. `Dockerfile` installs the existing locked runtime and embeds the frontend; Python wheels are not advertised as a complete application distribution. The base-image tags are explicit build inputs, not a claim of bit-reproducible rebuilding; retain the produced image/archive digest in the release receipt.

Build with `docker build -t tradinggym:candidate .` and validate with `python scripts/smoke_container.py tradinggym:candidate`. The smoke sends only readiness, UI and authenticated status reads, uses disposable volumes and never submits a trade. The release workflow requires verification, builds/smokes an image, and exposes its archive plus SHA256 through an Actions artifact. It does not publish to a public registry or grant live authority.

For use, provide `API_KEY` at runtime and persist **both** `/home/appuser/.trading-gym` (durable state) and `/app/config` (configuration) using named volumes. Fresh named volumes receive the image's config defaults; an empty bind mount must be seeded deliberately. Never build from a context that overrides the checked-in exclusion policy. Credentials and journals are runtime inputs; `.dockerignore` excludes them independently of Git's ignore rules.

This image remains a release candidate until the installed journey, platform support, upgrade/rollback and isolated application recovery tests pass. A frontend build or the smoke alone does not qualify model outcomes, broker compatibility or financial recovery.

References: https://docs.docker.com/build/concepts/context/ ; https://packaging.python.org/en/latest/tutorials/packaging-projects/ .
