"""Serve an explicitly built frontend after API routes are registered."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def mount_built_ui(app: FastAPI, directory: Path) -> bool:
    if directory.is_symlink():
        raise ValueError("frontend build directory must not be a symlink")
    if not (directory / "index.html").is_file():
        return False  # Source/dev API remains usable without an invented build.
    app.mount(
        "/",
        StaticFiles(directory=directory, html=True, follow_symlink=False),
        name="frontend",
    )
    return True
