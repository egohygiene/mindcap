"""Deterministic capture planning for the Mindcap synchronization subsystem.

Planning occurs after collection discovery and before any capture work.
The plan classifies each discovered source item and produces a stable,
inspectable, serializable order.

Classification decisions
------------------------
capture-new
    No local archive exists.
capture-changed
    A local archive exists but remote revision differs.
resume-incomplete
    A prior run began but did not produce a finalized archive.
retry-failed
    A prior run failed with a retryable error.
skip-verified-unchanged
    A verified, finalized archive exists with matching remote revision.
probe-required
    Local archive exists but revision evidence is insufficient to skip or
    capture without a lightweight metadata probe.
blocked
    Cannot proceed; e.g. a dependency failed or authentication expired.

Ordering
--------
Items are ordered first by ``collection_position`` (ascending, ``None`` last),
then by ``canonical_identifier`` as a deterministic tie-breaker.  This order
is stable across multiple calls with the same inputs.
"""

from __future__ import annotations

from pathlib import Path

from mindcap.sync.cache import VerifyFn, evaluate_cache
from mindcap.sync.models import (
    BatchRunState,
    CaptureItemRecord,
    ItemAction,
    ItemStatus,
    SourceDescriptor,
)


def build_plan(
    descriptors: list[SourceDescriptor],
    artifact_root: Path,
    archive_subdir: str,
    *,
    prior_run: BatchRunState | None = None,
    verify_fn: VerifyFn | None = None,
    force: bool = False,
    retry_failed: bool = False,
    max_items: int | None = None,
) -> list[CaptureItemRecord]:
    """Build a deterministic, classified capture plan.

    Parameters
    ----------
    descriptors:
        Source descriptors from the collection discovery phase.
    artifact_root:
        Private artifact root directory.
    archive_subdir:
        Sub-path within *artifact_root* for this provider's archives.
    prior_run:
        A prior run's state for resume/retry classification.
    verify_fn:
        Optional offline archive verifier used during cache evaluation.
    force:
        Ignore existing archives and re-capture every source.
    retry_failed:
        Promote ``failed-retryable`` items to the plan.
    max_items:
        If set, cap the number of items included in the plan.

    Returns
    -------
    list[CaptureItemRecord]
        Ordered, classified item records.
    """
    seen_ids: set[str] = set()
    records: list[CaptureItemRecord] = []

    for descriptor in descriptors:
        cid = descriptor.canonical_identifier
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        record = CaptureItemRecord(descriptor=descriptor)
        record.transition(ItemStatus.PLANNED)
        record.action = _classify(
            descriptor=descriptor,
            artifact_root=artifact_root,
            archive_subdir=archive_subdir,
            prior_run=prior_run,
            verify_fn=verify_fn,
            force=force,
            retry_failed=retry_failed,
        )
        # Attach cache decision for audit trail.
        if not force and record.action in {
            ItemAction.SKIP_VERIFIED_UNCHANGED,
            ItemAction.PROBE_REQUIRED,
            ItemAction.CAPTURE_CHANGED,
            ItemAction.CAPTURE_NEW,
        }:
            record.cache_decision = evaluate_cache(
                descriptor,
                artifact_root,
                archive_subdir,
                verify_fn=verify_fn,
            )
        records.append(record)

    # Stable ordering: collection_position asc (None sorts last), then ID.
    records.sort(
        key=lambda r: (
            r.descriptor.collection_position is None,
            r.descriptor.collection_position or 0,
            r.descriptor.canonical_identifier,
        )
    )

    if max_items is not None:
        records = records[:max_items]

    return records


def _classify(
    *,
    descriptor: SourceDescriptor,
    artifact_root: Path,
    archive_subdir: str,
    prior_run: BatchRunState | None,
    verify_fn: VerifyFn | None,
    force: bool,
    retry_failed: bool,
) -> ItemAction:
    """Return the planned ItemAction for one descriptor."""
    cid = descriptor.canonical_identifier

    # Honour force flag before any other check.
    if force:
        return ItemAction.CAPTURE_NEW

    # Check prior run state for resume/retry opportunity.
    if prior_run is not None:
        prior_item = prior_run.item_by_id(cid)
        if prior_item is not None:
            if prior_item.status == ItemStatus.INTERRUPTED:
                return ItemAction.RESUME_INCOMPLETE
            if prior_item.status == ItemStatus.FAILED_RETRYABLE and retry_failed:
                return ItemAction.RETRY_FAILED
            if prior_item.status in {
                ItemStatus.COMPLETE,
                ItemStatus.COMPLETE_WITH_WARNINGS,
            }:
                # Previously complete — still evaluate cache to detect changes.
                pass

    decision = evaluate_cache(
        descriptor,
        artifact_root,
        archive_subdir,
        verify_fn=verify_fn,
    )

    if decision.decision == "skip":
        return ItemAction.SKIP_VERIFIED_UNCHANGED
    if decision.decision == "probe":
        return ItemAction.PROBE_REQUIRED
    # "capture" — differentiate new vs changed.
    if decision.local_archive_version is not None:
        return ItemAction.CAPTURE_CHANGED
    return ItemAction.CAPTURE_NEW


def plan_summary(records: list[CaptureItemRecord]) -> dict[str, int]:
    """Return a human-readable summary dict for dry-run output."""
    counts: dict[str, int] = {action.value: 0 for action in ItemAction}
    for record in records:
        if record.action is not None:
            counts[record.action.value] += 1
    return counts


def format_dry_run_table(
    descriptors_count: int, records: list[CaptureItemRecord]
) -> str:
    """Return a formatted dry-run planning summary string."""
    summary = plan_summary(records)
    lines = [
        f"{'Discovered:':<28} {descriptors_count}",
        f"{'New:':<28} {summary.get('capture-new', 0)}",
        f"{'Changed:':<28} {summary.get('capture-changed', 0)}",
        f"{'Resume incomplete:':<28} {summary.get('resume-incomplete', 0)}",
        f"{'Retry failed:':<28} {summary.get('retry-failed', 0)}",
        f"{'Verified unchanged:':<28} {summary.get('skip-verified-unchanged', 0)}",
        f"{'Probe required:':<28} {summary.get('probe-required', 0)}",
        f"{'Blocked:':<28} {summary.get('blocked', 0)}",
    ]
    return "\n".join(lines)
