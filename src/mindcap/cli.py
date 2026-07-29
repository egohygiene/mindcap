from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mindcap import __version__
from mindcap.config import (
    chatgpt_profile_dir,
    default_artifact_root,
    find_repository_root,
)
from mindcap.core.errors import MindcapError
from mindcap.core.models import CaptureRequest
from mindcap.plugins.chatgpt.strategies.browser import (
    _find_stable_chrome,
    _is_dedicated_chrome_running,
    _is_profile_locked,
    authenticate_chatgpt,
    browser_capture_architecture,
    verify_chatgpt_authentication,
)
from mindcap.registry import build_registry
from mindcap.storage.filesystem import FilesystemStorageStrategy

app = typer.Typer(
    name="mindcap",
    no_args_is_help=True,
    help="Capture and normalize sources for knowledge extraction.",
)
auth_app = typer.Typer(no_args_is_help=True, help="Manage source authentication.")
plugins_app = typer.Typer(no_args_is_help=True, help="Inspect source plugins.")
doctor_app = typer.Typer(no_args_is_help=True, help="Inspect safe browser diagnostics.")
app.add_typer(auth_app, name="auth")
app.add_typer(plugins_app, name="plugins")
app.add_typer(doctor_app, name="doctor")
console = Console()


def _print_version() -> None:
    """Print the installed Mindcap version."""
    console.print(__version__)


def _version_callback(value: bool) -> None:
    """Handle the eager --version option."""
    if value:
        _print_version()
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show Mindcap version and exit.",
            is_eager=True,
            callback=_version_callback,
        ),
    ] = False,
) -> None:
    """Configure eager top-level options for the Mindcap CLI."""


def _fail(error: Exception) -> None:
    console.print(f"[bold red]Mindcap error:[/bold red] {error}")
    raise typer.Exit(code=1) from error


def _capture(
    source_type: str,
    source: str,
    strategy_name: str,
    output: Path | None,
    wait_seconds: float,
    identifier_override: str | None = None,
) -> None:
    try:
        registry = build_registry()
        plugin = registry.get(source_type)
        identifier_source = identifier_override or source
        identifier, canonical_url = plugin.canonicalize(identifier_source)
        request = CaptureRequest(
            source_type=source_type,
            source=source,
            provider=source_type,
            canonical_identifier=identifier,
            canonical_url=canonical_url,
            strategy=strategy_name,
            artifact_root=(output or default_artifact_root()).resolve(),
            wait_seconds=wait_seconds,
        )
        envelope = plugin.strategy(strategy_name).capture(request)
        normalized = plugin.normalize(envelope, identifier)
        transcript = plugin.render(normalized)
        stored = FilesystemStorageStrategy().persist(
            request, envelope, normalized, transcript
        )
        console.print(
            f"[bold green]{stored.status.title()}[/bold green] "
            f"[cyan]{stored.source_id}[/cyan] version {stored.version}"
        )
        console.print(str(stored.path))
    except (MindcapError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)


@auth_app.command("chatgpt")
def auth_chatgpt() -> None:
    """Log into the dedicated persistent ChatGPT browser profile."""
    try:
        authenticate_chatgpt()
        console.print("[bold green]Dedicated ChatGPT profile saved.[/bold green]")
    except Exception as error:
        _fail(error)


@app.command()
def capture(
    source_type: Annotated[str, typer.Argument(help="Registered source plugin.")],
    source: Annotated[str, typer.Argument(help="Source URL or identifier.")],
    strategy: Annotated[
        str, typer.Option("--strategy", help="Acquisition strategy.")
    ] = "browser",
    output: Annotated[
        Path | None, typer.Option("--output", help="Private artifact root.")
    ] = None,
    wait_seconds: Annotated[
        float,
        typer.Option("--wait-seconds", min=1.0, max=120.0),
    ] = 10.0,
) -> None:
    """Capture a source through a registered plugin and strategy."""
    _capture(source_type, source, strategy, output, wait_seconds)


@app.command("import")
def import_source(
    source_type: Annotated[str, typer.Argument(help="Registered source plugin.")],
    path: Annotated[Path, typer.Argument(help="Previously saved source JSON.")],
    conversation_id: Annotated[
        str | None,
        typer.Option(
            "--conversation-id",
            help="Conversation ID when it cannot be derived from the JSON filename.",
        ),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Import a previously exported or captured JSON artifact."""
    identifier = conversation_id
    if identifier is None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                identifier = str(
                    payload.get("id") or payload.get("conversation_id") or ""
                )
        except (OSError, json.JSONDecodeError) as error:
            _fail(error)
    if not identifier:
        _fail(ValueError("Could not derive a conversation ID. Pass --conversation-id."))
    _capture(
        source_type,
        str(path),
        "saved-json",
        output,
        10.0,
        identifier_override=identifier,
    )


@app.command()
def verify(
    bundle_path: Annotated[Path, typer.Argument(help="Finalized version bundle.")],
) -> None:
    """Verify stored artifact hashes and required files."""
    try:
        FilesystemStorageStrategy().verify(bundle_path.expanduser().resolve())
        console.print("[bold green]Bundle verification passed.[/bold green]")
    except (MindcapError, OSError, KeyError, TypeError) as error:
        _fail(error)


@plugins_app.command("list")
def list_plugins() -> None:
    """List registered source plugins."""
    table = Table("Source plugin", "Initial strategies")
    registry = build_registry()
    for name in registry.names():
        strategies = "browser, saved-json" if name == "chatgpt" else ""
        table.add_row(name, strategies)
    console.print(table)


@app.command()
def paths() -> None:
    """Display artifact and authentication-state locations."""
    table = Table("Purpose", "Path", "Archive this?")
    table.add_row("Artifacts", str(default_artifact_root()), "Yes, after review")
    table.add_row(
        "ChatGPT browser profile",
        str(chatgpt_profile_dir()),
        "No — contains authentication state",
    )
    console.print(table)


@app.command("version")
def version() -> None:
    """Print the installed Mindcap version."""
    _print_version()


def _is_artifact_root_git_ignored(path: Path) -> bool:
    repo = find_repository_root()
    try:
        result = subprocess.run(
            ["git", "check-ignore", str(path)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


@doctor_app.command("chatgpt")
def doctor_chatgpt(
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Print privacy-safe ChatGPT browser diagnostics."""
    profile = chatgpt_profile_dir()
    chrome = None
    chrome_discovery = "not found"
    try:
        chrome = _find_stable_chrome()
        chrome_discovery = "found"
    except Exception:
        chrome = None
    auth = verify_chatgpt_authentication() if chrome else None

    table = Table("Check", "Status")
    table.add_row("Operating system", platform.platform())
    table.add_row("Mindcap version", __version__)
    table.add_row("Stable Chrome discovery", chrome_discovery)
    table.add_row("Chrome executable path", str(chrome) if chrome else "unavailable")
    table.add_row("Dedicated user-data directory", str(profile))
    table.add_row("Expected profile subdirectory", str(profile / "Default"))
    table.add_row("Profile directory exists", "yes" if profile.is_dir() else "no")
    table.add_row(
        "Profile appears locked", "yes" if _is_profile_locked(profile) else "no"
    )
    table.add_row(
        "Dedicated Chrome process running",
        "yes" if _is_dedicated_chrome_running(profile) else "no",
    )
    table.add_row("Selected capture architecture", browser_capture_architecture())
    table.add_row("CDP support available", "yes" if chrome else "no")
    if auth is None:
        table.add_row("Authentication status", "indeterminate (Chrome unavailable)")
    else:
        table.add_row("Authentication status", auth.state.value)
    table.add_row(
        "Archive output Git-ignored",
        "yes" if _is_artifact_root_git_ignored(default_artifact_root()) else "no",
    )
    console.print(table)

    if verbose:
        detail_table = Table("Verbose detail", "Value")
        detail_table.add_row(
            "Authentication detail",
            auth.detail
            if auth is not None
            else "Skipped because Chrome was unavailable.",
        )
        detail_table.add_row(
            "Archive output path",
            str(default_artifact_root()),
        )
        console.print(detail_table)
