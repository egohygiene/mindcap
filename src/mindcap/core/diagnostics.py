"""Small, source-agnostic helpers for safe human-readable diagnostics.

Diagnostics are descriptions, never a serialization format for request bodies,
browser state, or provider payloads.  This last line of defence removes common
credentials and terminal controls from descriptions before they are displayed
or written to a log.  Callers must still avoid logging private payloads.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

_REDACTED = "[REDACTED]"
_ANSI = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)"
    r"|\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])"
)
_URL = re.compile(r"\b[a-z][a-z\d+.-]*://[^\s<>\"']+", re.IGNORECASE)
_SECRET_KEY = (
    r"(?:authorization|proxy[-_]authorization|auth|(?:set[-_])?cookies?|"
    r"(?:x[-_])?api[-_]?key|(?:access|refresh|id)[-_]?token|"
    r"(?:client[-_])?secret|password|passwd|session(?:[-_](?:id|token))?|"
    r"__session|__client(?:_uat)?|__clerk_db_jwt|"
    r"(?:c|x)srf(?:[-_]token)?|storage[-_]state|token)"
)
_SECRET_ASSIGNMENT = re.compile(
    rf"(?<![\w-])[\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*", re.IGNORECASE
)
_QUOTED_SECRET = re.compile(
    rf"(?P<key>[\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?:\\.|(?!\2).)*?\2",
    re.IGNORECASE,
)
_HEADER_SECRET = re.compile(
    rf"^(?P<key>\s*{_SECRET_KEY}\s*:\s*)(?!\[REDACTED\])\S.*$",
    re.IGNORECASE | re.MULTILINE,
)
_BARE_SECRET = re.compile(
    rf"(?<![\w-])(?P<key>[\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*)"
    r"(?!\[REDACTED\])[^\s,;}\]\"']+",
    re.IGNORECASE,
)
_INLINE_CREDENTIAL = re.compile(
    r"\b(?P<kind>Bearer|Basic)\s+(?!\[REDACTED\])[^\s,;\"'<>]+",
    re.IGNORECASE,
)
_SECRET_OPTION = re.compile(
    rf"(?P<key>--{_SECRET_KEY}\s+)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s]+)",
    re.IGNORECASE,
)
_INLINE_HEADER = re.compile(
    r"(?P<key>\b(?:authorization|proxy[-_]authorization|"
    r"(?:set[-_])?cookies?)\s*[:=]\s*)"
    r"(?![\"']|\[REDACTED\])[^\r\n]+",
    re.IGNORECASE,
)
_JWT = re.compile(
    r"(?<![\w-])eyJ[A-Za-z\d_-]+\.[A-Za-z\d_-]+\.[A-Za-z\d_-]+"
    r"(?![\w-])"
)


def _redact_structured_secrets(text: str) -> str:
    """Omit cookie/storage-state objects without serializing their members."""
    cursor = 0
    pieces: list[str] = []
    for match in _SECRET_ASSIGNMENT.finditer(text):
        if match.end() < cursor or text[match.end() : match.end() + 1] not in "{[":
            continue
        if text[match.end() :].startswith(_REDACTED):
            continue
        start = match.end()
        depth = 0
        quote = ""
        escaped = False
        end = len(text)
        for index in range(start, len(text)):
            character = text[index]
            if escaped:
                escaped = False
            elif quote:
                if character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
            elif character in "\"'":
                quote = character
            elif character in "{[":
                depth += 1
            elif character in "}]":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        pieces.extend((text[cursor:start], _REDACTED))
        cursor = end
    return "".join((*pieces, text[cursor:]))


def _redact_url(match: re.Match[str]) -> str:
    value = match.group()
    # Keep prose punctuation outside the URL.  URL details are intentionally
    # reduced: all query and fragment values may contain private/signed data.
    suffix = ""
    while value and value[-1] in ".,;)}]":
        if value.endswith(_REDACTED):
            break
        suffix = value[-1] + suffix
        value = value[:-1]
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if not host:
            return _REDACTED + suffix
        authority = f"[{host}]" if ":" in host else host
        if parts.port is not None:
            authority += f":{parts.port}"
        return (
            urlunsplit(
                (
                    parts.scheme,
                    authority,
                    parts.path,
                    _REDACTED if parts.query else "",
                    _REDACTED if parts.fragment else "",
                )
            )
            + suffix
        )
    except ValueError:
        return _REDACTED + suffix


def redact_text(value: str) -> str:
    """Return a literal, single-line diagnostic with common secrets removed.

    URL hosts, paths, and HTTP status descriptions remain useful, but URL
    credentials, queries, fragments, cookies, tokens, and passwords do not.
    ANSI escapes, control characters, and bidi controls cannot alter the
    surrounding terminal output.  This function is intentionally idempotent.
    """
    text = _ANSI.sub("", value)
    text = _redact_structured_secrets(text)
    text = _QUOTED_SECRET.sub(lambda m: m["key"] + _REDACTED, text)
    text = _HEADER_SECRET.sub(lambda m: m["key"] + _REDACTED, text)
    text = _INLINE_CREDENTIAL.sub(lambda m: m["kind"] + " " + _REDACTED, text)
    text = _SECRET_OPTION.sub(lambda m: m["key"] + _REDACTED, text)
    text = _INLINE_HEADER.sub(lambda m: m["key"] + _REDACTED, text)
    text = _URL.sub(_redact_url, text)
    text = _BARE_SECRET.sub(lambda m: m["key"] + _REDACTED, text)
    text = _JWT.sub(_REDACTED, text)
    text = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in text
    )
    return " ".join(text.split())
