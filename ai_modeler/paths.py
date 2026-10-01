"""Stable filesystem roots for code across the internal package layout."""
import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def data_path(*parts: str) -> Path:
    """Return a writable app-data path, falling back to the project in dev mode."""
    root = Path(os.environ.get("AIMODELER_DATA_DIR") or PROJECT_ROOT)
    return root.joinpath(*parts)


def config_path() -> Path:
    """Return the active config location, overridable by desktop packaging."""
    override = os.environ.get("AIMODELER_CONFIG_PATH")
    return Path(override) if override else data_path("config.json")

def project_path(*parts: str) -> str:
    """Return a path rooted at the repository, independent of module location."""
    return str(PROJECT_ROOT.joinpath(*parts))
