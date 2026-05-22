"""mongosemantic package.

`__version__` is read from the installed package metadata so it tracks
`pyproject.toml` automatically — no manual bump in two places at every
release. Falls back to "0.0.0+unknown" if the package isn't installed
(e.g. running directly from a source checkout without `pip install -e .`).
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mongosemantic")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
