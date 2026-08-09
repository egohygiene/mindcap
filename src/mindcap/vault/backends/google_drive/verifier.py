from __future__ import annotations

from mindcap.vault.backends.protocols import VerificationResult


class GoogleDriveArtifactVerifier:
    def verify_checksum(
        self,
        *,
        expected_sha256: str,
        expected_size: int,
        remote_sha256: str | None,
        remote_size: int | None,
    ) -> VerificationResult:
        if remote_size is None or remote_size != expected_size:
            return VerificationResult(verified=False, reason="size mismatch")
        if remote_sha256 is None:
            return VerificationResult(
                verified=False,
                reason="remote sha256 unavailable",
            )
        if remote_sha256 != expected_sha256:
            return VerificationResult(verified=False, reason="sha256 mismatch")
        return VerificationResult(verified=True)
