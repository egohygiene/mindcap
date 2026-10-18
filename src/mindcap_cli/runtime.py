"""Command boundaries: consistent diagnostics without changing library callers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer
from typer.core import TyperCommand, TyperGroup

from mindcap_cli._compat import click, get_current_context
from mindcap_cli.logging import DiagnosticLogError, diagnostic_session, render_error

logger = logging.getLogger("mindcap.cli")
CommandFunction = TypeVar("CommandFunction", bound=Callable[..., Any])


def diagnostic_options(ctx: click.Context | None = None) -> dict[str, Any]:
    """Combine global diagnostics with backwards-compatible command options."""
    ctx = ctx or get_current_context(silent=True)
    options: dict[str, Any] = {
        "verbose": False,
        "debug": False,
        "quiet": False,
        "log_file": None,
        "json_output": False,
    }
    contexts = []
    while ctx is not None:
        contexts.append(ctx)
        ctx = ctx.parent
    for current in reversed(contexts):
        for key in ("verbose", "debug", "quiet", "json_output"):
            options[key] = options[key] or bool(current.params.get(key))
        if current.params.get("asset_diagnostics"):
            options["json_output"] = True
        if current.meta.get("mindcap_json_requested"):
            options["json_output"] = True
        if current.params.get("log_file") is not None:
            options["log_file"] = Path(current.params["log_file"])
    return options


def fail(error: Exception) -> NoReturn:
    options = diagnostic_options()
    render_error(error, json_output=options["json_output"], debug=options["debug"])
    raise typer.Exit(1) from error


class DiagnosticCommand(TyperCommand):
    """Keep all operational failures inside the CLI's safe output boundary."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # The token parser does no conversion/callbacks. Remember a requested
        # JSON mode even when another option fails conversion before --json.
        try:
            raw, _, _ = self.make_parser(ctx).parse_args(args=list(args))
            ctx.meta["mindcap_json_requested"] = bool(
                raw.get("json_output") or raw.get("asset_diagnostics")
            )
            return super().parse_args(ctx, args)
        except click.ClickException as error:
            _usage_error(error, ctx)

    def invoke(self, ctx: click.Context) -> Any:
        options = diagnostic_options(ctx)
        for flag in ("verbose", "debug", "quiet"):
            if flag in ctx.params:
                ctx.params[flag] = options[flag]
        started = time.monotonic()
        try:
            with diagnostic_session(
                verbose=options["verbose"],
                debug=options["debug"],
                quiet=options["quiet"],
                log_file=options["log_file"],
            ):
                logger.debug("Starting %s", ctx.command_path)
                try:
                    result = super().invoke(ctx)
                except (KeyboardInterrupt, typer.Abort) as error:
                    render_error(typer.Abort(), json_output=options["json_output"])
                    raise typer.Exit(130) from error
                except (typer.Exit, click.ClickException):
                    raise
                except Exception as error:
                    fail(error)
                else:
                    logger.info(
                        "Finished %s in %.2fs",
                        ctx.command_path,
                        time.monotonic() - started,
                        extra={"progress_event": True, "event": "command_completed"},
                    )
                    return result
        except (OSError, DiagnosticLogError) as error:
            # Includes failure to open the requested diagnostic file. Never let
            # setup failures fall through to Typer's traceback renderer.
            fail(error)


class DiagnosticGroup(TyperGroup):
    """Redact nested usage errors and keep their normal usage exit status."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.ClickException as error:
            if isinstance(error, click.exceptions.NoArgsIsHelpError):
                raise
            _usage_error(error, ctx)

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.ClickException as error:
            _usage_error(error, getattr(error, "ctx", None) or ctx)


def _usage_error(error: click.ClickException, ctx: click.Context) -> NoReturn:
    options = diagnostic_options(ctx)
    render_error(error, json_output=options["json_output"], debug=options["debug"])
    raise typer.Exit(error.exit_code) from error


class DiagnosticTyper(typer.Typer):
    """Give current and future registered commands the same error boundary."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("cls", DiagnosticGroup)
        kwargs.setdefault("pretty_exceptions_enable", False)
        super().__init__(*args, **kwargs)

    def command(
        self, *args: Any, **kwargs: Any
    ) -> Callable[[CommandFunction], CommandFunction]:
        kwargs.setdefault("cls", DiagnosticCommand)
        return super().command(*args, **kwargs)
