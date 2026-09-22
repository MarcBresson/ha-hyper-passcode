"""Logbook descriptions for the submission event."""

from homeassistant.core import Event, HomeAssistant

from custom_components.hyper_passcode.const import (
    ATTR_IN_GRACE_PERIOD,
    ATTR_LABEL,
    ATTR_OUTCOME,
    ATTR_REASON,
    ATTR_SCOPE_ID,
    EVENT_SUBMISSION,
    Outcome,
    RejectionReason,
)
from custom_components.hyper_passcode.logbook import async_describe_events


def _describe(hass: HomeAssistant, data: dict) -> dict[str, str]:
    registered = {}

    def async_describe_event(domain, event_type, describe):
        registered[event_type] = describe

    async_describe_events(hass, async_describe_event)
    event = Event(EVENT_SUBMISSION, data)
    return registered[EVENT_SUBMISSION](event)


def _submission(**overrides) -> dict:
    data = {
        ATTR_SCOPE_ID: "front_door",
        "scope_name": "Front door",
        ATTR_OUTCOME: str(Outcome.VALID),
        ATTR_LABEL: None,
        ATTR_REASON: None,
        ATTR_IN_GRACE_PERIOD: False,
    }
    data.update(overrides)
    return data


def test_an_accepted_code_names_its_label(hass: HomeAssistant):
    description = _describe(hass, _submission(**{ATTR_LABEL: "Cleaner"}))
    assert description["name"] == "Front door"
    assert description["message"] == "'Cleaner' was accepted"


def test_an_accepted_code_notes_the_grace_period(hass: HomeAssistant):
    description = _describe(
        hass, _submission(**{ATTR_LABEL: "Cleaner", ATTR_IN_GRACE_PERIOD: True})
    )
    assert description["message"] == "'Cleaner' was accepted, in grace period"


def test_an_unlabeled_accepted_code_falls_back_to_a_code(hass: HomeAssistant):
    description = _describe(hass, _submission())
    assert description["message"] == "a code was accepted"


def test_a_rejected_code_states_the_reason(hass: HomeAssistant):
    description = _describe(
        hass,
        _submission(
            **{
                ATTR_OUTCOME: str(Outcome.INVALID),
                ATTR_REASON: str(RejectionReason.EXPIRED),
            }
        ),
    )
    assert description["message"] == "a code was rejected: the code has expired"


def test_a_rejected_code_with_a_label_names_it(hass: HomeAssistant):
    description = _describe(
        hass,
        _submission(
            **{
                ATTR_OUTCOME: str(Outcome.INVALID),
                ATTR_LABEL: "Cleaner",
                ATTR_REASON: str(RejectionReason.LOCKED_OUT),
            }
        ),
    )
    assert description["message"] == "'Cleaner' was rejected: the scope is locked out"
