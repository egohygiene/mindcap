from __future__ import annotations

from mindcap.core.models import CaptureEnvelope, CaptureRequest
from mindcap.plugins.suno.archive.service import SunoWorkspaceCaptureService


class SunoApiCaptureStrategy:
    name = "api"

    def __init__(self, service: SunoWorkspaceCaptureService | None = None) -> None:
        self._service = service

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        service = self._service or SunoWorkspaceCaptureService()
        return service.capture(request)
