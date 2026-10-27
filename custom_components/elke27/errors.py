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

from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)

from .const import DOMAIN

NOT_ACCEPTED_MESSAGE = "The panel did not accept the command."


# Panel error codes that mean the panel rejected the user code (a wrong code,
# or a code not allowed for this action). Each is a user error.
PANEL_ERROR_CODES_REJECTED: frozenset[int] = frozenset({11004, 11008, 11037, 11038})


# Panel error codes that mean the panel refused the action because of the
# current condition of the area or zone, mapped to their translation key. Each
# is a user error, raised like a rejected code.
PANEL_ERROR_CODES_USER_CONDITION: dict[int, str] = {
    11015: "not_ready",  # area not ready to arm (a zone is open)
    11023: "not_bypassable",  # zone can not be bypassed
    11027: "in_alarm",  # area is in alarm
    11028: "not_allowed_armed",  # not allowed while the area is armed
}

# Translation keys of the panel refusals that get the same rollback, event and
# notification handling as a rejected code.
#
# 11028 (not_allowed_armed) is excluded on purpose. That refusal means the area
# is already armed, so the bypasses that went with it are meant to stay; rolling
# them back would un-bypass zones in an armed system. It is still a translated
# user error (see command_error); the arming code keeps its bypasses via
# arming.is_already_armed_refusal.
USER_REFUSAL_KEYS: frozenset[str] = frozenset(
    {"code_rejected", "not_ready", "not_bypassable", "in_alarm"}
)


def user_refusal_key(err: BaseException | None) -> str | None:
    """Return the translation key when the panel refused as a user error."""
    code = panel_error_code(err)
    if code in PANEL_ERROR_CODES_REJECTED:
        return "code_rejected"
    return PANEL_ERROR_CODES_USER_CONDITION.get(code)


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
    return str(err) or type(err).__name__


def command_error(err: BaseException) -> HomeAssistantError:
    """
    Return the Home Assistant error to raise for a failed code command.

    A code the panel refused, or an action refused because of the area or
    zone condition (not ready, in alarm, ...), is a user error, so it becomes a
    translated ServiceValidationError; anything else stays a
    HomeAssistantError. The message never includes the code.
    """
    key = user_refusal_key(err)
    if key is not None:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=key,
        )
    return HomeAssistantError(_error_message(err))


def code_rejected_error(err: BaseException) -> ServiceValidationError | None:
    """
    Return the user-refusal error to re-raise unchanged, or None.

    A user refusal is a rejected code or a user-condition refusal (not ready,
    in alarm, ...). Walks the cause chain: an existing user-refusal
    ServiceValidationError is returned as is; a bare panel refusal gets one
    built by command_error.
    """
    seen: BaseException | None = err
    while seen is not None:
        if (
            isinstance(seen, ServiceValidationError)
            and seen.translation_key in USER_REFUSAL_KEYS
        ):
            return seen
        seen = seen.__cause__
    if user_refusal_key(err) in USER_REFUSAL_KEYS:
        rejected = command_error(err)
        rejected.__cause__ = err
        if isinstance(rejected, ServiceValidationError):
            return rejected
    return None
