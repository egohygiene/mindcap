"""Tests for the Mindcap synchronization models and state machine."""

from __future__ import annotations

import pytest

from mindcap.sync.models import (
    VALID_TRANSITIONS,
    BatchRunConfig,
    CaptureItemRecord,
    ItemStatus,
    RunStatus,
    SourceDescriptor,
    validate_transition,
)

# ---------------------------------------------------------------------------
# SourceDescriptor helpers
# ---------------------------------------------------------------------------


def _descriptor(
    canonical_identifier: str,
    position: int | None = None,
    remote_revision: str | None = None,
) -> SourceDescriptor:
    return SourceDescriptor(
        provider="suno",
        source_type="workspace",
        canonical_identifier=canonical_identifier,
        canonical_url=f"https://suno.com/create?wid={canonical_identifier}",
        display_title=f"Workspace {canonical_identifier}",
        collection_position=position,
        remote_revision=remote_revision,
    )


# ---------------------------------------------------------------------------
# State machine: valid transitions
# ---------------------------------------------------------------------------


class TestValidTransitions:
    def test_discovered_to_planned(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        assert record.status == ItemStatus.PLANNED

    def test_planned_to_capturing(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        record.transition(ItemStatus.CAPTURING)
        assert record.status == ItemStatus.CAPTURING

    def test_capturing_to_complete(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        record.transition(ItemStatus.CAPTURING)
        record.transition(ItemStatus.COMPLETE)
        assert record.status == ItemStatus.COMPLETE

    def test_capturing_to_failed_retryable(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        record.transition(ItemStatus.CAPTURING)
        record.transition(ItemStatus.FAILED_RETRYABLE)
        assert record.status == ItemStatus.FAILED_RETRYABLE

    def test_failed_retryable_to_capturing(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        record.transition(ItemStatus.CAPTURING)
        record.transition(ItemStatus.FAILED_RETRYABLE)
        record.transition(ItemStatus.CAPTURING)
        assert record.status == ItemStatus.CAPTURING

    def test_interrupted_to_resuming(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        record.transition(ItemStatus.CAPTURING)
        record.transition(ItemStatus.INTERRUPTED)
        record.transition(ItemStatus.RESUMING)
        assert record.status == ItemStatus.RESUMING

    def test_planned_to_skipped(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        record.transition(ItemStatus.SKIPPED_VERIFIED_UNCHANGED)
        assert record.is_terminal

    def test_discovered_to_blocked(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.BLOCKED)
        assert record.is_terminal


# ---------------------------------------------------------------------------
# State machine: invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    def test_complete_to_capturing_is_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid item state transition"):
            validate_transition(ItemStatus.COMPLETE, ItemStatus.CAPTURING)

    def test_failed_terminal_to_complete_is_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid item state transition"):
            validate_transition(ItemStatus.FAILED_TERMINAL, ItemStatus.COMPLETE)

    def test_skipped_to_capturing_is_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid item state transition"):
            validate_transition(
                ItemStatus.SKIPPED_VERIFIED_UNCHANGED, ItemStatus.CAPTURING
            )

    def test_discovered_to_complete_is_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid item state transition"):
            validate_transition(ItemStatus.DISCOVERED, ItemStatus.COMPLETE)

    def test_transition_on_record_raises(self) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"))
        record.transition(ItemStatus.PLANNED)
        record.transition(ItemStatus.CAPTURING)
        record.transition(ItemStatus.COMPLETE)
        with pytest.raises(ValueError):
            record.transition(ItemStatus.FAILED_TERMINAL)


# ---------------------------------------------------------------------------
# Terminal state detection
# ---------------------------------------------------------------------------


class TestTerminalDetection:
    @pytest.mark.parametrize(
        "status",
        [
            ItemStatus.COMPLETE,
            ItemStatus.COMPLETE_WITH_WARNINGS,
            ItemStatus.UNCHANGED,
            ItemStatus.SKIPPED_VERIFIED_UNCHANGED,
            ItemStatus.FAILED_TERMINAL,
            ItemStatus.BLOCKED,
        ],
    )
    def test_terminal_statuses(self, status: ItemStatus) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"), status=status)
        assert record.is_terminal

    @pytest.mark.parametrize(
        "status",
        [
            ItemStatus.DISCOVERED,
            ItemStatus.PLANNED,
            ItemStatus.CAPTURING,
            ItemStatus.RESUMING,
            ItemStatus.PROBING,
            ItemStatus.FAILED_RETRYABLE,
            ItemStatus.INTERRUPTED,
        ],
    )
    def test_non_terminal_statuses(self, status: ItemStatus) -> None:
        record = CaptureItemRecord(descriptor=_descriptor("ws-1"), status=status)
        assert not record.is_terminal


# ---------------------------------------------------------------------------
# Retryable detection
# ---------------------------------------------------------------------------


class TestRetryable:
    def test_retryable_when_count_below_max(self) -> None:
        record = CaptureItemRecord(
            descriptor=_descriptor("ws-1"),
            status=ItemStatus.FAILED_RETRYABLE,
            retry_count=1,
            max_retries=3,
        )
        assert record.is_retryable

    def test_not_retryable_when_at_max(self) -> None:
        record = CaptureItemRecord(
            descriptor=_descriptor("ws-1"),
            status=ItemStatus.FAILED_RETRYABLE,
            retry_count=3,
            max_retries=3,
        )
        assert not record.is_retryable

    def test_not_retryable_when_terminal(self) -> None:
        record = CaptureItemRecord(
            descriptor=_descriptor("ws-1"),
            status=ItemStatus.FAILED_TERMINAL,
            retry_count=0,
            max_retries=3,
        )
        assert not record.is_retryable


# ---------------------------------------------------------------------------
# BatchRunConfig fingerprint
# ---------------------------------------------------------------------------


class TestConfigFingerprint:
    def test_same_config_same_fingerprint(self) -> None:
        config1 = BatchRunConfig(
            provider="suno",
            collection_identifier="suno-account",
        )
        config2 = BatchRunConfig(
            provider="suno",
            collection_identifier="suno-account",
        )
        assert config1.fingerprint() == config2.fingerprint()

    def test_different_provider_different_fingerprint(self) -> None:
        config1 = BatchRunConfig(
            provider="suno",
            collection_identifier="suno-account",
        )
        config2 = BatchRunConfig(
            provider="distrokid",
            collection_identifier="suno-account",
        )
        assert config1.fingerprint() != config2.fingerprint()

    def test_fingerprint_is_16_hex_chars(self) -> None:
        config = BatchRunConfig(
            provider="suno",
            collection_identifier="suno-account",
        )
        fp = config.fingerprint()
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS coverage
# ---------------------------------------------------------------------------


def test_all_statuses_have_transition_entry() -> None:
    for status in ItemStatus:
        assert status in VALID_TRANSITIONS, f"Missing transition entry for {status}"


def test_run_status_values_are_strings() -> None:
    for status in RunStatus:
        assert isinstance(status.value, str)
