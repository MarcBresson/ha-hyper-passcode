"""Errors raised by HyperPasscode.

All derive from HomeAssistantError so they surface as readable messages in the UI and
in service call responses rather than as tracebacks.
"""

from homeassistant.exceptions import HomeAssistantError


class HyperPasscodeError(HomeAssistantError):
    """Base class for every error this integration raises."""


class UnknownScopeError(HyperPasscodeError):
    """The referenced scope does not exist."""


class UnknownCredentialError(HyperPasscodeError):
    """The referenced credential does not exist."""


class CodeCollisionError(HyperPasscodeError):
    """A credential with this code already exists.

    Refused unconditionally: two identical active codes make the audit log
    unattributable, which defeats the monitoring the integration exists to provide.
    """


class WeakCodeError(HyperPasscodeError):
    """The code was rejected by the weak-code rules.

    Only raised while the ``reject_weak_codes`` setting is on.
    """
