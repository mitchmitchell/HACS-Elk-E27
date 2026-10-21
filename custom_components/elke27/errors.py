"""Elke27 error groups, messages and classifiers."""

from __future__ import annotations

from typing import Any, Literal

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

NOT_ACCEPTED_MESSAGE = (
    "The panel didn't accept the command. Check that the area is ready, then try again."
)


PANEL_ERROR_CODE_REJECTED = 11008


CODE_REJECTED_MESSAGE = (
    "The panel rejected your user code. Check the user code and that it's allowed "
    "for this area."
)


TIMEOUT_MESSAGE = (
    "The panel didn't respond in time. Check the panel to see if the command worked."
)


CONNECTION_MESSAGE = (
    "The connection to the panel dropped. Check the panel to see if the command worked."
)


NOT_CONNECTED_MESSAGE = (
    "The panel isn't connected. The command wasn't sent. "
    "Check its network connection, then try again."
)


PANEL_ERROR_MESSAGE = (
    "The panel couldn't complete the command. Try again. "
    "If it keeps happening, check the panel."
)


PinAction = Literal["arm", "disarm", "bypass", "unlock", "lock", "control"]

_PIN_ACTION_PHRASE: dict[PinAction, str] = {
    "arm": "arm this area",
    "disarm": "disarm this area",
    "bypass": "bypass this zone",
    "unlock": "unlock this lock",
    "lock": "lock this lock",
    "control": "control this device",
}


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


def homeassistant_error(
    translation_key: str,
    message: str,
    /,
    *,
    translation_placeholders: dict[str, str] | None = None,
) -> HomeAssistantError:
    """Build a translated HomeAssistantError for this integration."""
    kwargs: dict[str, Any] = {
        "translation_domain": DOMAIN,
        "translation_key": translation_key,
    }
    if translation_placeholders is not None:
        kwargs["translation_placeholders"] = translation_placeholders
    return HomeAssistantError(message, **kwargs)


def service_validation_error(
    translation_key: str,
    message: str,
    /,
    *,
    translation_placeholders: dict[str, str] | None = None,
) -> ServiceValidationError:
    """Build a translated ServiceValidationError for this integration."""
    kwargs: dict[str, Any] = {
        "translation_domain": DOMAIN,
        "translation_key": translation_key,
    }
    if translation_placeholders is not None:
        kwargs["translation_placeholders"] = translation_placeholders
    return ServiceValidationError(message, **kwargs)


def pin_required_error(action: PinAction | None = None) -> ServiceValidationError:
    """Return the error when the panel requires a user code for an action."""
    if action is not None:
        phrase = _PIN_ACTION_PHRASE[action]
        message = f"Enter your user code to {phrase}."
        return service_validation_error(
            "pin_required",
            message,
            translation_placeholders={"action": phrase},
        )
    message = "Enter your user code and try again."
    return service_validation_error("pin_required_fallback", message)


def code_not_numeric_error() -> ServiceValidationError:
    """Return the error for a non-numeric user code."""
    return service_validation_error(
        "code_not_numeric", "Your user code can only contain numbers."
    )


def code_all_zero_error() -> ServiceValidationError:
    """Return the error for an all-zero user code."""
    return service_validation_error(
        "code_all_zero", "Your user code can't be all zeros."
    )


def arm_mode_not_supported_error() -> HomeAssistantError:
    """Return the error for an unsupported arming mode."""
    return homeassistant_error(
        "arm_mode_not_supported",
        "This panel doesn't support that arming mode.",
    )


def arm_mode_not_supported_validation() -> ServiceValidationError:
    """Return the service validation error for an unsupported arming mode."""
    return service_validation_error(
        "arm_mode_not_supported",
        "This panel doesn't support that arming mode.",
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


def command_homeassistant_error(err: BaseException) -> HomeAssistantError:
    """Return a translated HomeAssistantError for a failed panel command."""
    if is_timeout_error(err):
        return homeassistant_error("command_timeout", TIMEOUT_MESSAGE)
    if is_connection_error(err):
        return homeassistant_error("command_connection_lost", CONNECTION_MESSAGE)
    return homeassistant_error("panel_error", PANEL_ERROR_MESSAGE)


def command_error(err: BaseException) -> HomeAssistantError | ServiceValidationError:
    """
    Return the Home Assistant error to raise for a failed code command.

    A code the panel refused is a user error, so it becomes a translated
    ServiceValidationError; anything else stays a HomeAssistantError. The
    message never includes the code.
    """
    if panel_error_code(err) == PANEL_ERROR_CODE_REJECTED:
        return service_validation_error("code_rejected", CODE_REJECTED_MESSAGE)
    return command_homeassistant_error(err)


def code_rejected_error(err: BaseException) -> ServiceValidationError | None:
    """
    Return the code_rejected error to re-raise unchanged, or None.

    Walks the cause chain: an existing code_rejected ServiceValidationError is
    returned as is; a bare 11008 panel refusal gets one built by command_error.
    """
    seen: BaseException | None = err
    while seen is not None:
        if (
            isinstance(seen, ServiceValidationError)
            and seen.translation_key == "code_rejected"
        ):
            return seen
        seen = seen.__cause__
    if panel_error_code(err) == PANEL_ERROR_CODE_REJECTED:
        rejected = command_error(err)
        rejected.__cause__ = err
        if isinstance(rejected, ServiceValidationError):
            return rejected
    return None
