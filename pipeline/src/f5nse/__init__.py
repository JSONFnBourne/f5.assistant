"""
f5nse package bootstrap.

Provides utilities and CLI entrypoints for the f5nse fine-tuning pipeline.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("f5nse")
except PackageNotFoundError:  # pragma: no cover - during editable installs
    __version__ = "0.0.0"


__all__ = ["__version__"]
