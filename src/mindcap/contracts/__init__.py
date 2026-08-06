"""Public contracts for the Mindcap application layer.

Import typed command models, result models, event models, and protocols
from this package.
"""

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
    ItemCompleted,
    ItemProgress,
    ItemStarted,
    MindcapEvent,
    OperationCompleted,
    OperationFailed,
    OperationStarted,
    PhaseCompleted,
    PhaseStarted,
    RetryScheduled,
    WarningEmitted,
)
from mindcap.contracts.protocols import (
    CancellationToken,
    CollectingEventSink,
    CompositeEventSink,
    EventSink,
    NullCancellationToken,
    NullEventSink,
    UserInteraction,
)
from mindcap.contracts.results import (
    CaptureResult,
    DoctorResult,
    ImportResult,
    InspectionResult,
    PathEntry,
    PathResult,
    PluginDescriptor,
    PluginListResult,
    SyncResult,
    VerificationResult,
)

__all__ = [  # noqa: RUF022  (grouped by category for readability)
    # Commands
    "CaptureCommand",
    "ImportCommand",
    "SyncCommand",
    "VerifyCommand",
    "InspectCommand",
    "AuthenticationCommand",
    "DoctorCommand",
    "PluginListCommand",
    "PathsCommand",
    # Results
    "CaptureResult",
    "ImportResult",
    "SyncResult",
    "VerificationResult",
    "InspectionResult",
    "DoctorResult",
    "PluginListResult",
    "PluginDescriptor",
    "PathResult",
    "PathEntry",
    # Events
    "MindcapEvent",
    "OperationStarted",
    "PhaseStarted",
    "PhaseCompleted",
    "ItemStarted",
    "ItemProgress",
    "ItemCompleted",
    "WarningEmitted",
    "RetryScheduled",
    "OperationCompleted",
    "OperationFailed",
    # Protocols and sinks
    "EventSink",
    "UserInteraction",
    "CancellationToken",
    "NullEventSink",
    "CollectingEventSink",
    "CompositeEventSink",
    "NullCancellationToken",
]
