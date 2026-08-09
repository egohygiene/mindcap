from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from mindcap import __version__
from mindcap.config import (
    chatgpt_profile_dir,
    default_artifact_root,
    distrokid_profile_dir,
    find_repository_root,
)
from mindcap.core.errors import MindcapError
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.core.progress import CaptureProgressReporter, CaptureStats
from mindcap.integrations.google_drive.auth import authenticate_google_drive
from mindcap.integrations.google_drive.doctor import doctor_google_drive
from mindcap.plugins.chatgpt.strategies.browser import (
    _find_stable_chrome,
    _is_dedicated_chrome_running,
    _is_profile_locked,
    authenticate_chatgpt,
    browser_capture_architecture,
    verify_chatgpt_authentication,
)
from mindcap.plugins.chrome_bookmarks.diagnostics import (
    doctor_chrome_bookmarks as run_chrome_bookmarks_doctor,
)
from mindcap.plugins.distrokid.doctor import doctor_distrokid as run_distrokid_doctor
from mindcap.plugins.distrokid.strategies.browser import authenticate_distrokid
from mindcap.plugins.soundcloud.doctor import doctor_soundcloud as run_soundcloud_doctor
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
sync_app = typer.Typer(
    no_args_is_help=True,
    help="Provider-wide cache-aware synchronization.",
)
vault_app = typer.Typer(
    no_args_is_help=True,
    help="Ingest, inspect, verify, and restore immutable archival vaults.",
)
app.add_typer(auth_app, name="auth")
app.add_typer(plugins_app, name="plugins")
app.add_typer(doctor_app, name="doctor")
app.add_typer(inspect_app, name="inspect")
app.add_typer(sync_app, name="sync")
app.add_typer(vault_app, name="vault")
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


def _fmt_bytes_human(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _vault_service() -> Any:
    from mindcap.vault.service import VaultService

    return VaultService()


def _capture(
    source_type: str,
    source: str | None,
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
        identifier_source = identifier_override or source or ""
        identifier, canonical_url = plugin.canonicalize(identifier_source)
        request = CaptureRequest(
            source_type=source_type,
            source=source or "",
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


def _capture_export(
    source: str,
    output: Path | None,
    options: dict[str, Any],
    conversation_id_filter: str | None = None,
) -> None:
    """Batch-ingest a ChatGPT official export ZIP or directory."""
    from mindcap.core.errors import (
        MissingConversationIdError,
        UnsupportedConversationSchemaError,
        UnsupportedExportError,
    )
    from mindcap.plugins.chatgpt.plugin import ChatGPTPlugin
    from mindcap.plugins.chatgpt.strategies.export import ExportCaptureStrategy

    quiet: bool = bool(options.get("quiet")) and not bool(options.get("json"))
    verbose: bool = bool(options.get("verbose"))
    artifact_root = (output or default_artifact_root()).resolve()
    start_time = time.monotonic()

    plugin = ChatGPTPlugin()
    export_strategy = ExportCaptureStrategy()

    try:
        discovery = export_strategy.discover(source)
    except (MindcapError, OSError) as error:
        _fail(error)
        return

    if not quiet:
        console.print(
            f"[bold]Inspecting export:[/bold] {source}\n"
            f"  Conversation files: {len(discovery.conversation_files)}\n"
            f"  Metadata files:     {len(discovery.metadata_files)}\n"
            f"  Unknown files:      {len(discovery.unknown_files)}"
        )
        for w in discovery.warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")

    import_id = f"import-{uuid.uuid4().hex[:16]}"
    import_root = artifact_root / "imports" / "chatgpt" / import_id
    import_root.mkdir(parents=True, exist_ok=True)

    imported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    all_warnings: list[str] = list(discovery.warnings)

    total_discovered = 0

    if not quiet:
        console.print("\n[bold]Parsing conversations...[/bold]")

    try:
        for record in export_strategy.iter_conversations(
            source, conversation_id=conversation_id_filter
        ):
            total_discovered += 1
            conv_id = record.conversation_id
            try:
                envelope = CaptureEnvelope(
                    provider="chatgpt",
                    source_type="conversation",
                    canonical_identifier=conv_id,
                    canonical_url=f"https://chatgpt.com/c/{conv_id}",
                    captured_at=datetime.now(UTC),
                    strategy="export",
                    response_units=[
                        RawResponseUnit(
                            unit_id="response-000",
                            sequence=0,
                            media_type="application/json",
                            body=record.raw_bytes,
                        )
                    ],
                    safe_metadata={
                        "input_kind": "export",
                        "source_file": record.source_file,
                        "raw_sha256": record.sha256,
                    },
                )
                request = CaptureRequest(
                    source_type="chatgpt",
                    source=source or "",
                    provider="chatgpt",
                    canonical_identifier=conv_id,
                    canonical_url=f"https://chatgpt.com/c/{conv_id}",
                    strategy="export",
                    artifact_root=artifact_root,
                )
                normalized = plugin.normalize(envelope, conv_id)
                transcript = plugin.render(normalized)
                stored = plugin.storage().persist(
                    request, envelope, normalized, transcript
                )
                entry: dict[str, Any] = {
                    "conversation_id": conv_id,
                    "status": stored.status,
                    "version": stored.version,
                    "bundle_path": str(stored.path),
                    "source_file": record.source_file,
                    "raw_sha256": record.sha256,
                }
                if stored.status == "unchanged":
                    unchanged.append(entry)
                else:
                    imported.append(entry)
                if verbose:
                    console.print(f"  [{stored.status}] {conv_id} → {stored.path.name}")
            except (MindcapError, OSError, ValueError, json.JSONDecodeError) as exc:
                failed.append(
                    {
                        "conversation_id": conv_id,
                        "status": "failed",
                        "error": str(exc),
                        "source_file": record.source_file,
                    }
                )
                all_warnings.append(f"Failed to import conversation {conv_id}: {exc}")
    except (
        UnsupportedExportError,
        UnsupportedConversationSchemaError,
        MissingConversationIdError,
        MindcapError,
        OSError,
    ) as error:
        _fail(error)
        return

    elapsed = time.monotonic() - start_time

    # Write import manifest.
    manifest: dict[str, Any] = {
        "schema": "mindcap.import-manifest/v0.1",
        "import_id": import_id,
        "source": source,
        "source_sha256": discovery.source_sha256,
        "import_timestamp": datetime.now(UTC).isoformat(),
        "conversations_discovered": total_discovered,
        "conversations_imported": len(imported),
        "conversations_unchanged": len(unchanged),
        "conversations_failed": len(failed),
        "warnings": all_warnings,
        "elapsed_seconds": round(elapsed, 2),
    }
    conversations_index: dict[str, Any] = {
        "import_id": import_id,
        "imported": imported,
        "unchanged": unchanged,
        "failed": failed,
    }
    (import_root / "import-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (import_root / "conversations-index.json").write_text(
        json.dumps(conversations_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if all_warnings:
        (import_root / "warnings.json").write_text(
            json.dumps(all_warnings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if options.get("json"):
        console.print_json(data=manifest)
    elif quiet:
        console.print(str(import_root))
    else:
        mins, secs = divmod(int(elapsed), 60)
        elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        table = Table(title="ChatGPT import complete", show_header=False)
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_row("Conversations discovered", str(total_discovered))
        table.add_row(
            "Imported",
            f"[green]{len(imported)}[/green]",
        )
        table.add_row(
            "Unchanged",
            str(len(unchanged)),
        )
        table.add_row(
            "Failed",
            f"[red]{len(failed)}[/red]" if failed else "0",
        )
        table.add_row("Warnings", str(len(all_warnings)))
        table.add_row("Elapsed", elapsed_str)
        table.add_row("Import manifest", str(import_root))
        console.print(table)

    if failed:
        raise typer.Exit(code=1)


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


@auth_app.command("distrokid")
def auth_distrokid() -> None:
    """Log into the dedicated persistent DistroKid browser profile."""
    try:
        authenticate_distrokid()
        console.print("[bold green]Dedicated DistroKid profile saved.[/bold green]")
    except Exception as error:
        _fail(error)


@auth_app.command("google-drive")
def auth_google_drive(
    client_secrets_file: Annotated[
        Path,
        typer.Option(
            "--client-secrets-file",
            help="Path to Google OAuth desktop client configuration JSON.",
        ),
    ],
) -> None:
    """Configure Google Drive OAuth settings for the vault backend."""
    try:
        profile = authenticate_google_drive(client_secrets_file)
        console.print(
            "[bold green]Google Drive authentication configured.[/bold green] "
            f"account={profile.account} scope={profile.scope}"
        )
    except Exception as error:
        _fail(error)


@app.command()
def capture(
    source_type: Annotated[str, typer.Argument(help="Registered source plugin.")],
    source: Annotated[
        str | None,
        typer.Argument(
            help="Source URL, identifier, or provider-specific local selector."
        ),
    ] = None,
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
    conversation_id: Annotated[
        str | None,
        typer.Option(
            "--conversation-id",
            help="Filter export to a single conversation ID (export strategy only).",
        ),
    ] = None,
    user_data_dir: Annotated[
        list[Path] | None,
        typer.Option("--user-data-dir", help="Chrome user-data directory to inspect."),
    ] = None,
    profile: Annotated[
        list[str] | None,
        typer.Option("--profile", help="Chrome profile directory name to capture."),
    ] = None,
    channel: Annotated[
        str,
        typer.Option("--channel", help="Chrome release channel to inspect."),
    ] = "stable",
    all_profiles: Annotated[
        bool,
        typer.Option("--all-profiles", help="Capture all discovered Chrome profiles."),
    ] = False,
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
    include_releases: Annotated[
        bool,
        typer.Option(
            "--include-releases", help="Capture each discovered library release."
        ),
    ] = False,
    include_audio: Annotated[
        bool,
        typer.Option("--include-audio/--no-include-audio"),
    ] = True,
    require_audio: Annotated[
        bool,
        typer.Option(
            "--require-audio", help="Fail capture when expected audio is unavailable."
        ),
    ] = False,
    include_artwork: Annotated[
        bool,
        typer.Option("--include-artwork/--no-include-artwork"),
    ] = True,
    include_credits: Annotated[
        bool,
        typer.Option(
            "--include-credits", help="Include read-only release credits capture."
        ),
    ] = False,
    include_lyrics: Annotated[
        bool,
        typer.Option(
            "--include-lyrics", help="Include read-only release lyrics capture."
        ),
    ] = False,
    include_store_links: Annotated[
        bool,
        typer.Option("--include-store-links/--no-include-store-links"),
    ] = True,
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
    debug_discovery: Annotated[
        bool,
        typer.Option(
            "--debug-discovery",
            help="Emit secret-safe response schema discovery diagnostics.",
        ),
    ] = False,
) -> None:
    """Capture a source through a registered plugin and strategy."""
    # Export strategy requires batch processing handled separately.
    if source_type == "chatgpt" and strategy == "export":
        _capture_export(
            source=source or "",
            output=output,
            conversation_id_filter=conversation_id,
            options={
                "user_data_dirs": [str(item) for item in (user_data_dir or [])],
                "profiles": list(profile or []),
                "channel": channel,
                "all_profiles": all_profiles,
                "force": force,
                "json": json_output,
                "quiet": quiet,
                "verbose": verbose,
                "debug": debug,
            },
        )
        return
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
            "include_releases": include_releases,
            "include_audio": include_audio,
            "require_audio": require_audio,
            "include_artwork": include_artwork,
            "include_credits": include_credits,
            "include_lyrics": include_lyrics,
            "include_store_links": include_store_links,
            "concurrency": concurrency,
            "user_data_dirs": [str(item) for item in (user_data_dir or [])],
            "profiles": list(profile or []),
            "channel": channel,
            "all_profiles": all_profiles,
            "force": force,
            "json": json_output,
            "quiet": quiet,
            "verbose": verbose,
            "debug": debug,
            "debug_discovery": debug_discovery,
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
    from mindcap.config import soundcloud_profile_dir

    table = Table("Purpose", "Path", "Archive this?")
    table.add_row("Artifacts", str(default_artifact_root()), "Yes, after review")
    table.add_row(
        "ChatGPT browser profile",
        str(chatgpt_profile_dir()),
        "No — contains authentication state",
    )
    table.add_row(
        "DistroKid browser profile",
        str(distrokid_profile_dir()),
        "No — contains authentication state",
    )
    table.add_row(
        "SoundCloud browser profile",
        str(soundcloud_profile_dir()),
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


@doctor_app.command("distrokid")
def doctor_distrokid(
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Print privacy-safe DistroKid browser diagnostics."""
    try:
        run_distrokid_doctor(console, verbose=verbose)
    except Exception as error:
        _fail(error)


@doctor_app.command("chrome-bookmarks")
def doctor_chrome_bookmarks(
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Print privacy-safe Google Chrome bookmark discovery diagnostics."""
    try:
        run_chrome_bookmarks_doctor(console, verbose=verbose)
    except Exception as error:
        _fail(error)


@doctor_app.command("soundcloud")
def doctor_soundcloud(
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Print privacy-safe SoundCloud authentication and API diagnostics."""
    try:
        run_soundcloud_doctor(console, verbose=verbose)
    except Exception as error:
        _fail(error)


@doctor_app.command("google-drive")
def doctor_google_drive_command(
    client_secrets_file: Annotated[
        Path | None,
        typer.Option(
            "--client-secrets-file",
            help="Optional Google OAuth desktop client configuration JSON.",
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Run safe Google Drive diagnostics."""
    try:
        diagnostics = doctor_google_drive(client_secrets_file)
    except Exception as error:
        _fail(error)
        return
    if json_output:
        console.print_json(data=diagnostics)
        return
    table = Table(title="Mindcap Google Drive Doctor", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Status", diagnostics["status"])
    table.add_row("Scope", diagnostics["scope"])
    table.add_row("Client secrets file", diagnostics["client_secrets_file"])
    console.print(table)


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


@inspect_app.command("distrokid")
def inspect_distrokid(
    archive: Annotated[
        Path, typer.Argument(help="DistroKid release/library bundle directory.")
    ],
) -> None:
    """Inspect a captured DistroKid archive."""
    from mindcap.plugins.distrokid.archive.inspector import inspect_distrokid_archive

    try:
        inspect_distrokid_archive(archive, console)
    except (MindcapError, OSError) as error:
        _fail(error)


@inspect_app.command("soundcloud")
def inspect_soundcloud(
    archive: Annotated[
        Path, typer.Argument(help="SoundCloud archive bundle directory (v<N> folder).")
    ],
) -> None:
    """Inspect a captured SoundCloud archive."""
    from mindcap.plugins.soundcloud.archive.inspector import inspect_soundcloud_archive

    try:
        inspect_soundcloud_archive(archive, console)
    except (MindcapError, OSError) as error:
        _fail(error)


@inspect_app.command("chatgpt")
def inspect_chatgpt(
    archive: Annotated[
        Path, typer.Argument(help="ChatGPT conversation bundle or import directory.")
    ],
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    """Inspect a captured ChatGPT conversation archive or import manifest."""
    path = archive.expanduser().resolve()
    if not path.exists():
        _fail(FileNotFoundError(f'Archive path does not exist: "{path}"'))
        return

    # Import manifest directory.
    import_manifest_path = path / "import-manifest.json"
    if import_manifest_path.is_file():
        try:
            manifest = json.loads(import_manifest_path.read_text(encoding="utf-8"))
            index_path = path / "conversations-index.json"
            index: dict[str, Any] = (
                json.loads(index_path.read_text(encoding="utf-8"))
                if index_path.is_file()
                else {}
            )
        except (OSError, json.JSONDecodeError) as error:
            _fail(error)
            return
        table = Table(
            title=("ChatGPT Import - " + str(manifest.get("import_id", "unknown")))
        )
        table.add_column("Field", style="bold")
        table.add_column("Value")
        table.add_row("Import ID", str(manifest.get("import_id", "-")))
        table.add_row("Source", str(manifest.get("source", "-")))
        table.add_row("Import timestamp", str(manifest.get("import_timestamp", "-")))
        table.add_row(
            "Conversations discovered",
            str(manifest.get("conversations_discovered", "-")),
        )
        table.add_row("Imported", str(manifest.get("conversations_imported", "-")))
        table.add_row("Unchanged", str(manifest.get("conversations_unchanged", "-")))
        table.add_row("Failed", str(manifest.get("conversations_failed", "-")))
        table.add_row("Warnings", str(len(manifest.get("warnings") or [])))
        table.add_row("Elapsed", f"{manifest.get('elapsed_seconds', '-')}s")
        console.print(table)
        if verbose and index.get("failed"):
            fail_table = Table(title="Failed conversations")
            fail_table.add_column("ID")
            fail_table.add_column("Error")
            for entry in index["failed"]:
                fail_table.add_row(
                    str(entry.get("conversation_id", "-")),
                    str(entry.get("error", "-")),
                )
            console.print(fail_table)
        return

    # Conversation bundle (vN directory with manifest.yaml).
    manifest_yaml_path = path / "manifest.yaml"
    if manifest_yaml_path.is_file():
        try:
            bundle_manifest = yaml.safe_load(
                manifest_yaml_path.read_text(encoding="utf-8")
            )
            normalized_path = path / "normalized" / "conversation.json"
            normalized: dict[str, Any] = {}
            if normalized_path.is_file():
                normalized = json.loads(normalized_path.read_bytes())
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
            _fail(error)
            return
        conv = bundle_manifest.get("conversation") or {}
        graph_integrity = normalized.get("graph_integrity") or {}
        provider_id = bundle_manifest.get("provider_id", "unknown")
        table = Table(title=f"ChatGPT Conversation - {provider_id}")
        table.add_column("Field", style="bold")
        table.add_column("Value")
        raw_title = normalized.get("title") or conv.get("title") or "-"
        table.add_row("Title", str(raw_title))
        table.add_row("Provider ID", str(bundle_manifest.get("provider_id", "-")))
        table.add_row(
            "Capture version",
            str(bundle_manifest.get("capture_version", "-")),
        )
        table.add_row(
            "Schema version",
            str(bundle_manifest.get("versions", {}).get("schema", "-")),
        )
        table.add_row("Captured at", str(bundle_manifest.get("captured_at", "-")))
        table.add_row("Strategy", str(bundle_manifest.get("strategy", "-")))

        def _conv_or_norm(key: str) -> str:
            return str(conv.get(key) or normalized.get(key) or "-")

        table.add_row("Total nodes", _conv_or_norm("provider_node_count"))
        table.add_row("Total messages", _conv_or_norm("provider_message_count"))
        table.add_row("Visible messages", _conv_or_norm("visible_message_count"))
        table.add_row("Hidden messages", _conv_or_norm("hidden_message_count"))
        table.add_row("Structural nodes", _conv_or_norm("structural_node_count"))
        table.add_row(
            "Attachments",
            str(len(normalized.get("attachments") or [])),
        )
        table.add_row(
            "Graph integrity complete",
            "yes" if graph_integrity.get("complete") else "no",
        )
        table.add_row(
            "Graph warnings",
            str(len(graph_integrity.get("warnings") or [])),
        )
        table.add_row(
            "Attachment warnings",
            str(len(normalized.get("attachment_warnings") or [])),
        )
        table.add_row(
            "Capture status",
            str(bundle_manifest.get("capture_status", "-")),
        )
        console.print(table)
        if verbose:
            for w in graph_integrity.get("warnings") or []:
                console.print(f"  [yellow]⚠ Graph: {w}[/yellow]")
            for w in normalized.get("attachment_warnings") or []:
                console.print(f"  [yellow]⚠ {w}[/yellow]")
        return

    _fail(
        ValueError(
            f'"{path}" does not appear to be a ChatGPT conversation bundle or '
            f"import manifest directory."
        )
    )


@vault_app.command("create")
def vault_create(
    storage_backend: Annotated[
        str,
        typer.Option(
            "--storage-backend",
            help="Vault storage backend (filesystem or google-drive).",
        ),
    ] = "filesystem",
    destination: Annotated[
        str | None,
        typer.Option(
            "--destination",
            help="Destination path for filesystem vault creation.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Requested Google Drive vault folder name."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Create an empty vault destination."""
    from mindcap.vault.backends import parse_vault_locator
    from mindcap.vault.layout import initialize_vault

    backend = storage_backend.strip().lower()
    created_at = datetime.now(UTC).isoformat()
    if backend == "filesystem":
        if not destination:
            _fail(ValueError("Pass --destination for filesystem vault creation."))
            return
        locator = parse_vault_locator(destination)
        if getattr(locator, "backend", "") != "filesystem":
            _fail(ValueError("Filesystem backend requires a local path destination."))
            return
        metadata = initialize_vault(
            locator.path,
            provider="vault",
            created_at=created_at,
        )
        payload = {
            "backend": "filesystem",
            "vault_id": metadata.vault_id,
            "locator": str(locator.path),
            "verification": "initialized",
        }
        if json_output:
            console.print_json(data=payload)
            return
        table = Table(title="Mindcap Vault Create", show_header=False)
        table.add_column("Field", style="bold")
        table.add_column("Value")
        table.add_row("Backend", payload["backend"])
        table.add_row("Vault ID", payload["vault_id"])
        table.add_row("Locator", payload["locator"])
        table.add_row("Verification", payload["verification"])
        console.print(table)
        return
    elif backend == "google-drive":
        if not name:
            _fail(
                ValueError(
                    "Pass --name when creating a Google Drive-backed vault "
                    "(for example: --name \"suno.mindcap-vault\")."
                )
            )
            return
        _fail(
            ValueError(
                "Google Drive vault creation is not available in this build yet. "
                "Use `task google-drive:auth`, then `task google-drive:doctor`, "
                "and rerun once the backend is enabled."
            )
        )
        return
    else:
        _fail(ValueError(f'Unsupported storage backend: "{storage_backend}"'))
    return


@vault_app.command("ingest")
def vault_ingest(
    provider: Annotated[str, typer.Option("--provider", help="Vault provider name.")],
    source: Annotated[
        Path, typer.Option("--source", help="Source bundle or workspace root.")
    ],
    destination: Annotated[
        str, typer.Option("--destination", help="Vault destination path or locator.")
    ],
    source_label: Annotated[
        str | None,
        typer.Option("--source-label", help="Optional human label for the source."),
    ] = None,
    pack_size_mib: Annotated[
        int,
        typer.Option(
            "--pack-size-mib", min=1, help="Target immutable pack size in MiB."
        ),
    ] = 512,
    staging_directory: Annotated[
        Path | None,
        typer.Option(
            "--staging-directory", help="Local staging directory for catalog work."
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Plan an ingestion without writing the vault."),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Ingest existing provider archives into an immutable vault."""
    try:
        summary = _vault_service().ingest(
            provider=provider,
            source=source,
            destination=destination,
            source_label=source_label,
            pack_size_mib=pack_size_mib,
            staging_directory=staging_directory,
            dry_run=dry_run,
        )
    except (MindcapError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)
        return
    if json_output:
        console.print_json(data=summary.to_dict())
        return
    table = Table(title="Mindcap Vault Ingest", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Vault", str(summary.vault_path))
    table.add_row("Dry run", "yes" if summary.dry_run else "no")
    table.add_row("Planning mode", summary.planning_mode)
    table.add_row("Archive units discovered", str(summary.discovered_archives))
    table.add_row("Archive units imported", str(summary.imported_archives))
    table.add_row(
        "Archive units already present", str(summary.already_present_archives)
    )
    table.add_row("Files examined", str(summary.files_examined))
    table.add_row("Unique objects added", str(summary.unique_objects_added))
    table.add_row("Objects deduplicated", str(summary.objects_deduplicated))
    table.add_row(
        "Logical source bytes", _fmt_bytes_human(summary.logical_source_bytes)
    )
    table.add_row(
        "Physical bytes written", _fmt_bytes_human(summary.physical_bytes_written)
    )
    table.add_row("Pack files created", str(summary.pack_files_created))
    table.add_row(
        "Catalog generation published",
        str(summary.catalog_generation_published)
        if summary.catalog_generation_published is not None
        else "none",
    )
    table.add_row(
        "Import receipt path",
        str(summary.import_receipt_path)
        if summary.import_receipt_path is not None
        else "none",
    )
    table.add_row(
        "Source data modified or deleted", summary.source_data_modified_or_deleted
    )
    console.print(table)
    if summary.warning:
        console.print()
        console.print(f"[yellow]Warning:[/yellow] {summary.warning}")


@vault_app.command("inspect")
def vault_inspect(
    vault: Annotated[
        str, typer.Option("--vault", help="Vault directory or locator to inspect.")
    ],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Inspect the latest sealed vault catalog and pack set."""
    try:
        summary = _vault_service().inspect(vault)
    except (MindcapError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)
        return
    if json_output:
        console.print_json(data=summary.to_dict())
        return
    table = Table(title="Mindcap Vault Inspect", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Vault", str(summary.vault_path))
    table.add_row("Vault ID", summary.vault_id)
    table.add_row("Format", f"{summary.format} (v{summary.format_version})")
    table.add_row(
        "Latest valid catalog generation",
        str(summary.latest_generation)
        if summary.latest_generation is not None
        else "none",
    )
    table.add_row(
        "Imported providers",
        ", ".join(summary.providers) if summary.providers else "none",
    )
    table.add_row("Archive units", str(summary.archive_units))
    table.add_row("Provider records", str(summary.provider_records))
    table.add_row("Logical bytes", _fmt_bytes_human(summary.logical_bytes))
    table.add_row("Physical bytes", _fmt_bytes_human(summary.physical_bytes))
    table.add_row(
        "Deduplicated bytes saved", _fmt_bytes_human(summary.deduplicated_bytes_saved)
    )
    table.add_row("Pack count", str(summary.pack_count))
    table.add_row("Incomplete artifacts", str(len(summary.incomplete_artifacts)))
    table.add_row("Orphaned sealed packs", str(len(summary.orphaned_sealed_packs)))
    table.add_row("Last successful import", summary.last_successful_import or "none")
    table.add_row("Verification status", summary.verification_status)
    console.print(table)


@vault_app.command("verify")
def vault_verify(
    vault: Annotated[
        str, typer.Option("--vault", help="Vault directory or locator to verify.")
    ],
    deep: Annotated[
        bool,
        typer.Option("--deep", help="Read every stored object and verify SHA-256."),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Verify sealed catalog generations and immutable pack files."""
    try:
        summary = _vault_service().verify(vault, deep=deep)
    except (MindcapError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)
        return
    if json_output:
        console.print_json(data=summary.to_dict())
        return
    console.print("[green]✓ Vault metadata[/green]")
    console.print("[green]✓ Latest sealed catalog[/green]")
    console.print("[green]✓ Referenced pack files[/green]")
    if deep:
        console.print("[green]✓ Deep object hashes[/green]")
    console.print()
    table = Table(title="Mindcap Vault Verify", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row(
        "Latest generation",
        str(summary.latest_generation)
        if summary.latest_generation is not None
        else "none",
    )
    table.add_row("Archive units", str(summary.archive_units))
    table.add_row("Packs", str(summary.pack_count))
    table.add_row("Objects", str(summary.object_count))
    table.add_row("Incomplete artifacts", str(len(summary.incomplete_artifacts)))
    table.add_row("Orphaned sealed packs", str(len(summary.orphaned_sealed_packs)))
    console.print(table)
    console.print()
    console.print("[bold green]PASS[/bold green]")
    console.print(
        "[yellow]Warning:[/yellow] Successful filesystem verification does "
        "not confirm that a Google Drive client has finished remote "
        "synchronization."
    )


@vault_app.command("restore")
def vault_restore(
    vault: Annotated[
        str, typer.Option("--vault", help="Vault directory or locator to restore from.")
    ],
    provider: Annotated[str, typer.Option("--provider", help="Provider name.")],
    source_id: Annotated[
        str, typer.Option("--source-id", help="Stable provider source ID.")
    ],
    capture_version: Annotated[
        str, typer.Option("--capture-version", help="Capture version to restore.")
    ],
    destination: Annotated[
        Path,
        typer.Option(
            "--destination", help="Destination directory for the restored bundle."
        ),
    ],
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Allow overwriting existing files.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Restore one archive unit from a sealed vault."""
    try:
        summary = _vault_service().restore(
            vault_path=vault,
            provider=provider,
            source_id=source_id,
            capture_version=capture_version,
            destination=destination,
            overwrite=overwrite,
        )
    except (MindcapError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)
        return
    if json_output:
        console.print_json(data=summary.to_dict())
        return
    table = Table(title="Mindcap Vault Restore", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Vault", str(summary.vault_path))
    table.add_row("Destination", str(summary.destination))
    table.add_row("Restored bundle", str(summary.restored_bundle_path))
    table.add_row("Restored files", str(summary.restored_files))
    table.add_row("Restore receipt", str(summary.receipt_path))
    console.print(table)


# ---------------------------------------------------------------------------
# Sync sub-commands
# ---------------------------------------------------------------------------


def _run_sync(
    provider: str,
    collection_url: str | None,
    output: Path | None,
    run_id: str | None,
    resume: bool,
    retry_failed: bool,
    force: bool,
    dry_run: bool,
    max_items: int | None,
    concurrency: int,
    wait_seconds: float,
    quiet: bool,
    verbose: bool,
    debug: bool,
    json_output: bool,
) -> None:
    """Shared sync execution for any supported provider."""
    from mindcap.sync.models import (
        BatchRunConfig,
        RunStatus,
        run_exit_code,
    )
    from mindcap.sync.run_storage import RunStorage, generate_run_id
    from mindcap.sync.runner import SyncRunner, build_sync_result

    artifact_root = (output or default_artifact_root()).resolve()
    _quiet = quiet and not json_output
    reporter = CaptureProgressReporter(
        console=console,
        verbose=verbose,
        debug=debug,
        quiet=_quiet,
    )

    if provider == "suno":
        from mindcap.plugins.suno.collection import SunoCollectionDiscovery

        discovery = SunoCollectionDiscovery()
        archive_subdir = "workspaces/suno"
        collection_identifier = "suno-account"
    elif provider == "distrokid":
        from mindcap.plugins.distrokid.collection import DistroKidCollectionDiscovery

        discovery = DistroKidCollectionDiscovery(  # type: ignore[assignment]
            mymusic_url=collection_url or "https://distrokid.com/mymusic/"
        )
        archive_subdir = "releases/distrokid"
        collection_identifier = "distrokid-library"
    elif provider == "soundcloud":
        from mindcap.plugins.soundcloud.collection import SoundCloudCollectionDiscovery

        discovery = SoundCloudCollectionDiscovery()  # type: ignore[assignment]
        archive_subdir = "archives/soundcloud"
        collection_identifier = "soundcloud-account"
    else:
        _fail(ValueError(f'Unsupported sync provider: "{provider}"'))
        return

    config = BatchRunConfig(
        provider=provider,
        collection_identifier=collection_identifier,
        collection_url=collection_url,
        concurrency=concurrency,
        max_items=max_items,
        force=force,
        dry_run=dry_run,
        wait_seconds=wait_seconds,
    )
    config_fingerprint = config.fingerprint()

    # ---- Resume logic --------------------------------------------------
    prior_state = None
    effective_run_id = run_id

    if resume or run_id:
        if run_id:
            storage = RunStorage(artifact_root, provider, run_id)
            prior_state = storage.load_state()
            if prior_state is None:
                _fail(ValueError(f"No run state found for run ID: {run_id}"))
                return
            effective_run_id = run_id
        else:
            # Auto-detect unfinished runs.
            candidates = RunStorage.find_resumable(
                artifact_root, provider, config_fingerprint
            )
            if len(candidates) == 0:
                if not _quiet:
                    console.print(
                        "[yellow]No compatible unfinished run found; "
                        "starting a new run.[/yellow]"
                    )
            elif len(candidates) == 1:
                storage = candidates[0]
                prior_state = storage.load_state()
                effective_run_id = storage.run_id
                if not _quiet:
                    console.print(f"[bold]Resuming run:[/bold] {effective_run_id}")
            else:
                ids = ", ".join(s.run_id for s in candidates[:5])
                _fail(
                    ValueError(
                        f"Multiple unfinished runs found: {ids}. "
                        f"Pass --run-id to select one."
                    )
                )
                return

    if not _quiet and not dry_run:
        reporter.phase("Authenticating...")
        reporter.phase("Discovering collection...")

    runner = SyncRunner(
        discovery=discovery,
        archive_subdir=archive_subdir,
        reporter=reporter,
    )

    try:
        state = runner.run(
            config=config,
            artifact_root=artifact_root,
            run_id=effective_run_id or generate_run_id(provider),
            prior_state=prior_state,
            retry_failed=retry_failed,
        )
    except (MindcapError, OSError, ValueError) as error:
        _fail(error)
        return

    counts = state.counts()
    run_dir = artifact_root / "runs" / provider / state.run_id

    if json_output:
        result = build_sync_result(state, run_dir)
        console.print_json(data=result.model_dump(mode="json"))
    elif dry_run:
        from mindcap.sync.plan import format_dry_run_table

        console.print(f"\n[bold]{provider.title()} Sync — Dry Run[/bold]\n")
        console.print(format_dry_run_table(len(state.items), state.items))
        console.print()
    elif not _quiet:
        table = Table(title=f"{provider.title()} Account Sync", show_header=False)
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_row("Run ID", state.run_id)
        table.add_row("Status", state.status.value)
        table.add_row("Discovered", str(counts["discovered"]))
        completed = counts["complete"] + counts["complete_with_warnings"]
        table.add_row("Completed", str(completed))
        table.add_row("Unchanged", str(counts["unchanged"]))
        table.add_row("Skipped", str(counts["skipped"]))
        table.add_row("Failed", str(counts["failed"]))
        table.add_row("Run directory", str(run_dir))
        console.print(table)
    else:
        console.print(str(run_dir))

    if state.status == RunStatus.INTERRUPTED:
        console.print(
            f"\n[bold yellow]Capture interrupted safely.[/bold yellow]\n"
            f"\nRun ID:\n  {state.run_id}\n"
            f"\nResume:\n"
            f"  mindcap sync {provider} --resume --run-id {state.run_id}"
        )

    exit_code = run_exit_code(state)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@sync_app.command("suno")
def sync_suno(
    collection_url: Annotated[
        str | None,
        typer.Argument(help="Optional collection URL override."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Private artifact root."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Explicit run ID for resume or retry."),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="Resume the latest unfinished compatible run."),
    ] = False,
    retry_failed: Annotated[
        bool,
        typer.Option("--retry-failed", help="Retry retryable failures from prior run"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-capture all sources ignoring local cache."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Plan captures without executing them."),
    ] = False,
    max_items: Annotated[
        int | None,
        typer.Option("--max-items", help="Cap the number of sources to process."),
    ] = None,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", min=1, max=8),
    ] = 1,
    wait_seconds: Annotated[
        float,
        typer.Option("--wait-seconds", min=1.0, max=120.0),
    ] = 10.0,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress progress output."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit per-item detail."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Emit low-level diagnostics (no secrets)."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON result to stdout."),
    ] = False,
) -> None:
    """Discover and archive every Suno workspace in the authenticated account."""
    _run_sync(
        provider="suno",
        collection_url=collection_url,
        output=output,
        run_id=run_id,
        resume=resume,
        retry_failed=retry_failed,
        force=force,
        dry_run=dry_run,
        max_items=max_items,
        concurrency=concurrency,
        wait_seconds=wait_seconds,
        quiet=quiet,
        verbose=verbose,
        debug=debug,
        json_output=json_output,
    )


@sync_app.command("distrokid")
def sync_distrokid(
    collection_url: Annotated[
        str | None,
        typer.Argument(help="My Music URL (default: https://distrokid.com/mymusic/)."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Private artifact root."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Explicit run ID for resume or retry."),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="Resume the latest unfinished compatible run."),
    ] = False,
    retry_failed: Annotated[
        bool,
        typer.Option("--retry-failed", help="Retry retryable failures from prior run"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-capture all sources ignoring local cache."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Plan captures without executing them."),
    ] = False,
    max_items: Annotated[
        int | None,
        typer.Option("--max-items", help="Cap the number of sources to process."),
    ] = None,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", min=1, max=4),
    ] = 1,
    wait_seconds: Annotated[
        float,
        typer.Option("--wait-seconds", min=1.0, max=120.0),
    ] = 10.0,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress progress output."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit per-item detail."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Emit low-level diagnostics (no secrets)."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON result to stdout."),
    ] = False,
) -> None:
    """Discover and archive every DistroKid release in the authenticated library."""
    _run_sync(
        provider="distrokid",
        collection_url=collection_url,
        output=output,
        run_id=run_id,
        resume=resume,
        retry_failed=retry_failed,
        force=force,
        dry_run=dry_run,
        max_items=max_items,
        concurrency=concurrency,
        wait_seconds=wait_seconds,
        quiet=quiet,
        verbose=verbose,
        debug=debug,
        json_output=json_output,
    )


@sync_app.command("soundcloud")
def sync_soundcloud(
    collection_url: Annotated[
        str | None,
        typer.Argument(help="Optional SoundCloud account URL override."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Private artifact root."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Explicit run ID for resume or retry."),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="Resume the latest unfinished compatible run."),
    ] = False,
    retry_failed: Annotated[
        bool,
        typer.Option("--retry-failed", help="Retry retryable failures from prior run"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-capture all sources ignoring local cache."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Plan captures without executing them."),
    ] = False,
    max_items: Annotated[
        int | None,
        typer.Option("--max-items", help="Cap the number of sources to process."),
    ] = None,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", min=1, max=8),
    ] = 1,
    wait_seconds: Annotated[
        float,
        typer.Option("--wait-seconds", min=1.0, max=120.0),
    ] = 10.0,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Suppress progress output."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit per-item detail."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Emit low-level diagnostics (no secrets)."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON result to stdout."),
    ] = False,
) -> None:
    """Discover and archive every track in the authenticated SoundCloud account."""
    _run_sync(
        provider="soundcloud",
        collection_url=collection_url,
        output=output,
        run_id=run_id,
        resume=resume,
        retry_failed=retry_failed,
        force=force,
        dry_run=dry_run,
        max_items=max_items,
        concurrency=concurrency,
        wait_seconds=wait_seconds,
        quiet=quiet,
        verbose=verbose,
        debug=debug,
        json_output=json_output,
    )


@inspect_app.command("run")
def inspect_run(
    run_path: Annotated[
        Path,
        typer.Argument(help="Run state directory to inspect."),
    ],
) -> None:
    """Inspect a sync batch run directory."""
    import json as _json

    run_path = run_path.expanduser().resolve()
    run_json = run_path / "run.json"
    if not run_json.is_file():
        _fail(FileNotFoundError(f"No run.json found at: {run_path}"))
        return

    try:
        state_raw = _json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as error:
        _fail(error)
        return

    table = Table(title=f"Sync Run — {state_raw.get('run_id', 'unknown')}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field in (
        "run_id",
        "provider",
        "collection_identifier",
        "status",
        "started_at",
        "completed_at",
        "artifact_root",
    ):
        table.add_row(field, str(state_raw.get(field, "-")))

    items = state_raw.get("items", [])
    status_counts: dict[str, int] = {}
    for item in items:
        s = item.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1
    table.add_row("Total items", str(len(items)))
    for status, count in sorted(status_counts.items()):
        table.add_row(f"  {status}", str(count))
    console.print(table)


@inspect_app.command("collection")
def inspect_collection(
    collection_path: Annotated[
        Path,
        typer.Argument(help="Collection archive directory to inspect."),
    ],
) -> None:
    """Inspect a collection-level archive."""
    import json as _json

    collection_path = collection_path.expanduser().resolve()
    latest_json = collection_path / "latest.json"
    if not latest_json.is_file():
        _fail(FileNotFoundError(f"No latest.json found at: {collection_path}"))
        return

    try:
        latest = _json.loads(latest_json.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as error:
        _fail(error)
        return

    table = Table(title="Collection Archive")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field in ("provider", "collection_id", "run_id", "archive_version"):
        table.add_row(field, str(latest.get(field, "-")))
    console.print(table)
