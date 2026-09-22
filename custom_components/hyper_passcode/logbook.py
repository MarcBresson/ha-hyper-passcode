"""Human-readable Logbook entries for submissions.

Without this, the Logbook and the History "what happened" panel fall back to a
generic description of the submission event, dropping the label, the rejection
reason and whether the use fell inside a grace period -- exactly the detail
those views are being asked for.
"""

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    ATTR_IN_GRACE_PERIOD,
    ATTR_LABEL,
    ATTR_OUTCOME,
    ATTR_REASON,
    DOMAIN,
    EVENT_SUBMISSION,
    Outcome,
    RejectionReason,
)

#: What each rejection reason means, in the Logbook's voice.
REASON_TEXT: dict[str, str] = {
    RejectionReason.UNKNOWN_CODE: "the code was not recognized",
    RejectionReason.NO_GRANT: "the code has no access to this scope",
    RejectionReason.DISABLED: "the code is disabled",
    RejectionReason.REVOKED: "the code has been revoked",
    RejectionReason.NOT_YET_VALID: "the code is not yet valid",
    RejectionReason.EXPIRED: "the code has expired",
    RejectionReason.OUT_OF_SCHEDULE: "the code was used outside its schedule",
    RejectionReason.CONDITION_FAILED: "the code's condition was not met",
    RejectionReason.MAX_USES_REACHED: "the code has reached its maximum uses",
    RejectionReason.RATE_LIMITED: "too many attempts were made too quickly",
    RejectionReason.LOCKED_OUT: "the scope is locked out",
}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe hyper_passcode logbook events."""

    @callback
    def async_describe_submission_event(event: Event) -> dict[str, str]:
        """Turn a submission event into a Logbook entry."""
        data = event.data
        label = data.get(ATTR_LABEL)
        who = f"'{label}'" if label else "a code"

        if data.get(ATTR_OUTCOME) == str(Outcome.VALID):
            message = f"{who} was accepted"
            if data.get(ATTR_IN_GRACE_PERIOD):
                message += ", in grace period"
        else:
            message = f"{who} was rejected"
            reason = data.get(ATTR_REASON)
            if reason in REASON_TEXT:
                message += f": {REASON_TEXT[reason]}"

        return {
            LOGBOOK_ENTRY_NAME: data.get("scope_name") or DOMAIN,
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    async_describe_event(DOMAIN, EVENT_SUBMISSION, async_describe_submission_event)
