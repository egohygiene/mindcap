"""Protocol contracts for the Mindcap synchronization subsystem.

Provider-specific collection discovery must implement
:class:`CollectionDiscoveryStrategy` and be registered with a provider plugin.
All batch orchestration lives in the core sync layer.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from mindcap.core.progress import CaptureProgressReporter
from mindcap.sync.models import CollectionRequest, SourceDescriptor


class CollectionDiscoveryStrategy(Protocol):
    """Discover all source items available in one provider collection.

    Implementations must:

    - Emit items as they are discovered (generator or lazy iterable).
    - Handle pagination, cursors, or infinite-scroll internally.
    - Apply repeated-page and repeated-cursor protection.
    - Never emit duplicate canonical identifiers.
    - Return a :class:`~mindcap.sync.models.DiscoveryResult` via the
      ``discovery_result`` attribute after iteration is complete.
    - Never persist cookies, tokens, or authentication headers.
    """

    def discover(
        self,
        request: CollectionRequest,
        reporter: CaptureProgressReporter | None,
    ) -> Iterable[SourceDescriptor]:
        """Yield :class:`~mindcap.sync.models.SourceDescriptor` objects.

        The iterable must be fully consumable.  Implementations should yield
        items incrementally so that the caller can checkpoint after each page.
        """
        ...
