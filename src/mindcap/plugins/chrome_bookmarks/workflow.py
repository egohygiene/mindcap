"""Compose faithful capture and a separate, inspectable bookmark-review export."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from mindcap.config import ensure_private_directory
from mindcap.contracts.commands import (
    BookmarkBatchesCommand,
    CaptureCommand,
    PrepareBookmarksCommand,
)
from mindcap.contracts.results import BookmarkBatchesResult, CaptureResult
from mindcap.core.errors import CaptureFailedError, MindcapError
from mindcap.plugins.chrome_bookmarks.review import (
    export_bookmark_batches,
    validate_review_output_directory,
)


def _default_workspace() -> Path:
    return Path.home() / "Downloads" / "mindcap-bookmarks"


def _new_review_directory(workspace: Path) -> Path:
    return workspace / f"review-{uuid4().hex[:12]}"


def prepare_bookmarks(
    command: PrepareBookmarksCommand,
    *,
    capture: Callable[[CaptureCommand], CaptureResult],
) -> BookmarkBatchesResult:
    """Read Chrome once and emit manual-review files beside the private archive."""
    workspace = validate_review_output_directory(
        command.output_root or _default_workspace()
    )
    ensure_private_directory(workspace)
    try:
        result = capture(
            CaptureCommand(
                provider="chrome-bookmarks",
                output_root=workspace,
                options={
                    "user_data_dirs": [str(path) for path in command.user_data_dirs],
                    "profiles": command.profiles,
                    "channel": command.channel,
                },
            )
        )
    except (MindcapError, OSError, ValueError) as error:
        # Provider errors can contain local profile paths or malformed source text.
        raise CaptureFailedError(
            "Chrome bookmark capture could not finish; no review batches were "
            "created. Run this command on the computer with your Chrome profiles. "
            "Check --channel and --profile, or use --user-data-dir with the parent "
            "directory containing Default / Profile folders. You can find the "
            "Profile Path at chrome://version. Run mindcap doctor chrome-bookmarks "
            "for local discovery diagnostics."
        ) from error
    return export_bookmark_batches(
        result.archive_path,
        _new_review_directory(workspace),
        batch_size=command.batch_size,
    )


def bookmark_batches(command: BookmarkBatchesCommand) -> BookmarkBatchesResult:
    """Derive another batch set from one saved, verified capture."""
    destination = command.output_directory
    if destination is None:
        destination = _new_review_directory(_default_workspace())
    return export_bookmark_batches(
        command.bundle_path, destination, batch_size=command.batch_size
    )
