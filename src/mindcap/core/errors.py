class MindcapError(Exception):
    """Base error for expected Mindcap failures."""


class InvalidSourceError(MindcapError):
    """Raised when a source identifier cannot be validated."""


class AuthenticationRequiredError(MindcapError):
    """Raised when the dedicated browser is not authenticated."""


class CaptureFailedError(MindcapError):
    """Raised when a strategy cannot acquire a usable source payload."""


class NormalizationError(MindcapError):
    """Raised when captured data cannot be normalized safely."""


class VerificationError(MindcapError):
    """Raised when a bundle fails integrity verification."""


class StableChromeNotFoundError(MindcapError):
    """Raised when stable Google Chrome cannot be located on this system."""


class ProfileLockedError(MindcapError):
    """Raised when the dedicated Chrome profile is locked by another process."""
