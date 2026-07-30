from __future__ import annotations

from mindcap.core.models import CaptureEnvelope, CaptureRequest
from mindcap.core.progress import CaptureProgressReporter
from mindcap.plugins.suno.archive.service import SunoWorkspaceCaptureService


class SunoApiCaptureStrategy:
    name = "api"

    def __init__(
        self,
        service: SunoWorkspaceCaptureService | None = None,
        reporter: CaptureProgressReporter | None = None,
    ) -> None:
        self._service = service
        self._reporter = reporter

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        service = self._service or SunoWorkspaceCaptureService(reporter=self._reporter)
        return service.capture(request)
