"""
E27 error contract for provisioning and runtime.

This module defines structured exceptions used by the E27 implementation.
Home Assistant (and other callers) should use these types to decide whether
to retry, re-auth, or initiate provisioning.

Aligned decisions:
- DDR-0019: Provisioning vs Runtime Responsibilities and Module Boundaries
- DDR-0020: api_link Provisioning Failure is Silent; Timeout is the Only Signal
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class E27ErrorCode(str, Enum):
    """Stable error codes for logging and external mapping."""
    PROVISIONING_REQUIRED = "provisioning_required"
    PROVISIONING_TIMEOUT = "provisioning_timeout"
    LINK_INVALID = "link_invalid"
    AUTH_FAILED = "auth_failed"
    AUTH_REQUIRED = "auth_required"
    INVALID_PIN = "invalid_pin"
    INVALID_CREDENTIALS = "invalid_credentials"
    MISSING_CONTEXT = "missing_context"
    PROTOCOL_ERROR = "protocol_error"
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    NOT_READY = "not_ready"
    NOT_AUTHENTICATED = "not_authenticated"
    PERMISSION_DENIED = "permission_denied"
    PANEL_NOT_DISARMED = "panel_not_disarmed"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class E27ErrorContext:
    """
    Optional structured context for debugging/logging.

    Keep this safe for logs: do NOT include access codes, passphrases, PINs,
    raw decrypted payloads, or key material.
    """
    host: str | None = None
    port: int | None = None
    phase: str | None = None  # e.g. "discovery", "api_link", "hello", "authenticate", "call"
    detail: str | None = None  # short non-secret info
    seq: int | None = None
    session_id: int | None = None


class E27Error(RuntimeError):
    """
    Base exception for all E27-specific failures.

    `message` should be clear English suitable for logs.
    `code` is a stable identifier suitable for programmatic mapping.
    `context` should never contain secrets.
    """

    def __init__(
        self,
        message: str,
        *,
        code: E27ErrorCode = E27ErrorCode.INTERNAL_ERROR,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code: E27ErrorCode = code
        self.context: E27ErrorContext | None = context
        self.__cause__ = cause


class E27ProvisioningRequired(E27Error):
    """
    Raised when runtime operation requires link credentials (linkkey/linkhmac),
    but none are available.

    This is the expected signal for a Home Assistant provisioning / reauth flow.
    """

    def __init__(
        self,
        message: str = "Provisioning is required: missing E27 link credentials (linkkey/linkhmac).",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.PROVISIONING_REQUIRED,
            context=context,
            cause=cause,
        )


class E27ProvisioningTimeout(E27Error):
    """
    Raised when api_link provisioning fails due to silent non-response.

    Per DDR-0020, incorrect access code and/or passphrase (and some client_identity
    mismatches) may cause the panel to not respond at all.
    """

    def __init__(
        self,
        message: str = (
            "Provisioning failed: the panel did not respond to api_link within the timeout. "
            "Verify access code/passphrase and connectivity, then retry."
        ),
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.PROVISIONING_TIMEOUT,
            context=context,
            cause=cause,
        )


class E27LinkInvalid(E27Error):
    """
    Raised when stored link credentials appear invalid.

    Typical triggers:
    - hello response sk/shm decrypt fails (MAGIC mismatch / invalid plaintext)
    - consistent decrypt/parse failures using a stored linkkey
    """

    def __init__(
        self,
        message: str = (
            "Stored E27 link credentials appear invalid (unable to establish session keys). "
            "Re-provisioning may be required."
        ),
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.LINK_INVALID,
            context=context,
            cause=cause,
        )


class E27AuthFailed(E27Error):
    """
    Raised when authentication fails (e.g., incorrect PIN or access level denied).

    Prefer raising this only when an explicit authenticated response indicates failure
    (e.g., error_code != 0), not for timeouts.
    """

    def __init__(
        self,
        message: str = "Authentication failed (PIN rejected or insufficient privileges).",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.AUTH_FAILED,
            context=context,
            cause=cause,
        )


class E27ProtocolError(E27Error):
    """
    Raised for protocol-level violations.

    Examples:
    - CRC failures after deframing
    - invalid LENGTH fields
    - MAGIC mismatch after decrypt (schema-0)
    - malformed payloads that violate expected invariants
    """

    def __init__(
        self,
        message: str = "E27 protocol error.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.PROTOCOL_ERROR,
            context=context,
            cause=cause,
        )


class ProtocolError(E27ProtocolError):
    """Stable public alias for protocol-level errors."""


class E27TransportError(E27Error):
    """
    Raised for socket/transport failures.

    Examples:
    - connection refused
    - connection reset
    - broken pipe
    - network unreachable
    """

    def __init__(
        self,
        message: str = "E27 transport error (socket/connectivity failure).",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.TRANSPORT_ERROR,
            context=context,
            cause=cause,
        )


class ConnectionLost(E27TransportError):
    """Raised when an established connection is lost."""


class E27Timeout(E27Error):
    """
    Raised for timeouts that are not specifically provisioning timeouts.

    Use for:
    - waiting for a response to an authenticated encrypted call
    - waiting for hello response during runtime
    """

    def __init__(
        self,
        message: str = "E27 operation timed out waiting for a response.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.TIMEOUT,
            context=context,
            cause=cause,
        )


class E27NotReady(E27Error):
    """
    Raised when an operation is attempted before the session is ready.

    Example:
    - trying to send encrypted commands before hello/auth has completed
    """

    def __init__(
        self,
        message: str = "E27 session is not ready for this operation.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.NOT_READY,
            context=context,
            cause=cause,
        )


class NotAuthenticatedError(E27Error):
    """
    Raised when an operation requires authentication or an encryption session.
    """

    def __init__(
        self,
        message: str = "Authentication required for this operation.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.NOT_AUTHENTICATED,
            context=context,
            cause=cause,
        )


class E27MissingContext(E27Error):
    """
    Raised when required panel/client_identity/session context is missing.
    """

    def __init__(
        self,
        message: str = "Missing required connection context.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.MISSING_CONTEXT,
            context=context,
            cause=cause,
        )


class MissingContext(E27MissingContext):
    """Stable public alias for missing connection context."""


class AuthorizationRequired(E27Error):
    """
    Raised when an operation requires authorization/provisioning.
    """

    def __init__(
        self,
        message: str = "Authorization is required for this operation.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.AUTH_REQUIRED,
            context=context,
            cause=cause,
        )


class PermissionDeniedError(E27Error):
    """
    Raised when current credentials are insufficient for a command.
    """

    def __init__(
        self,
        message: str = "Permission denied for this operation.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.PERMISSION_DENIED,
            context=context,
            cause=cause,
        )


class PanelNotDisarmedError(E27Error):
    """
    Raised when a command requires all areas to be disarmed.
    """

    def __init__(
        self,
        message: str = "Panel must be fully disarmed for this operation.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.PANEL_NOT_DISARMED,
            context=context,
            cause=cause,
        )


class InvalidCredentials(E27Error):
    """
    Raised when provided credentials are invalid or rejected.
    """

    def __init__(
        self,
        message: str = "Credentials were rejected or are invalid.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.INVALID_CREDENTIALS,
            context=context,
            cause=cause,
        )


class InvalidLinkKeys(E27Error):
    """
    Raised when stored link keys cannot establish a session.
    """

    def __init__(
        self,
        message: str = "Stored link keys are invalid or rejected.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.LINK_INVALID,
            context=context,
            cause=cause,
        )


class InvalidPin(E27Error):
    """
    Raised when authentication fails due to an invalid PIN.
    """

    def __init__(
        self,
        message: str = "PIN was rejected or is invalid.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.INVALID_PIN,
            context=context,
            cause=cause,
        )


class MissingPinError(E27Error):
    """
    Raised when a command requires a PIN but none was provided.
    """

    def __init__(
        self,
        message: str = "PIN required for this command.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.INVALID_PIN,
            context=context,
            cause=cause,
        )


class InvalidPinError(E27Error):
    """
    Raised when a command PIN is invalid.
    """

    def __init__(
        self,
        message: str = "PIN must be a positive integer.",
        *,
        context: E27ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code=E27ErrorCode.INVALID_PIN,
            context=context,
            cause=cause,
        )


class CryptoError(ProtocolError):
    """Raised for crypto/framing/CRC failures treated as protocol errors."""


def _scrub_text(text: str) -> str:
    if not text:
        return text
    replacements = (
        "passphrase",
        "access_code",
        "accesscode",
        "pin",
        "linkkey",
        "linkhmac",
        "tempkey",
        "session_key",
        "token",
    )
    scrubbed = text
    for key in replacements:
        lowered = scrubbed.lower()
        idx = lowered.find(key)
        if idx == -1:
            continue
        end = len(scrubbed)
        for sep in (" ", ",", ";", ")"):
            pos = scrubbed.find(sep, idx)
            if pos != -1:
                end = min(end, pos)
        scrubbed = scrubbed[: idx + len(key)] + "=***" + scrubbed[end:]
    return scrubbed


class Elke27Error(Exception):
    """
    Base exception for v2 public API errors.

    These errors are exceptions-only and safe to surface to callers.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        is_transient: bool,
        user_message: str | None = None,
    ) -> None:
        safe_message = _scrub_text(user_message or message)
        super().__init__(safe_message)
        self.code = code
        self.is_transient = is_transient
        self.user_message = safe_message

    def __str__(self) -> str:
        return self.user_message

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(code={self.code!r}, "
            f"is_transient={self.is_transient!r}, user_message={self.user_message!r})"
        )


class Elke27TransientError(Elke27Error):
    def __init__(self, message: str, *, code: str = "transient", user_message: str | None = None) -> None:
        super().__init__(message, code=code, is_transient=True, user_message=user_message)


class Elke27ConnectionError(Elke27TransientError):
    def __init__(self, message: str = "Connection error.", *, user_message: str | None = None) -> None:
        super().__init__(message, code="connection_error", user_message=user_message)


class Elke27TimeoutError(Elke27TransientError):
    def __init__(self, message: str = "Operation timed out.", *, user_message: str | None = None) -> None:
        super().__init__(message, code="timeout", user_message=user_message)


class Elke27DisconnectedError(Elke27TransientError):
    def __init__(self, message: str = "Disconnected.", *, user_message: str | None = None) -> None:
        super().__init__(message, code="disconnected", user_message=user_message)


class Elke27AuthError(Elke27Error):
    def __init__(self, message: str = "Authentication failed.", *, user_message: str | None = None) -> None:
        super().__init__(message, code="auth", is_transient=False, user_message=user_message)


class Elke27LinkRequiredError(Elke27Error):
    def __init__(self, message: str = "Linking required.", *, user_message: str | None = None) -> None:
        super().__init__(message, code="link_required", is_transient=False, user_message=user_message)


class Elke27PermissionError(Elke27Error):
    def __init__(
        self,
        message: str = "Permission denied.",
        *,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message, code="permission", is_transient=False, user_message=user_message)


class Elke27PinRequiredError(Elke27Error):
    def __init__(
        self,
        message: str = "A PIN is required to perform this action.",
        *,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message, code="pin_required", is_transient=False, user_message=user_message)


class Elke27ProtocolError(Elke27Error):
    def __init__(self, message: str = "Protocol error.", *, user_message: str | None = None) -> None:
        super().__init__(message, code="protocol", is_transient=False, user_message=user_message)


class Elke27CryptoError(Elke27Error):
    def __init__(self, message: str = "Cryptographic error.", *, user_message: str | None = None) -> None:
        super().__init__(message, code="crypto", is_transient=False, user_message=user_message)


class Elke27InvalidArgument(ValueError):
    """Programmer error for invalid arguments in the v2 API."""

    def __str__(self) -> str:
        return _scrub_text(super().__str__())

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.__str__()!r})"
