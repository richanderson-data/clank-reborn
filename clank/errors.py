"""Exception hierarchy for Clank-Reborn.

Every error the user can hit is a subclass of ClankError so the CLI can
render a consistent, actionable message instead of a traceback.
"""


class ClankError(Exception):
    """Base class for all expected Clank errors."""


class ConfigError(ClankError):
    """Invalid or missing configuration."""


class ProviderError(ClankError):
    """Base class for model provider failures."""


class ProviderConnectionError(ProviderError):
    """The provider host could not be reached."""


class ProviderHTTPError(ProviderError):
    """The provider answered with an HTTP error status."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProviderProtocolError(ProviderError):
    """The provider sent a malformed or unexpected response."""


class StreamInterrupted(ProviderError):
    """A stream failed after it had started emitting events."""


class ToolError(ClankError):
    """A tool failed for a valid but unusable request."""


class PermissionDenied(ToolError):
    """A tool request was refused by the directory sandbox."""
