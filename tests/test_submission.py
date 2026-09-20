"""Submission handling: validation, actions, limits, lockout and buffering."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.hyper_passcode.const import (
    DOMAIN,
    EVENT_SUBMISSION,
    EventType,
    RejectionReason,
    Source,
)
from custom_components.hyper_passcode.exceptions import (
    CodeCollisionError,
    WeakCodeError,
)
from tests.helpers import state_of

ACTION_EVENT = "hyper_passcode_test_action"
FIRE_ACTION = [{"event": ACTION_EVENT}]


async def make_scope_with_action(coordinator, **kwargs):
    """A scope whose default action fires a bus event we can observe."""
    return await coordinator.async_create_scope(
        name=kwargs.pop("name", "Front Door"),
        default_actions=FIRE_ACTION,
        **kwargs,
    )


async def test_a_valid_code_runs_the_default_action(hass: HomeAssistant, coordinator):
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )
    actions = async_capture_events(hass, ACTION_EVENT)

    result = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    assert result.valid is True
    assert result.reason is None
    assert result.label == "Household"
    assert len(actions) == 1


async def test_an_unknown_code_is_refused_and_runs_nothing(
    hass: HomeAssistant, coordinator
):
    scope = await make_scope_with_action(coordinator)
    actions = async_capture_events(hass, ACTION_EVENT)

    result = await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)
    await hass.async_block_till_done()

    assert result.valid is False
    assert result.reason is RejectionReason.UNKNOWN_CODE
    assert not actions


async def test_a_one_time_code_works_exactly_once(hass: HomeAssistant, coordinator):
    scope = await make_scope_with_action(coordinator)
    credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, duration=timedelta(hours=2)
    )
    actions = async_capture_events(hass, ACTION_EVENT)

    first = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    second = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    assert first.valid is True
    assert second.valid is False
    assert second.reason is RejectionReason.MAX_USES_REACHED
    assert len(actions) == 1
    assert credential.use_count == 1


async def test_the_use_is_counted_even_when_the_action_fails(
    hass: HomeAssistant, coordinator
):
    # Otherwise a broken action could be retried to burn through a one-time code.
    scope = await coordinator.async_create_scope(
        name="Broken", default_actions=[{"action": "nonexistent.service"}]
    )
    credential, code = await coordinator.async_create_otp(scope_id=scope.scope_id)

    result = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    assert result.valid is True
    assert credential.use_count == 1

    again = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert again.reason is RejectionReason.MAX_USES_REACHED


async def test_test_code_records_nothing_and_runs_nothing(
    hass: HomeAssistant, coordinator
):
    scope = await make_scope_with_action(coordinator)
    credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )
    actions = async_capture_events(hass, ACTION_EVENT)

    result = await coordinator.async_submit(
        scope.scope_id, code, Source.SERVICE, dry_run=True
    )
    await hass.async_block_till_done()

    assert result.valid is True
    assert credential.use_count == 0
    assert not actions
    assert not coordinator.data.audit


async def test_test_code_explains_why_a_code_is_refused(
    hass: HomeAssistant, coordinator
):
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_credential(
        label="Guest", scope_ids=[scope.scope_id], policy=None
    )
    await coordinator.async_set_enabled(_credential.credential_id, False)

    result = await coordinator.async_submit(
        scope.scope_id, code, Source.SERVICE, dry_run=True
    )
    assert result.reason is RejectionReason.DISABLED


async def test_submission_fires_the_bus_event(hass: HomeAssistant, coordinator):
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )
    events = async_capture_events(hass, EVENT_SUBMISSION)

    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["outcome"] == "valid"
    assert data["event_type"] == str(EventType.VALID)
    assert data["label"] == "Household"
    assert data["source"] == Source.KEYPAD
    assert data["device_id"] is not None


async def test_event_entity_reports_the_outcome(
    hass: HomeAssistant, entry, coordinator
):
    scope = await make_scope_with_action(coordinator)
    await hass.async_block_till_done()
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )

    entity_id = er.async_get(hass).async_get_entity_id(
        "event", DOMAIN, f"{scope.scope_id}_code"
    )
    assert entity_id

    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()
    assert state_of(hass, entity_id).attributes["event_type"] == str(EventType.VALID)

    await coordinator.async_submit(scope.scope_id, "999888", Source.KEYPAD)
    await hass.async_block_till_done()
    state = state_of(hass, entity_id)
    assert state.attributes["event_type"] == str(EventType.INVALID)
    assert state.attributes["reason"] == RejectionReason.UNKNOWN_CODE


async def test_expired_code_gets_its_own_event_type(hass: HomeAssistant, coordinator):
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id,
        valid_until=dt_util.utcnow() - timedelta(minutes=1),
    )

    result = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert result.reason is RejectionReason.EXPIRED
    assert result.event_type is EventType.EXPIRED


async def test_lockout_trips_and_then_recovers(hass: HomeAssistant, entry, coordinator):
    scope = await coordinator.async_create_scope(
        name="Gate", lockout_threshold=3, lockout_duration=300
    )
    await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{scope.scope_id}_lockout"
    )
    assert state_of(hass, entity_id).state == "off"

    for _ in range(3):
        await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)
    await hass.async_block_till_done()

    assert coordinator.is_locked_out(scope.scope_id)
    assert state_of(hass, entity_id).state == "on"

    # A correct code is refused while the lockout holds.
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )
    result = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert result.reason is RejectionReason.LOCKED_OUT

    # And works again once it lapses.
    coordinator.runtime(scope.scope_id).locked_until = None
    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid


async def test_a_success_clears_the_failure_counter(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(name="Gate", lockout_threshold=5)
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )

    for _ in range(3):
        await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)
    assert coordinator.runtime(scope.scope_id).failed_attempts == 3

    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert coordinator.runtime(scope.scope_id).failed_attempts == 0


async def test_collision_is_refused_unconditionally(hass: HomeAssistant, coordinator):
    scope = await make_scope_with_action(coordinator)
    await coordinator.async_create_credential(
        label="First", code="495162", scope_ids=[scope.scope_id]
    )

    with pytest.raises(CodeCollisionError):
        await coordinator.async_create_credential(
            label="Second", code="495162", scope_ids=[scope.scope_id]
        )

    # Still refused with weak-code checking turned off: this is a correctness rule.
    hass.config_entries.async_update_entry(
        coordinator.entry, options={"reject_weak_codes": False}
    )
    with pytest.raises(CodeCollisionError):
        await coordinator.async_create_credential(label="Third", code="495162")


async def test_weak_codes_follow_the_setting(hass: HomeAssistant, entry, coordinator):
    with pytest.raises(WeakCodeError):
        await coordinator.async_create_credential(label="Weak", code="123456")

    hass.config_entries.async_update_entry(entry, options={"reject_weak_codes": False})
    await hass.async_block_till_done()

    credential, _code = await entry.runtime_data.async_create_credential(
        label="Weak", code="123456"
    )
    assert credential.label == "Weak"


async def test_blocklisted_codes_are_refused(hass: HomeAssistant, entry, coordinator):
    hass.config_entries.async_update_entry(
        entry, options={"weak_code_blocklist": ["495162"]}
    )
    await hass.async_block_till_done()

    with pytest.raises(WeakCodeError):
        await entry.runtime_data.async_create_credential(label="House", code="495162")


async def test_audit_log_records_both_outcomes(hass: HomeAssistant, coordinator):
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )

    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await coordinator.async_submit(scope.scope_id, "000111", Source.UI)

    entries = coordinator.data.audit
    assert len(entries) == 2
    assert entries[0].outcome == "valid"
    assert entries[0].label == "Household"
    assert entries[1].outcome == "invalid"
    assert entries[1].reason == RejectionReason.UNKNOWN_CODE
    # The typed code is not recorded by default.
    assert entries[1].typed is None


async def test_failed_plaintext_is_recorded_only_when_asked(
    hass: HomeAssistant, entry, coordinator
):
    hass.config_entries.async_update_entry(
        entry, options={"log_failed_plaintext": True}
    )
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    scope = await make_scope_with_action(coordinator)
    await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)

    assert coordinator.data.audit[-1].typed == "000111"


async def test_revoke_all_filters_by_tag(hass: HomeAssistant, coordinator):
    scope = await make_scope_with_action(coordinator)
    guest, _ = await coordinator.async_create_credential(
        label="Guest", scope_ids=[scope.scope_id], tags=["guest"]
    )
    household, _ = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id], tags=["family"]
    )

    revoked = await coordinator.async_revoke_all(tags=["guest"])

    assert revoked == 1
    assert guest.revoked is True
    assert household.revoked is False
