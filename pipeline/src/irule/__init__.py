"""
irule package bootstrap.

Provides utilities and CLI entrypoints for the irule fine-tuning pipeline.
"""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("irule")
except PackageNotFoundError:  # pragma: no cover - during editable installs
    __version__ = "0.0.0"


__all__ = ["__version__"]
