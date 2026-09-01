"""Dependency inventory and release supply-chain policy checks."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any


MAX_LOCK_BYTES = 32 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bounded(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required dependency file is not a regular file: {path}")
    if path.stat().st_size > MAX_LOCK_BYTES:
        raise ValueError(f"dependency file exceeds {MAX_LOCK_BYTES} bytes: {path}")
    return path.read_bytes()


def _normalise_poetry_dependency(name: str, value: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"name": name, "ecosystem": "pypi", "direct": True}
    if isinstance(value, str):
        record["constraint"] = value
        record["source"] = "registry"
    elif isinstance(value, list):
        record["constraint"] = " OR ".join(str(item.get("version", "*")) for item in value if isinstance(item, dict))
        record["source"] = "registry"
    elif isinstance(value, dict):
        record["constraint"] = str(value.get("version", "*"))
        source_keys = [key for key in ("git", "path", "url") if key in value]
        record["source"] = source_keys[0] if source_keys else "registry"
        for key in source_keys:
            record[key] = str(value[key])
    else:
        record["constraint"] = str(value)
        record["source"] = "unknown"
    return record


def build_dependency_inventory(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    pyproject_path = root / "pyproject.toml"
    poetry_lock = root / "poetry.lock"
    package_path = root / "frontend" / "package.json"
    package_lock = root / "frontend" / "package-lock.json"
    pyproject = tomllib.loads(_read_bounded(pyproject_path).decode("utf-8"))
    poetry = tomllib.loads(_read_bounded(poetry_lock).decode("utf-8"))
    package = json.loads(_read_bounded(package_path).decode("utf-8"))
    lock = json.loads(_read_bounded(package_lock).decode("utf-8"))

    python_direct = []
    dependencies = pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, value in sorted(dependencies.items()):
        if name == "python":
            continue
        python_direct.append(_normalise_poetry_dependency(name, value))

    python_locked: list[dict[str, Any]] = []
    for package_record in poetry.get("package", []):
        if not isinstance(package_record, dict):
            continue
        files = package_record.get("files", [])
        hashes = sorted(
            str(item.get("hash"))
            for item in files
            if isinstance(item, dict) and item.get("hash")
        )
        python_locked.append(
            {
                "name": str(package_record.get("name", "unknown")),
                "ecosystem": "pypi",
                "version": str(package_record.get("version", "unknown")),
                "direct": False,
                "groups": sorted(str(item) for item in package_record.get("groups", [])),
                "optional": bool(package_record.get("optional", False)),
                "python_versions": str(package_record.get("python-versions", "*")),
                "artifact_hashes": hashes,
            }
        )

    npm_components: list[dict[str, Any]] = []
    for key, value in sorted(lock.get("packages", {}).items()):
        if not key or not isinstance(value, dict):
            continue
        name = value.get("name") or key.rsplit("node_modules/", 1)[-1]
        npm_components.append(
            {
                "name": str(name),
                "ecosystem": "npm",
                "version": str(value.get("version", "unknown")),
                "resolved": value.get("resolved"),
                "integrity": value.get("integrity"),
                "development": bool(value.get("dev", False)),
                "license": value.get("license"),
                "path": key,
            }
        )

    findings: list[dict[str, str]] = []
    for component in python_direct:
        if component["source"] in {"git", "path", "url", "unknown"}:
            findings.append(
                {
                    "severity": "critical",
                    "code": "non_registry_python_dependency",
                    "component": component["name"],
                    "detail": f"direct Python dependency uses source {component['source']}",
                }
            )
        if component["constraint"] in {"*", "latest", ""}:
            findings.append(
                {
                    "severity": "warning",
                    "code": "unbounded_python_constraint",
                    "component": component["name"],
                    "detail": "direct Python dependency has no useful version bound",
                }
            )
    for component in python_locked:
        hashes = component["artifact_hashes"]
        if not hashes:
            findings.append(
                {
                    "severity": "critical",
                    "code": "missing_python_artifact_hash",
                    "component": component["name"],
                    "detail": "Poetry lock package has no artifact hash",
                }
            )
        elif any(not re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in hashes):
            findings.append(
                {
                    "severity": "critical",
                    "code": "invalid_python_artifact_hash",
                    "component": component["name"],
                    "detail": "Poetry lock package contains a non-SHA-256 artifact hash",
                }
            )

    for component in npm_components:
        resolved = component.get("resolved")
        if isinstance(resolved, str) and resolved.startswith("http://"):
            findings.append(
                {
                    "severity": "critical",
                    "code": "insecure_npm_transport",
                    "component": component["name"],
                    "detail": "package-lock resolves over plain HTTP",
                }
            )
        if resolved and not component.get("integrity"):
            findings.append(
                {
                    "severity": "critical",
                    "code": "missing_npm_integrity",
                    "component": component["name"],
                    "detail": "resolved npm package lacks an integrity value",
                }
            )

    backend_version = str(pyproject.get("tool", {}).get("poetry", {}).get("version", ""))
    frontend_version = str(package.get("version", ""))
    lock_root = lock.get("packages", {}).get("", {})
    lock_version = str(lock_root.get("version", "")) if isinstance(lock_root, dict) else ""
    if len({backend_version, frontend_version, lock_version}) != 1:
        findings.append(
            {
                "severity": "critical",
                "code": "version_incoherence",
                "component": "project",
                "detail": f"backend={backend_version}, frontend={frontend_version}, frontend-lock={lock_version}",
            }
        )

    files = {
        "pyproject.toml": _sha256(pyproject_path),
        "poetry.lock": _sha256(poetry_lock),
        "frontend/package.json": _sha256(package_path),
        "frontend/package-lock.json": _sha256(package_lock),
    }
    payload = {
        "schema": "trading-gym-dependency-inventory/v1",
        "project": {"name": pyproject.get("tool", {}).get("poetry", {}).get("name"), "version": backend_version},
        "files": files,
        "components": python_direct + python_locked + npm_components,
        "findings": findings,
        "summary": {
            "python_direct": len(python_direct),
            "python_locked": len(python_locked),
            "npm_packages": len(npm_components),
            "critical": sum(item["severity"] == "critical" for item in findings),
            "warnings": sum(item["severity"] == "warning" for item in findings),
        },
    }
    payload["inventory_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def verify_dependency_policy(project_root: Path) -> dict[str, Any]:
    inventory = build_dependency_inventory(project_root)
    critical = [item for item in inventory["findings"] if item["severity"] == "critical"]
    return {
        "valid": not critical,
        "inventory_sha256": inventory["inventory_sha256"],
        "critical": critical,
        "warnings": [item for item in inventory["findings"] if item["severity"] == "warning"],
        "summary": inventory["summary"],
    }
