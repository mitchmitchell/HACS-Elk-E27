"""Elke27 error groups, messages and classifiers."""

from __future__ import annotations

from elke27_lib.errors import (
    E27Error,
    E27NotReady,
    E27Timeout,
    E27TransportError,
    Elke27AuthError,
    Elke27ConnectionError,
    Elke27CryptoError,
    Elke27DisconnectedError,
    Elke27Error,
    Elke27InvalidArgument,
    Elke27LinkRequiredError,
    Elke27TimeoutError,
)

from homeassistant.exceptions import ConfigEntryNotReady

NOT_ACCEPTED_MESSAGE = "The panel did not accept the command."


TIMEOUT_MESSAGE = (
    "The panel did not respond in time; the command may not have been applied."
)


CONNECTION_MESSAGE = (
    "Lost connection to the panel; the command may not have been applied."
)


# Every library failure a command can surface: elke27's public errors, its
# lower-level E27 errors (async_execute returns these), and raw timeouts/OS errors.
COMMAND_ERRORS: tuple[type[Exception], ...] = (
    Elke27Error,
    Elke27InvalidArgument,
    E27Error,
    TimeoutError,
    OSError,
)


AUTH_ERRORS: tuple[type[Exception], ...] = (
    Elke27LinkRequiredError,
    Elke27AuthError,
    Elke27CryptoError,
)


def panel_error_code(err: BaseException | None) -> int | None:
    """Return the panel error code on an error or its cause chain."""
    while err is not None:
        code = getattr(err, "panel_error_code", None)
        if code is not None:
            return int(code)
        err = err.__cause__
    return None


def is_reconnect_retryable(err: BaseException) -> bool:
    """Return True when a failed reconnect should be retried with backoff."""
    if isinstance(err, ConfigEntryNotReady):
        return True
    return is_connection_error(err) or is_timeout_error(err)


def is_timeout_error(err: BaseException) -> bool:
    """Return True for any elke27 or raw timeout."""
    return isinstance(err, (Elke27TimeoutError, E27Timeout, TimeoutError))


def is_connection_error(err: BaseException) -> bool:
    """Return True for any elke27 transport/session failure."""
    if is_timeout_error(err):
        return False
    return isinstance(
        err,
        (
            Elke27ConnectionError,
            Elke27DisconnectedError,
            E27TransportError,
            E27NotReady,
            OSError,
        ),
    )


def _error_message(err: BaseException) -> str:
    """Return a user-facing message for a library error."""
    if is_timeout_error(err):
        return TIMEOUT_MESSAGE
    if is_connection_error(err):
        return CONNECTION_MESSAGE
    if isinstance(err, Elke27Error) and err.user_message:
        return err.user_message
    return str(err) or type(err).__name__
