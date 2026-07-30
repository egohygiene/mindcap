"""Mindcap source-capture toolkit.

Public API
----------
The stable public interface is::

    from mindcap import MindcapApplication
    from mindcap.contracts import CaptureCommand, CaptureResult
    from mindcap.registry import PluginRegistry, create_default_registry

Usage example::

    app = MindcapApplication.default()
    result = app.capture(CaptureCommand(provider="chatgpt", source="..."))
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("egohygiene-mindcap")
except PackageNotFoundError:
    __version__ = "0.1.0"

from mindcap.application.facade import MindcapApplication
from mindcap.registry import PluginRegistry, build_registry, create_default_registry

__all__ = [
    "MindcapApplication",
    "PluginRegistry",
    "__version__",
    "build_registry",
    "create_default_registry",
]
