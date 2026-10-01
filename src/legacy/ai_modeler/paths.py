"""Stable filesystem roots for code across the internal package layout."""
import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
REPOSITORY_ROOT = PACKAGE_ROOT.parents[2]


def data_path(*parts: str) -> Path:
    """Return a writable app-data path, falling back to the project in dev mode."""
    root = Path(os.environ.get("AIMODELER_DATA_DIR") or PROJECT_ROOT)
    return root.joinpath(*parts)


def temp_path(*parts: str) -> Path:
    """Place generated records outside source trees."""
    explicit = os.environ.get("AIMODELER_TEMP_DIR")
    if explicit:
        root = Path(explicit)
    elif os.environ.get("AIMODELER_PACKAGED_DESKTOP"):
        root = data_path("temp")
    else:
        root = REPOSITORY_ROOT / "temp"
    return root.joinpath(*parts)


def docs_path(*parts: str) -> Path:
    """Resolve bundled docs first and checkout docs second."""
    bundled = PROJECT_ROOT / "docs"
    if bundled.is_dir():
        return bundled.joinpath(*parts)
    return REPOSITORY_ROOT.joinpath("docs", *parts)


def documentation_root() -> Path:
    """Return a root suitable for documentation-relative desktop links."""
    return PROJECT_ROOT if (PROJECT_ROOT / "docs").is_dir() else REPOSITORY_ROOT


def config_path() -> Path:
    """Return the active config location, overridable by desktop packaging."""
    override = os.environ.get("AIMODELER_CONFIG_PATH")
    return Path(override) if override else data_path("config.json")

def project_path(*parts: str) -> str:
    """Return a path rooted at the active Python resource workspace."""
    return str(PROJECT_ROOT.joinpath(*parts))
