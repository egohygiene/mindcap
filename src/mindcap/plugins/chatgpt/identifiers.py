from __future__ import annotations

import re
from urllib.parse import urlparse

from mindcap.core.errors import InvalidSourceError

CHATGPT_IDENTIFIER = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def canonicalize_chatgpt_identifier(value: str) -> tuple[str, str]:
    candidate = value.strip()
    if CHATGPT_IDENTIFIER.fullmatch(candidate):
        identifier = candidate.lower()
        return identifier, f"https://chatgpt.com/c/{identifier}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "chatgpt.com",
        "www.chatgpt.com",
    }:
        raise InvalidSourceError(f'Not a supported ChatGPT source: "{value}"')

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2 or segments[0] != "c":
        raise InvalidSourceError(
            "Expected a private ChatGPT URL shaped as "
            '"https://chatgpt.com/c/<conversation-id>".'
        )

    identifier = segments[1].lower()
    if not CHATGPT_IDENTIFIER.fullmatch(identifier):
        raise InvalidSourceError(
            f'Invalid ChatGPT conversation identifier: "{segments[1]}"'
        )
    return identifier, f"https://chatgpt.com/c/{identifier}"


def supports_chatgpt_source(value: str) -> bool:
    try:
        canonicalize_chatgpt_identifier(value)
    except InvalidSourceError:
        return False
    return True
