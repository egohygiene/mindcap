from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

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
from mindcap.core.progress import CaptureProgressReporter, CaptureStats
from mindcap.plugins.chatgpt.strategies.browser import (
    _find_stable_chrome,
    _is_dedicated_chrome_running,
    _is_profile_locked,
    authenticate_chatgpt,
    browser_capture_architecture,
    verify_chatgpt_authentication,
)
from mindcap.plugins.suno.auth import authenticate_suno_cookie_stdin
from mindcap.plugins.suno.doctor import doctor_suno as run_suno_doctor
from mindcap.registry import build_registry
from mindcap.storage import verify_bundle

app = typer.Typer(
    name="mindcap",
    no_args_is_help=True,
    help="Capture and normalize sources for knowledge extraction.",
)
auth_app = typer.Typer(no_args_is_help=True, help="Manage source authentication.")
plugins_app = typer.Typer(no_args_is_help=True, help="Inspect source plugins.")
doctor_app = typer.Typer(no_args_is_help=True, help="Inspect safe browser diagnostics.")
inspect_app = typer.Typer(no_args_is_help=True, help="Inspect captured archives.")
app.add_typer(auth_app, name="auth")
app.add_typer(plugins_app, name="plugins")
app.add_typer(doctor_app, name="doctor")
app.add_typer(inspect_app, name="inspect")
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
    strategy_name: str | None,
    output: Path | None,
    wait_seconds: float,
    options: dict[str, Any],
    identifier_override: str | None = None,
) -> None:
    verbose: bool = bool(options.get("verbose"))
    debug: bool = bool(options.get("debug"))
    quiet: bool = bool(options.get("quiet")) and not bool(options.get("json"))
    reporter = CaptureProgressReporter(
        console=console,
        verbose=verbose,
        debug=debug,
        quiet=quiet,
    )
    start_time = time.monotonic()
    try:
        registry = build_registry()
        plugin = registry.get(source_type)
        selected_strategy = strategy_name or plugin.default_strategy()
        identifier_source = identifier_override or source
        identifier, canonical_url = plugin.canonicalize(identifier_source)
        request = CaptureRequest(
            source_type=source_type,
            source=source,
            provider=source_type,
            canonical_identifier=identifier,
            canonical_url=canonical_url,
            strategy=selected_strategy,
            artifact_root=(output or default_artifact_root()).resolve(),
            wait_seconds=wait_seconds,
            options=options,
        )
        reporter.phase("Authenticating...")
        strategy_obj = plugin.strategy(selected_strategy, reporter=reporter)
        envelope = strategy_obj.capture(request)
        reporter.phase("Generating manifests...")
        normalized = plugin.normalize(envelope, identifier)
        transcript = plugin.render(normalized)
        reporter.phase("Verifying archive...")
        stored = plugin.storage().persist(request, envelope, normalized, transcript)
        elapsed = time.monotonic() - start_time
        if options.get("json"):
            console.print_json(
                data={
                    "status": stored.status,
                    "source_id": stored.source_id,
                    "version": stored.version,
                    "path": str(stored.path),
                }
            )
        elif quiet:
            console.print(str(stored.path))
        else:
            safe_meta = envelope.safe_metadata
            stats = CaptureStats(
                clips_archived=int(safe_meta.get("clip_count") or 0),
                audio_downloaded=int(safe_meta.get("audio_count") or 0),
                artwork_downloaded=int(safe_meta.get("artwork_count") or 0),
                videos_downloaded=int(safe_meta.get("video_count") or 0),
                bytes_downloaded=int(safe_meta.get("bytes_downloaded") or 0),
                elapsed_seconds=elapsed,
                verification_passed=stored.status in ("complete", "unchanged"),
                warnings=list(envelope.warnings),
            )
            reporter.summary(
                stats,
                project_name=str(safe_meta["project_title"])
                if safe_meta.get("project_title")
                else None,
                archive_path=str(stored.path),
            )
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


@auth_app.command("suno")
def auth_suno(
    cookie_stdin: Annotated[
        bool,
        typer.Option(
            "--cookie-stdin",
            help="Read the Clerk __client cookie or Cookie header from stdin.",
        ),
    ] = False,
) -> None:
    """Store Suno authentication state outside the repository."""
    if not cookie_stdin:
        _fail(ValueError("Pass --cookie-stdin and pipe the cookie value over stdin."))
    try:
        authenticate_suno_cookie_stdin(sys.stdin.read())
        console.print("[bold green]Suno authentication state saved.[/bold green]")
    except Exception as error:
        _fail(error)


@app.command()
def capture(
    source_type: Annotated[str, typer.Argument(help="Registered source plugin.")],
    source: Annotated[str, typer.Argument(help="Source URL or identifier.")],
    strategy: Annotated[
        str | None, typer.Option("--strategy", help="Acquisition strategy.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Private artifact root.")
    ] = None,
    wait_seconds: Annotated[
        float,
        typer.Option("--wait-seconds", min=1.0, max=120.0),
    ] = 10.0,
    audio_format: Annotated[
        str,
        typer.Option("--audio-format", help="Preferred Suno audio format."),
    ] = "mp3",
    include_video: Annotated[
        bool,
        typer.Option("--include-video/--no-include-video"),
    ] = True,
    request_wav: Annotated[
        bool,
        typer.Option("--request-wav", help="Request WAV metadata when supported."),
    ] = False,
    include_stems: Annotated[
        bool,
        typer.Option("--include-stems", help="Request stem metadata when supported."),
    ] = False,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", min=1, max=16),
    ] = 4,
    force: Annotated[
        bool,
        typer.Option("--force", help="Force a new archive version."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable capture output."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Print only the resulting bundle path."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit per-asset detail during capture."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Emit low-level diagnostics (no secrets)."),
    ] = False,
) -> None:
    """Capture a source through a registered plugin and strategy."""
    _capture(
        source_type,
        source,
        strategy,
        output,
        wait_seconds,
        {
            "audio_format": audio_format,
            "include_video": include_video,
            "request_wav": request_wav,
            "include_stems": include_stems,
            "concurrency": concurrency,
            "force": force,
            "json": json_output,
            "quiet": quiet,
            "verbose": verbose,
            "debug": debug,
        },
    )


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
        {},
        identifier_override=identifier,
    )


@app.command()
def verify(
    bundle_path: Annotated[Path, typer.Argument(help="Finalized version bundle.")],
) -> None:
    """Verify stored artifact hashes and required files."""
    try:
        verify_bundle(bundle_path.expanduser().resolve())
        console.print("[green]✓ Manifest[/green]")
        console.print("[green]✓ Checksums[/green]")
        console.print()
        console.print("[bold green]PASS[/bold green]")
    except (MindcapError, OSError, KeyError, TypeError) as error:
        _fail(error)


@plugins_app.command("list")
def list_plugins() -> None:
    """List registered source plugins."""
    table = Table("Source plugin", "Initial strategies")
    registry = build_registry()
    for name in registry.names():
        plugin = registry.get(name)
        table.add_row(name, ", ".join(plugin.strategies()))
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


@doctor_app.command("suno")
def doctor_suno(
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Print privacy-safe Suno authentication and API diagnostics."""
    try:
        run_suno_doctor(console, verbose=verbose)
    except Exception as error:
        _fail(error)


@inspect_app.command("suno")
def inspect_suno(
    archive: Annotated[Path, typer.Argument(help="Suno workspace bundle directory.")],
) -> None:
    """Inspect a captured Suno workspace archive."""
    from mindcap.plugins.suno.archive.inspector import inspect_suno_archive

    try:
        inspect_suno_archive(archive, console)
    except (MindcapError, OSError) as error:
        _fail(error)
