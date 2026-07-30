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


class UnsupportedExportError(MindcapError):
    """Raised when the input is not a recognised ChatGPT export."""


class MalformedZipError(MindcapError):
    """Raised when the ZIP input cannot be opened or is structurally invalid."""


class UnsafeZipEntryError(MindcapError):
    """Raised when a ZIP entry would escape the extraction directory."""


class UnsupportedConversationSchemaError(MindcapError):
    """Raised when a conversation JSON does not match any supported schema."""


class MissingConversationIdError(MindcapError):
    """Raised when a conversation record has no usable identifier."""


class GraphIntegrityError(MindcapError):
    """Raised when a conversation graph fails structural integrity checks."""


class ExportIngestionError(MindcapError):
    """Raised when a batch export cannot be ingested."""
