from __future__ import annotations

from typing import Any

from mindcap.core.errors import InvalidSourceError
from mindcap.core.models import CaptureEnvelope
from mindcap.core.protocols import CaptureStrategy
from mindcap.plugins.chatgpt.identifiers import (
    canonicalize_chatgpt_identifier,
    supports_chatgpt_source,
)
from mindcap.plugins.chatgpt.normalizer import normalize_chatgpt
from mindcap.plugins.chatgpt.renderer import render_chatgpt_markdown
from mindcap.plugins.chatgpt.strategies.browser import BrowserCaptureStrategy
from mindcap.plugins.chatgpt.strategies.saved_json import SavedJsonCaptureStrategy


class ChatGPTPlugin:
    source_type = "chatgpt"

    def supports(self, value: str) -> bool:
        return supports_chatgpt_source(value)

    def canonicalize(self, value: str) -> tuple[str, str | None]:
        return canonicalize_chatgpt_identifier(value)

    def strategy(self, name: str) -> CaptureStrategy:
        strategies: dict[str, CaptureStrategy] = {
            "browser": BrowserCaptureStrategy(),
            "saved-json": SavedJsonCaptureStrategy(),
        }
        try:
            return strategies[name]
        except KeyError as error:
            available = ", ".join(sorted(strategies))
            raise InvalidSourceError(
                f'Unknown ChatGPT strategy "{name}". Available: {available}'
            ) from error

    def normalize(
        self, envelope: CaptureEnvelope, requested_identifier: str
    ) -> dict[str, Any]:
        return normalize_chatgpt(envelope, requested_identifier)

    def render(self, normalized: dict[str, Any]) -> str:
        return render_chatgpt_markdown(normalized)
