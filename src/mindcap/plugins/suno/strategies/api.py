from __future__ import annotations

from mindcap.core.models import CaptureEnvelope, CaptureRequest
from mindcap.plugins.suno.archive.service import SunoWorkspaceCaptureService


class SunoApiCaptureStrategy:
    name = "api"

    def __init__(self, service: SunoWorkspaceCaptureService | None = None) -> None:
        self._service = service or SunoWorkspaceCaptureService()

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        return self._service.capture(request)
