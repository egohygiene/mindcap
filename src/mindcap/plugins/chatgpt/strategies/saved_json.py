from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from mindcap.core.errors import CaptureFailedError, UnsupportedConversationSchemaError
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.plugins.chatgpt.identifiers import CHATGPT_IDENTIFIER

# Minimum keys that signal a ChatGPT conversation object.
_CONVERSATION_KEYS = frozenset({"id", "mapping"})


def _detect_input_shape(payload: object, source_filename: str) -> str:
    """Return a human-readable description of the JSON input shape."""
    if isinstance(payload, dict):
        if isinstance(payload.get("mapping"), dict):
            conv_id = str(payload.get("id") or payload.get("conversation_id") or "")
            if conv_id and CHATGPT_IDENTIFIER.fullmatch(conv_id.strip()):
                return "single-conversation"
            return "single-conversation (no UUID id field)"
        nested = payload.get("conversation")
        if isinstance(nested, dict) and isinstance(nested.get("mapping"), dict):
            return "wrapped-conversation"
        if any(k in payload for k in _CONVERSATION_KEYS):
            return "partial-conversation-object"
        return f"dict (keys: {sorted(payload.keys())[:6]!r})"
    if isinstance(payload, list):
        if not payload:
            return "empty-array"
        first = payload[0]
        if isinstance(first, dict) and isinstance(first.get("mapping"), dict):
            return f"conversation-array (length={len(payload)})"
        return f"array (length={len(payload)}, first-type={type(first).__name__})"
    return type(payload).__name__


class SavedJsonCaptureStrategy:
    name = "saved-json"

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        path = Path(request.source).expanduser().resolve()
        if not path.is_file():
            raise CaptureFailedError(
                f'JSON source does not exist: "{path}". '
                f"Provide an absolute path to a conversation JSON file."
            )
        body = path.read_bytes()

        # Validate the JSON shape so that malformed input is rejected early
        # with a clear, actionable error rather than a cryptic normalisation
        # failure later in the pipeline.
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise UnsupportedConversationSchemaError(
                f'Cannot parse "{path.name}" as JSON: {exc}. '
                f"Source filename: {path.name!r}. "
                f"Verify that the file is valid UTF-8 JSON exported from ChatGPT."
            ) from exc

        shape = _detect_input_shape(payload, path.name)

        # Reject shapes that are definitely not usable.
        if isinstance(payload, dict):
            if not (
                isinstance(payload.get("mapping"), dict)
                or isinstance((payload.get("conversation") or {}).get("mapping"), dict)
            ):
                raise UnsupportedConversationSchemaError(
                    f'"{path.name}" does not appear to contain a ChatGPT conversation. '
                    f"Detected shape: {shape}. "
                    f'Expected shape: a JSON object with a "mapping" field, '
                    f"an array of conversation objects, or an export fragment. "
                    f"Source filename: {path.name!r}. "
                    f"If this is a full export, use --strategy export instead."
                )
        elif isinstance(payload, list):
            if payload and not isinstance(payload[0], dict):
                raise UnsupportedConversationSchemaError(
                    f'"{path.name}" contains a JSON array whose elements are not '
                    f"objects. "
                    f"Detected shape: {shape}. "
                    f"Source filename: {path.name!r}."
                )
        else:
            raise UnsupportedConversationSchemaError(
                f'"{path.name}" is not a JSON object or array. '
                f"Detected shape: {shape}. "
                f"Source filename: {path.name!r}."
            )

        return CaptureEnvelope(
            provider="chatgpt",
            source_type="conversation",
            canonical_identifier=request.canonical_identifier,
            canonical_url=request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy=self.name,
            response_units=[
                RawResponseUnit(
                    unit_id="response-000",
                    sequence=0,
                    media_type="application/json",
                    body=body,
                    source_url=None,
                )
            ],
            safe_metadata={"input_kind": "local-json", "detected_shape": shape},
        )
