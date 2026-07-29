from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mindcap.core.errors import CaptureFailedError
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit


class SavedJsonCaptureStrategy:
    name = "saved-json"

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        path = Path(request.source).expanduser().resolve()
        if not path.is_file():
            raise CaptureFailedError(f'JSON source does not exist: "{path}"')
        body = path.read_bytes()
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
            safe_metadata={"input_kind": "local-json"},
        )
