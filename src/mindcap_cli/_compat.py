"""Use the command engine owned by the installed Typer version."""

try:
    from typer import _click as click
    from typer._click.globals import get_current_context
except ImportError:  # Typer versions before the command engine was vendored.
    import click  # type: ignore[no-redef,import-not-found,unused-ignore]
    from click import get_current_context  # type: ignore[no-redef]

__all__ = ["click", "get_current_context"]
