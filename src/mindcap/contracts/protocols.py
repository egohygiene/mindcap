"""Presentation-neutral protocols used by the Mindcap application layer.

These protocols define the interaction boundaries between the reusable
library and its execution environment (CLI, API server, background worker,
or test harness).
"""

from __future__ import annotations

from typing import Protocol

from mindcap.contracts.events import MindcapEvent


class EventSink(Protocol):
    """Receives structured progress events from application services."""

    def emit(self, event: MindcapEvent) -> None:
        """Emit a single event to this sink."""
        ...


class UserInteraction(Protocol):
    """Abstracts terminal prompts for interactive provider flows."""

    def notify(self, message: str) -> None:
        """Display an informational message to the user."""
        ...

    def wait_for_confirmation(self, prompt: str) -> None:
        """Pause and wait for the user to confirm before continuing."""
        ...


class CancellationToken(Protocol):
    """Provides cooperative cancellation for long-running operations."""

    def raise_if_cancelled(self) -> None:
        """Raise an exception if cancellation has been requested."""
        ...


class NullEventSink:
    """An :class:`EventSink` that silently discards all events."""

    def emit(self, event: MindcapEvent) -> None:
        pass


class CollectingEventSink:
    """An :class:`EventSink` that stores all emitted events for inspection."""

    def __init__(self) -> None:
        self.events: list[MindcapEvent] = []

    def emit(self, event: MindcapEvent) -> None:
        self.events.append(event)


class CompositeEventSink:
    """An :class:`EventSink` that forwards events to multiple child sinks."""

    def __init__(self, *sinks: EventSink) -> None:
        self._sinks = list(sinks)

    def emit(self, event: MindcapEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)


class NullCancellationToken:
    """A :class:`CancellationToken` that never cancels."""

    def raise_if_cancelled(self) -> None:
        pass
