"""Backward-compatibility shim for mindcap.cli.

The CLI implementation has moved to ``mindcap_cli.app``.  This module
re-exports the public Typer application for any code that still imports
from the old location.
"""

from __future__ import annotations

from mindcap_cli.app import app

__all__ = ["app"]
