"""Batch runner for the Mindcap synchronization subsystem.

The runner:

- Iterates the capture plan sequentially (or with bounded concurrency).
- Delegates each item to the existing single-source capture pipeline.
- Checkpoints state after every meaningful transition.
- Handles Ctrl+C (SIGINT) and SIGTERM gracefully.
- Isolates failures so that one failed source does not abort the batch.
- Retries retryable failures up to ``max_retries`` times.
- Writes an interrupted checkpoint on signal.
- Prints the resume command after interruption.

Architecture
------------
The runner *never* contains provider-specific logic.  Provider details live in
the plugin's capture strategy.  The runner calls::

    plugin.strategy(strategy_name, reporter=reporter).capture(request)
    plugin.storage().persist(request, envelope, normalized, transcript)

exactly as the single-source CLI path does.
"""

from __future__ import annotations

import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mindcap.core.errors import AuthenticationRequiredError, MindcapError
from mindcap.core.models import CaptureRequest
from mindcap.core.progress import CaptureProgressReporter
from mindcap.registry import build_registry
from mindcap.sync.locks import RunLock
from mindcap.sync.models import (
    AttemptRecord,
    BatchRunConfig,
    BatchRunState,
    CaptureItemRecord,
    CollectionRequest,
    DiscoveryResult,
    ItemAction,
    ItemStatus,
    RunStatus,
    SyncResult,
)
from mindcap.sync.plan import build_plan
from mindcap.sync.protocols import CollectionDiscoveryStrategy
from mindcap.sync.run_storage import RunStorage, create_initial_state, generate_run_id

# ---------------------------------------------------------------------------
# Retryable HTTP / capture error classification
# ---------------------------------------------------------------------------

_RETRYABLE_ERROR_FRAGMENTS = (
    "timeout",
    "timed out",
    "temporary",
    "temporarily",
    "503",
    "502",
    "504",
    "429",
    "rate limit",
    "connection",
    "network",
    "interrupted",
    "incomplete",
)


def _is_retryable_error(error: Exception) -> bool:
    """Return ``True`` when *error* is likely transient."""
    msg = str(error).lower()
    return any(fragment in msg for fragment in _RETRYABLE_ERROR_FRAGMENTS)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class SyncRunner:
    """Executes a provider-wide synchronization batch run.

    Parameters
    ----------
    discovery:
        A :class:`~mindcap.sync.protocols.CollectionDiscoveryStrategy`
        instance.
    archive_subdir:
        Sub-path within *artifact_root* for this provider's archives,
        e.g. ``"workspaces/suno"``.
    reporter:
        Progress reporter.  Pass ``None`` for silent operation.
    """

    def __init__(
        self,
        discovery: CollectionDiscoveryStrategy,
        archive_subdir: str,
        reporter: CaptureProgressReporter | None = None,
    ) -> None:
        self._discovery = discovery
        self._archive_subdir = archive_subdir
        self._reporter = reporter or CaptureProgressReporter()
        self._interrupted = False

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        config: BatchRunConfig,
        artifact_root: Path,
        *,
        run_id: str | None = None,
        prior_state: BatchRunState | None = None,
        retry_failed: bool = False,
    ) -> BatchRunState:
        """Execute a full synchronization batch run.

        Parameters
        ----------
        config:
            Batch run configuration.
        artifact_root:
            Private artifact root directory.
        run_id:
            Override the auto-generated run ID (useful for resume).
        prior_state:
            Pre-loaded state from a prior interrupted run (resume path).
        retry_failed:
            Promote ``failed-retryable`` items to the plan.

        Returns
        -------
        BatchRunState
            Final run state after completion or interruption.
        """
        effective_run_id = run_id or generate_run_id(config.provider)
        storage = RunStorage(artifact_root, config.provider, effective_run_id)
        storage.ensure_dir()

        with RunLock(storage.run_dir):
            if prior_state is not None:
                state = prior_state
                state.status = RunStatus.RUNNING
            else:
                state = create_initial_state(effective_run_id, config, artifact_root)

            self._install_signal_handlers(state, storage)
            storage.save_state(state)
            storage.append_run_event(
                {"event": "run-started", "run_id": effective_run_id}
            )

            try:
                state = self._run_body(
                    state=state,
                    config=config,
                    artifact_root=artifact_root,
                    storage=storage,
                    prior_state=prior_state,
                    retry_failed=retry_failed,
                )
            except _InterruptedError:
                pass  # Handled in signal handler; state already updated.
            except (MindcapError, OSError, Exception) as error:
                state.status = RunStatus.FAILED
                state.errors.append(str(error))
                storage.save_state(state)
                storage.append_run_event({"event": "run-failed", "error": str(error)})
            finally:
                if state.status not in {
                    RunStatus.COMPLETE,
                    RunStatus.COMPLETE_WITH_FAILURES,
                    RunStatus.FAILED,
                }:
                    if self._interrupted:
                        state.status = RunStatus.INTERRUPTED
                    state.completed_at = datetime.now(UTC)
                    storage.save_state(state)
                    storage.save_report(state)

        return state

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _run_body(
        self,
        *,
        state: BatchRunState,
        config: BatchRunConfig,
        artifact_root: Path,
        storage: RunStorage,
        prior_state: BatchRunState | None,
        retry_failed: bool,
    ) -> BatchRunState:
        # ---- Discovery -------------------------------------------------
        needs_discovery = (
            state.discovery_result is None
            or not state.discovery_result.discovery_complete
        )
        if needs_discovery:
            state.status = RunStatus.DISCOVERING
            storage.save_state(state)
            collection_request = CollectionRequest(
                provider=config.provider,
                collection_identifier=config.collection_identifier,
                collection_url=config.collection_url,
                artifact_root=artifact_root,
                max_items=config.max_items,
                options=config.options,
            )
            descriptors = list(
                self._discovery.discover(collection_request, self._reporter)
            )
            # Retrieve discovery result from the strategy if available.
            discovery_result: DiscoveryResult | None = getattr(
                self._discovery, "discovery_result", None
            )
            if discovery_result is None:
                discovery_result = DiscoveryResult(
                    provider=config.provider,
                    collection_identifier=config.collection_identifier,
                    collection_url=config.collection_url,
                    unique_items_discovered=len(descriptors),
                    discovery_complete=True,
                    discovered_at=datetime.now(UTC),
                )
            state.discovery_result = discovery_result
            storage.save_state(state)
        else:
            # Resume: reconstruct descriptors from prior item records.
            descriptors = [item.descriptor for item in state.items]

        if self._interrupted:
            raise _InterruptedError()

        # ---- Planning --------------------------------------------------
        plugin = build_registry().get(config.provider)
        verify_fn = _make_verify_fn(plugin)

        items = build_plan(
            descriptors=descriptors,
            artifact_root=artifact_root,
            archive_subdir=self._archive_subdir,
            prior_run=prior_state,
            verify_fn=verify_fn,
            force=config.force,
            retry_failed=retry_failed,
            max_items=config.max_items,
        )
        state.items = items
        state.status = RunStatus.RUNNING
        storage.save_plan(items)
        storage.save_state(state)

        if config.dry_run:
            state.status = RunStatus.COMPLETE
            state.completed_at = datetime.now(UTC)
            storage.save_state(state)
            storage.save_report(state)
            return state

        # ---- Execution -------------------------------------------------
        strategy_name = plugin.default_strategy()

        for item in items:
            if self._interrupted:
                item.transition(ItemStatus.INTERRUPTED)
                break

            action = item.action
            if action == ItemAction.SKIP_VERIFIED_UNCHANGED:
                item.transition(ItemStatus.SKIPPED_VERIFIED_UNCHANGED)
                storage.append_item_event(
                    {
                        "event": "item-skipped",
                        "id": item.descriptor.canonical_identifier,
                    }
                )
                continue

            if action == ItemAction.BLOCKED:
                item.transition(ItemStatus.BLOCKED)
                continue

            self._capture_item(
                item=item,
                config=config,
                artifact_root=artifact_root,
                plugin=plugin,
                strategy_name=strategy_name,
                storage=storage,
            )

            storage.save_state(state)

        # ---- Finalize --------------------------------------------------
        counts = state.counts()
        if counts["failed"] > 0:
            state.status = RunStatus.COMPLETE_WITH_FAILURES
        else:
            state.status = RunStatus.COMPLETE
        state.completed_at = datetime.now(UTC)
        storage.save_state(state)
        storage.save_report(state)
        storage.append_run_event(
            {
                "event": "run-complete",
                "status": state.status.value,
                "counts": counts,
            }
        )
        return state

    def _capture_item(
        self,
        *,
        item: CaptureItemRecord,
        config: BatchRunConfig,
        artifact_root: Path,
        plugin: Any,
        strategy_name: str,
        storage: RunStorage,
    ) -> None:
        """Execute one item, retrying on retryable failures."""
        descriptor = item.descriptor
        action = item.action

        next_status = (
            ItemStatus.RESUMING
            if action == ItemAction.RESUME_INCOMPLETE
            else ItemStatus.CAPTURING
        )
        item.transition(next_status)

        while True:
            attempt_num = item.retry_count + 1
            started = datetime.now(UTC)
            storage.append_item_event(
                {
                    "event": "item-started",
                    "id": descriptor.canonical_identifier,
                    "attempt": attempt_num,
                }
            )

            attempt = AttemptRecord(
                attempt=attempt_num,
                started_at=started,
                status=item.status,
            )

            try:
                request = CaptureRequest(
                    source_type=descriptor.source_type,
                    source=descriptor.canonical_url or descriptor.canonical_identifier,
                    provider=descriptor.provider,
                    canonical_identifier=descriptor.canonical_identifier,
                    canonical_url=descriptor.canonical_url,
                    strategy=strategy_name,
                    artifact_root=artifact_root,
                    wait_seconds=config.wait_seconds,
                    options=config.options,
                )
                strategy_obj = plugin.strategy(strategy_name, reporter=self._reporter)
                envelope = strategy_obj.capture(request)
                normalized = plugin.normalize(envelope, descriptor.canonical_identifier)
                transcript = plugin.render(normalized)
                stored = plugin.storage().persist(
                    request, envelope, normalized, transcript
                )
                finished = datetime.now(UTC)
                attempt.finished_at = finished
                attempt.status = ItemStatus.COMPLETE
                attempt.archive_path = str(stored.path)
                item.attempts.append(attempt)
                item.archive_path = str(stored.path)
                item.archive_version = stored.version

                if stored.status == "unchanged":
                    item.transition(ItemStatus.UNCHANGED)
                elif item.warnings or envelope.warnings:
                    item.transition(ItemStatus.COMPLETE_WITH_WARNINGS)
                else:
                    item.transition(ItemStatus.COMPLETE)

                storage.append_item_event(
                    {
                        "event": "item-complete",
                        "id": descriptor.canonical_identifier,
                        "status": item.status.value,
                        "archive_path": str(stored.path),
                    }
                )
                return

            except AuthenticationRequiredError as error:
                attempt.finished_at = datetime.now(UTC)
                attempt.status = ItemStatus.FAILED_TERMINAL
                attempt.error = str(error)
                item.attempts.append(attempt)
                item.errors.append(f"Authentication expired: {error}")
                item.transition(ItemStatus.FAILED_TERMINAL)
                storage.append_item_event(
                    {
                        "event": "item-failed",
                        "id": descriptor.canonical_identifier,
                        "terminal": True,
                        "error": str(error),
                    }
                )
                return

            except (MindcapError, OSError, Exception) as error:
                attempt.finished_at = datetime.now(UTC)
                item.errors.append(str(error))

                if _is_retryable_error(error) and item.retry_count < item.max_retries:
                    attempt.status = ItemStatus.FAILED_RETRYABLE
                    item.attempts.append(attempt)
                    item.retry_count += 1
                    item.transition(ItemStatus.FAILED_RETRYABLE)
                    item.transition(ItemStatus.CAPTURING)
                    backoff = min(2**item.retry_count, 30)
                    if not self._interrupted:
                        time.sleep(backoff)
                    storage.append_item_event(
                        {
                            "event": "item-retry",
                            "id": descriptor.canonical_identifier,
                            "attempt": attempt_num,
                            "error": str(error),
                        }
                    )
                    continue

                attempt.status = ItemStatus.FAILED_TERMINAL
                item.attempts.append(attempt)
                item.transition(ItemStatus.FAILED_TERMINAL)
                storage.append_item_event(
                    {
                        "event": "item-failed",
                        "id": descriptor.canonical_identifier,
                        "terminal": True,
                        "error": str(error),
                    }
                )
                return

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(
        self, state: BatchRunState, storage: RunStorage
    ) -> None:
        """Install SIGINT and SIGTERM handlers for graceful interruption."""

        def _handle(signum: int, frame: object) -> None:
            self._interrupted = True
            state.status = RunStatus.INTERRUPTED
            state.interruption_reason = f"signal-{signum}"
            state.completed_at = datetime.now(UTC)
            try:
                storage.save_state(state)
                storage.save_report(state)
            except Exception:
                pass

        try:
            signal.signal(signal.SIGINT, _handle)
            signal.signal(signal.SIGTERM, _handle)
        except (OSError, ValueError):
            # Signal handling is not available in all environments.
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _InterruptedError(BaseException):
    """Internal sentinel for graceful interruption within _run_body."""


def _make_verify_fn(plugin: Any) -> Any:
    """Return the plugin's offline archive verification function, or ``None``."""
    try:
        storage_strategy = plugin.storage()
        verify = getattr(storage_strategy, "verify", None)
        if callable(verify):
            return verify
    except Exception:
        pass
    return None


def build_sync_result(state: BatchRunState, run_dir: Path | None) -> SyncResult:
    """Build the machine-readable :class:`~mindcap.sync.models.SyncResult`."""
    counts = state.counts()
    resumed = sum(
        1
        for item in state.items
        if any(a.status == ItemStatus.RESUMING for a in item.attempts)
    )
    return SyncResult(
        provider=state.provider,
        run_id=state.run_id,
        status=state.status,
        discovered=counts["discovered"],
        completed=counts["complete"] + counts["complete_with_warnings"],
        unchanged=counts["unchanged"],
        resumed=resumed,
        failed=counts["failed"],
        skipped=counts["skipped"],
        run_path=str(run_dir) if run_dir else None,
    )
