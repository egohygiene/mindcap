from __future__ import annotations


class GoogleDriveError(RuntimeError):
    pass


class GoogleDriveAuthError(GoogleDriveError):
    pass


class GoogleDriveRetryableError(GoogleDriveError):
    pass


class GoogleDriveQuotaError(GoogleDriveError):
    pass
