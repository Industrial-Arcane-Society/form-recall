"""Stable filesystem roots for code across the internal package layout."""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

def project_path(*parts: str) -> str:
    """Return a path rooted at the repository, independent of module location."""
    return str(PROJECT_ROOT.joinpath(*parts))
