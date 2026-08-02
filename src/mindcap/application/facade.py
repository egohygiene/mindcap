"""Reusable Mindcap application facade.

:class:`MindcapApplication` is the primary entry point for Python consumers.
It exposes all capture, import, sync, verify, inspect, authenticate, doctor,
plugin-listing, and path-resolution operations behind a stable API.

Usage (simple)::

    from mindcap.application import MindcapApplication
    from mindcap.contracts import CaptureCommand

    app = MindcapApplication.default()
    result = app.capture(CaptureCommand(provider="chatgpt", source="..."))

Usage (dependency injection)::

    from mindcap import MindcapApplication
    from mindcap.contracts import NullEventSink
    from mindcap.registry import PluginRegistry

    registry = PluginRegistry()
    registry.register(MyPlugin())

    app = MindcapApplication(registry=registry, event_sink=NullEventSink())
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from mindcap.config import default_artifact_root
from mindcap.contracts.commands import (
    AuthenticationCommand,
    CaptureCommand,
    DoctorCommand,
    ImportCommand,
    InspectCommand,
    PathsCommand,
    PluginListCommand,
    SyncCommand,
    VerifyCommand,
)
from mindcap.contracts.events import (
    OperationCompleted,
    OperationFailed,
    OperationStarted,
    PhaseStarted,
)
from mindcap.contracts.protocols import (
    EventSink,
    NullEventSink,
    UserInteraction,
)
from mindcap.contracts.results import (
    CaptureResult,
    DoctorResult,
    ImportConversationResult,
    ImportResult,
    PathEntry,
    PathResult,
    PluginDescriptor,
    PluginListResult,
    SyncResult,
    VerificationResult,
)
from mindcap.core.errors import MindcapError
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.registry import PluginRegistry, create_default_registry
from mindcap.storage import verify_bundle


class MindcapApplication:
    """The public Mindcap application facade.

    Consumers should obtain an instance through :meth:`default` for standard
    usage or through the constructor for dependency injection.
    """

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        event_sink: EventSink | None = None,
        interaction: UserInteraction | None = None,
    ) -> None:
        self._registry = registry if registry is not None else create_default_registry()
        self._event_sink: EventSink = event_sink or NullEventSink()
        self._interaction = interaction

    @classmethod
    def default(cls) -> MindcapApplication:
        """Construct a ready-to-use application with built-in defaults."""
        return cls(
            registry=create_default_registry(),
            event_sink=NullEventSink(),
        )

    # ------------------------------------------------------------------
    # Plugin discovery
    # ------------------------------------------------------------------

    def list_plugins(
        self, _command: PluginListCommand | None = None
    ) -> PluginListResult:
        """Return metadata for every registered source plugin."""
        descriptors = []
        for name in self._registry.names():
            plugin = self._registry.get(name)
            descriptors.append(
                PluginDescriptor(
                    source_type=plugin.source_type,
                    strategies=plugin.strategies(),
                )
            )
        return PluginListResult(plugins=descriptors)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, command: CaptureCommand) -> CaptureResult:
        """Capture a single source through a registered plugin and strategy."""
        operation_id = f"capture-{uuid.uuid4().hex[:8]}"
        self._event_sink.emit(
            OperationStarted(
                operation_id=operation_id,
                operation="capture",
                provider=command.provider,
            )
        )
        start = time.monotonic()
        try:
            result = self._run_capture(command, operation_id)
        except Exception as exc:
            self._event_sink.emit(
                OperationFailed(
                    operation_id=operation_id,
                    operation="capture",
                    provider=command.provider,
                    error=str(exc),
                )
            )
            raise
        elapsed = time.monotonic() - start
        self._event_sink.emit(
            OperationCompleted(
                operation_id=operation_id,
                operation="capture",
                provider=command.provider,
                elapsed_seconds=elapsed,
            )
        )
        return result

    def _run_capture(self, command: CaptureCommand, operation_id: str) -> CaptureResult:
        plugin = self._registry.get(command.provider)
        selected_strategy = command.strategy or plugin.default_strategy()
        identifier_source = command.identifier_override or command.source or ""
        identifier, canonical_url = plugin.canonicalize(identifier_source)
        artifact_root = (command.output_root or default_artifact_root()).resolve()

        request = CaptureRequest(
            source_type=command.provider,
            source=command.source or "",
            provider=command.provider,
            canonical_identifier=identifier,
            canonical_url=canonical_url,
            strategy=selected_strategy,
            artifact_root=artifact_root,
            wait_seconds=command.wait_seconds,
            options=command.options,
        )

        self._event_sink.emit(
            PhaseStarted(
                operation_id=operation_id, phase="capture", provider=command.provider
            )
        )
        strategy_obj = plugin.strategy(selected_strategy)
        start = time.monotonic()
        envelope = strategy_obj.capture(request)

        self._event_sink.emit(
            PhaseStarted(
                operation_id=operation_id,
                phase="normalize",
                provider=command.provider,
            )
        )
        normalized = plugin.normalize(envelope, identifier)
        transcript = plugin.render(normalized)

        self._event_sink.emit(
            PhaseStarted(
                operation_id=operation_id, phase="persist", provider=command.provider
            )
        )
        stored = plugin.storage().persist(request, envelope, normalized, transcript)
        elapsed = time.monotonic() - start

        return CaptureResult(
            status=stored.status,
            provider=command.provider,
            source_id=stored.source_id,
            canonical_identifier=identifier,
            archive_version=stored.version,
            archive_path=stored.path,
            canonical_content_hash=stored.canonical_content_hash,
            safe_metadata=dict(envelope.safe_metadata),
            warnings=list(envelope.warnings),
            elapsed_seconds=elapsed,
            verification_passed=stored.status in ("complete", "unchanged"),
        )

    # ------------------------------------------------------------------
    # Import (batch)
    # ------------------------------------------------------------------

    def import_source(self, command: ImportCommand) -> ImportResult:
        """Batch-import a previously exported source (e.g. ChatGPT ZIP)."""
        from mindcap.plugins.chatgpt.plugin import ChatGPTPlugin
        from mindcap.plugins.chatgpt.strategies.export import ExportCaptureStrategy

        artifact_root = (command.output_root or default_artifact_root()).resolve()
        start_time = time.monotonic()
        plugin = ChatGPTPlugin()
        export_strategy = ExportCaptureStrategy()

        discovery = export_strategy.discover(command.source)
        import_id = f"import-{uuid.uuid4().hex[:16]}"
        import_root = artifact_root / "imports" / "chatgpt" / import_id
        import_root.mkdir(parents=True, exist_ok=True)

        imported: list[ImportConversationResult] = []
        failed: list[ImportConversationResult] = []
        unchanged: list[ImportConversationResult] = []
        all_warnings: list[str] = list(discovery.warnings)
        total_discovered = 0

        for record in export_strategy.iter_conversations(
            command.source,
            conversation_id=command.conversation_id_filter,
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
                req = CaptureRequest(
                    source_type="chatgpt",
                    source=command.source or "",
                    provider="chatgpt",
                    canonical_identifier=conv_id,
                    canonical_url=f"https://chatgpt.com/c/{conv_id}",
                    strategy="export",
                    artifact_root=artifact_root,
                )
                normalized = plugin.normalize(envelope, conv_id)
                transcript = plugin.render(normalized)
                stored = plugin.storage().persist(req, envelope, normalized, transcript)
                entry = ImportConversationResult(
                    conversation_id=conv_id,
                    status="unchanged" if stored.status == "unchanged" else "imported",
                    version=stored.version,
                    bundle_path=str(stored.path),
                    source_file=record.source_file,
                    raw_sha256=record.sha256,
                )
                if stored.status == "unchanged":
                    unchanged.append(entry)
                else:
                    imported.append(entry)
            except (MindcapError, OSError, ValueError, json.JSONDecodeError) as exc:
                failed.append(
                    ImportConversationResult(
                        conversation_id=conv_id,
                        status="failed",
                        source_file=record.source_file,
                        error=str(exc),
                    )
                )
                all_warnings.append(f"Failed to import conversation {conv_id}: {exc}")

        elapsed = time.monotonic() - start_time

        # Write import manifest.
        manifest: dict[str, Any] = {
            "schema": "mindcap.import-manifest/v0.1",
            "import_id": import_id,
            "source": command.source,
            "source_sha256": discovery.source_sha256,
            "import_timestamp": datetime.now(UTC).isoformat(),
            "conversations_discovered": total_discovered,
            "conversations_imported": len(imported),
            "conversations_unchanged": len(unchanged),
            "conversations_failed": len(failed),
            "warnings": all_warnings,
            "elapsed_seconds": round(elapsed, 2),
        }
        (import_root / "import-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        conversations_index: dict[str, Any] = {
            "import_id": import_id,
            "imported": [r.model_dump() for r in imported],
            "unchanged": [r.model_dump() for r in unchanged],
            "failed": [r.model_dump() for r in failed],
        }
        (import_root / "conversations-index.json").write_text(
            json.dumps(conversations_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if all_warnings:
            (import_root / "warnings.json").write_text(
                json.dumps(all_warnings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return ImportResult(
            import_id=import_id,
            source=command.source or "",
            source_sha256=discovery.source_sha256,
            import_timestamp=datetime.now(UTC).isoformat(),
            conversations_discovered=total_discovered,
            conversations_imported=len(imported),
            conversations_unchanged=len(unchanged),
            conversations_failed=len(failed),
            warnings=all_warnings,
            elapsed_seconds=round(elapsed, 2),
            import_path=import_root,
        )

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync(self, command: SyncCommand) -> SyncResult:
        """Synchronize an entire provider account collection."""
        from mindcap.core.errors import InvalidSourceError
        from mindcap.sync.models import BatchRunConfig
        from mindcap.sync.run_storage import RunStorage, generate_run_id
        from mindcap.sync.runner import SyncRunner, build_sync_result

        artifact_root = (command.output_root or default_artifact_root()).resolve()

        if command.provider == "suno":
            from mindcap.plugins.suno.collection import SunoCollectionDiscovery

            discovery = SunoCollectionDiscovery()
            archive_subdir = "workspaces/suno"
            collection_identifier = "suno-account"
        elif command.provider == "distrokid":
            from mindcap.plugins.distrokid.collection import (
                DistroKidCollectionDiscovery,
            )

            discovery = DistroKidCollectionDiscovery(  # type: ignore[assignment]
                mymusic_url=command.collection_url or "https://distrokid.com/mymusic/"
            )
            archive_subdir = "releases/distrokid"
            collection_identifier = "distrokid-library"
        elif command.provider == "soundcloud":
            from mindcap.plugins.soundcloud.collection import (
                SoundCloudCollectionDiscovery,
            )

            discovery = SoundCloudCollectionDiscovery()  # type: ignore[assignment]
            archive_subdir = "archives/soundcloud"
            collection_identifier = "soundcloud-account"
        else:
            raise InvalidSourceError(f'Unsupported sync provider: "{command.provider}"')

        config = BatchRunConfig(
            provider=command.provider,
            collection_identifier=collection_identifier,
            collection_url=command.collection_url,
            concurrency=command.concurrency,
            max_items=command.max_items,
            force=command.force,
            dry_run=command.dry_run,
            wait_seconds=command.wait_seconds,
        )
        config_fingerprint = config.fingerprint()

        prior_state = None
        effective_run_id = command.run_id

        if command.resume or command.run_id:
            if command.run_id:
                storage = RunStorage(artifact_root, command.provider, command.run_id)
                prior_state = storage.load_state()
                if prior_state is None:
                    raise ValueError(f"No run state found for run ID: {command.run_id}")
                effective_run_id = command.run_id
            else:
                candidates = RunStorage.find_resumable(
                    artifact_root, command.provider, config_fingerprint
                )
                if len(candidates) == 1:
                    storage = candidates[0]
                    prior_state = storage.load_state()
                    effective_run_id = storage.run_id
                elif len(candidates) > 1:
                    ids = ", ".join(s.run_id for s in candidates[:5])
                    raise ValueError(
                        f"Multiple unfinished runs found: {ids}. "
                        "Pass run_id to select one."
                    )

        runner = SyncRunner(
            discovery=discovery,
            archive_subdir=archive_subdir,
        )

        state = runner.run(
            config=config,
            artifact_root=artifact_root,
            run_id=effective_run_id or generate_run_id(command.provider),
            prior_state=prior_state,
            retry_failed=command.retry_failed,
        )

        run_dir = artifact_root / "runs" / command.provider / state.run_id
        build_sync_result(state, run_dir)
        counts = state.counts()

        return SyncResult(
            run_id=state.run_id,
            provider=command.provider,
            collection_identifier=collection_identifier,
            status=state.status.value,
            discovered=counts.get("discovered", 0),
            completed=counts.get("complete", 0)
            + counts.get("complete_with_warnings", 0),
            unchanged=counts.get("unchanged", 0),
            skipped=counts.get("skipped", 0),
            failed=counts.get("failed", 0),
            run_path=run_dir,
        )

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify(self, command: VerifyCommand) -> VerificationResult:
        """Verify the integrity of a captured bundle."""
        bundle_path = command.bundle_path.expanduser().resolve()
        try:
            verify_bundle(bundle_path)
            return VerificationResult(
                status="pass",
                bundle_path=bundle_path,
                checks=[
                    {"name": "manifest", "status": "pass"},
                    {"name": "checksums", "status": "pass"},
                ],
            )
        except (MindcapError, OSError, KeyError, TypeError) as exc:
            return VerificationResult(
                status="fail",
                bundle_path=bundle_path,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Inspect
    # ------------------------------------------------------------------

    def inspect(self, command: InspectCommand) -> dict[str, Any]:
        """Inspect a captured archive and return raw field data."""
        archive = command.archive_path.expanduser().resolve()
        plugin = self._registry.get(command.provider)
        inspector = getattr(plugin, "inspector", None)
        if inspector is not None:
            return inspector(archive)  # type: ignore[no-any-return]
        return {"archive_path": str(archive), "provider": command.provider}

    # ------------------------------------------------------------------
    # Authenticate
    # ------------------------------------------------------------------

    def authenticate(self, command: AuthenticationCommand) -> dict[str, Any]:
        """Trigger authentication for a provider.

        Returns a mapping of provider-specific status information.
        The return value is intentionally untyped at this phase to avoid
        hard-coding provider-specific result schemas into the public API.
        """
        provider = command.provider
        if provider == "chatgpt":
            from mindcap.plugins.chatgpt.strategies.browser import authenticate_chatgpt

            authenticate_chatgpt()
            return {"provider": provider, "status": "authenticated"}
        elif provider == "suno":
            import sys

            from mindcap.plugins.suno.auth import authenticate_suno_cookie_stdin

            cookie_data = command.options.get("cookie_data") or sys.stdin.read()
            authenticate_suno_cookie_stdin(cookie_data)
            return {"provider": provider, "status": "authenticated"}
        elif provider == "distrokid":
            from mindcap.plugins.distrokid.strategies.browser import (
                authenticate_distrokid,
            )

            authenticate_distrokid()
            return {"provider": provider, "status": "authenticated"}
        else:
            raise MindcapError(f'No authentication implementation for "{provider}"')

    # ------------------------------------------------------------------
    # Doctor
    # ------------------------------------------------------------------

    def doctor(self, command: DoctorCommand) -> DoctorResult:
        """Run diagnostic checks for a provider."""
        import io

        from rich.console import Console

        provider = command.provider
        # Use a no-op console to keep the library's doctor operations
        # terminal-neutral.  The CLI layer constructs its own Console.
        _console = Console(file=io.StringIO(), highlight=False)

        if provider == "chatgpt":
            from mindcap.config import chatgpt_profile_dir
            from mindcap.plugins.chatgpt.strategies.browser import (
                _find_stable_chrome,
                verify_chatgpt_authentication,
            )

            checks = []
            chrome = None
            try:
                chrome = _find_stable_chrome()
                checks.append(
                    {
                        "name": "chrome_discovery",
                        "status": "found",
                        "detail": str(chrome),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "chrome_discovery",
                        "status": "not_found",
                        "detail": str(exc),
                    }
                )

            profile = chatgpt_profile_dir()
            checks.append(
                {
                    "name": "profile_exists",
                    "status": "yes" if profile.is_dir() else "no",
                    "detail": str(profile),
                }
            )
            auth = verify_chatgpt_authentication() if chrome else None
            checks.append(
                {
                    "name": "authentication",
                    "status": auth.state.value if auth else "indeterminate",
                    "detail": auth.detail if auth else "Chrome unavailable",
                }
            )
            from mindcap.contracts.results import DoctorCheckResult

            return DoctorResult(
                provider=provider,
                checks=[
                    DoctorCheckResult(
                        name=c["name"], status=c["status"], detail=c.get("detail", "")
                    )
                    for c in checks
                ],
            )
        elif provider in ("suno", "distrokid", "soundcloud", "chrome-bookmarks"):
            # These doctor implementations require a Rich console currently.
            if provider == "suno":
                from mindcap.plugins.suno.doctor import doctor_suno as _doctor

                _doctor(_console, verbose=command.verbose)
            elif provider == "distrokid":
                from mindcap.plugins.distrokid.doctor import (
                    doctor_distrokid as _doctor,
                )

                _doctor(_console, verbose=command.verbose)
            elif provider == "soundcloud":
                from mindcap.plugins.soundcloud.doctor import (
                    doctor_soundcloud as _doctor,
                )

                _doctor(_console, verbose=command.verbose)
            elif provider == "chrome-bookmarks":
                from mindcap.contracts.results import DoctorCheckResult
                from mindcap.plugins.chrome_bookmarks.diagnostics import (
                    collect_chrome_bookmarks_diagnostics,
                )

                checks = collect_chrome_bookmarks_diagnostics()
                return DoctorResult(
                    provider=provider,
                    checks=[
                        DoctorCheckResult(
                            name=str(c["name"]),
                            status=str(c["status"]),
                            detail=str(c.get("detail") or ""),
                        )
                        for c in checks
                    ],
                )
            return DoctorResult(provider=provider, checks=[])
        else:
            raise MindcapError(f'No doctor implementation for "{provider}"')

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def paths(self, _command: PathsCommand | None = None) -> PathResult:
        """Resolve key filesystem paths used by Mindcap."""
        from mindcap.config import (
            chatgpt_profile_dir,
            distrokid_profile_dir,
            soundcloud_profile_dir,
        )

        return PathResult(
            entries=[
                PathEntry(
                    purpose="Artifacts",
                    path=default_artifact_root(),
                    archive_this="Yes, after review",
                ),
                PathEntry(
                    purpose="ChatGPT browser profile",
                    path=chatgpt_profile_dir(),
                    archive_this="No — contains authentication state",
                ),
                PathEntry(
                    purpose="DistroKid browser profile",
                    path=distrokid_profile_dir(),
                    archive_this="No — contains authentication state",
                ),
                PathEntry(
                    purpose="SoundCloud browser profile",
                    path=soundcloud_profile_dir(),
                    archive_this="No — contains authentication state",
                ),
            ]
        )
