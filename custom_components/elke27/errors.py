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

NOT_ACCEPTED_TRANSLATION_KEY = "not_accepted"
NOT_CONNECTED_TRANSLATION_KEY = "not_connected"

PANEL_ERROR_CODES_REJECTED: frozenset[int] = frozenset({11004, 11008, 11037, 11038})

PANEL_ERROR_CODES_USER_CONDITION: dict[int, str] = {
    11015: "not_ready",
    11023: "not_bypassable",
    11027: "in_alarm",
    11028: "not_allowed_armed",
}

USER_REFUSAL_KEYS: frozenset[str] = frozenset(
    {"code_rejected", "not_ready", "not_bypassable", "in_alarm"}
)


def user_refusal_key(err: BaseException | None) -> str | None:
    """
    Return the translation key when the panel refused as a user error.

    Code 11028 maps to not_allowed_armed but stays out of USER_REFUSAL_KEYS;
    automatic arming handles it through is_already_armed_refusal instead.
    """
    code = panel_error_code(err)
    if code is None:
        return None
    if code in PANEL_ERROR_CODES_REJECTED:
        return "code_rejected"
    return PANEL_ERROR_CODES_USER_CONDITION.get(code)


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


PinAction = Literal["arm", "disarm", "bypass", "unlock", "lock", "control"]

_PIN_REQUIRED: dict[PinAction, str] = {
    "arm": "pin_required_arm",
    "disarm": "pin_required_disarm",
    "bypass": "pin_required_bypass",
    "unlock": "pin_required_unlock",
    "lock": "pin_required_lock",
    "control": "pin_required_control",
}


def homeassistant_error(
    *,
    translation_key: str,
    translation_placeholders: dict[str, str] | None = None,
) -> HomeAssistantError:
    """Build a translated HomeAssistantError for this integration."""
    kwargs: dict[str, Any] = {
        "translation_domain": DOMAIN,
        "translation_key": translation_key,
    }
    if translation_placeholders is not None:
        kwargs["translation_placeholders"] = translation_placeholders
    return HomeAssistantError(**kwargs)


def service_validation_error(
    *,
    translation_key: str,
    translation_placeholders: dict[str, str] | None = None,
) -> ServiceValidationError:
    """Build a translated ServiceValidationError for this integration."""
    kwargs: dict[str, Any] = {
        "translation_domain": DOMAIN,
        "translation_key": translation_key,
    }
    if translation_placeholders is not None:
        kwargs["translation_placeholders"] = translation_placeholders
    return ServiceValidationError(**kwargs)


def pin_required_error(action: PinAction | None = None) -> ServiceValidationError:
    """Return the error when the panel requires a user code for an action."""
    if action == "arm":
        return service_validation_error(translation_key="pin_required_arm")
    if action == "disarm":
        return service_validation_error(translation_key="pin_required_disarm")
    if action == "bypass":
        return service_validation_error(translation_key="pin_required_bypass")
    if action == "unlock":
        return service_validation_error(translation_key="pin_required_unlock")
    if action == "lock":
        return service_validation_error(translation_key="pin_required_lock")
    if action == "control":
        return service_validation_error(translation_key="pin_required_control")
    return service_validation_error(translation_key="pin_required_fallback")


def code_not_numeric_error() -> ServiceValidationError:
    """Return the error for a non-numeric user code."""
    return service_validation_error(translation_key="code_not_numeric")


def code_all_zero_error() -> ServiceValidationError:
    """Return the error for an all-zero user code."""
    return service_validation_error(translation_key="code_all_zero")


def arm_mode_not_supported_error() -> HomeAssistantError:
    """Return the error for an unsupported arming mode."""
    return homeassistant_error(translation_key="arm_mode_not_supported")


def arm_mode_not_supported_validation() -> ServiceValidationError:
    """Return the service validation error for an unsupported arming mode."""
    return service_validation_error(translation_key="arm_mode_not_supported")


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
        return homeassistant_error(translation_key="command_timeout")
    if is_connection_error(err):
        return homeassistant_error(translation_key="command_connection_lost")
    return homeassistant_error(translation_key="panel_error")


def command_error(err: BaseException) -> HomeAssistantError | ServiceValidationError:
    """
    Return the Home Assistant error to raise for a failed code command.

    A code the panel refused, or an action refused because of the area or
    zone condition (not ready, in alarm, ...), is a user error, so it becomes a
    translated ServiceValidationError; anything else stays a
    HomeAssistantError. The message never includes the code.
    """
    key = user_refusal_key(err)
    if key == "code_rejected":
        return service_validation_error(translation_key="code_rejected")
    if key == "not_ready":
        return service_validation_error(translation_key="not_ready")
    if key == "not_bypassable":
        return service_validation_error(translation_key="not_bypassable")
    if key == "in_alarm":
        return service_validation_error(translation_key="in_alarm")
    if key == "not_allowed_armed":
        return service_validation_error(translation_key="not_allowed_armed")
    return command_homeassistant_error(err)


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
