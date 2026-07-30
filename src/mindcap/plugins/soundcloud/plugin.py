"""SoundCloud source plugin for Mindcap."""

from __future__ import annotations

from typing import Any

from mindcap.core.errors import InvalidSourceError
from mindcap.core.models import CaptureEnvelope
from mindcap.core.progress import CaptureProgressReporter
from mindcap.core.protocols import CaptureStrategy
from mindcap.plugins.soundcloud.archive.storage import SoundCloudArchiveStorageStrategy
from mindcap.plugins.soundcloud.identifiers import (
    canonicalize_soundcloud_identifier,
    supports_soundcloud_source,
)
from mindcap.plugins.soundcloud.normalizer import normalize_soundcloud
from mindcap.plugins.soundcloud.renderer import render_soundcloud_markdown
from mindcap.plugins.soundcloud.strategies.api import SoundCloudApiCaptureStrategy


class SoundCloudPlugin:
    """Mindcap plugin for the SoundCloud provider.

    Supported source types:

    - ``account`` — the authenticated user's account or a public profile.
    - ``track`` — a single SoundCloud track.
    - ``playlist`` — a SoundCloud playlist, album, or set.
    """

    source_type = "soundcloud"

    def supports(self, value: str) -> bool:
        return supports_soundcloud_source(value)

    def canonicalize(self, value: str) -> tuple[str, str | None]:
        return canonicalize_soundcloud_identifier(value)

    def default_strategy(self) -> str:
        return "api"

    def strategies(self) -> tuple[str, ...]:
        return ("api",)

    def strategy(
        self,
        name: str,
        reporter: CaptureProgressReporter | None = None,
    ) -> CaptureStrategy:
        available_strategies: dict[str, CaptureStrategy] = {
            "api": SoundCloudApiCaptureStrategy(reporter=reporter),
        }
        try:
            return available_strategies[name]
        except KeyError as error:
            available = ", ".join(sorted(available_strategies))
            raise InvalidSourceError(
                f'Unknown SoundCloud strategy "{name}". Available: {available}'
            ) from error

    def normalize(
        self, envelope: CaptureEnvelope, requested_identifier: str
    ) -> dict[str, Any]:
        return normalize_soundcloud(envelope, requested_identifier)

    def render(self, normalized: dict[str, Any]) -> str:
        return render_soundcloud_markdown(normalized)

    def storage(self) -> SoundCloudArchiveStorageStrategy:
        return SoundCloudArchiveStorageStrategy()
