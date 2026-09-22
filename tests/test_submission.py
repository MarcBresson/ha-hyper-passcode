"""Submission handling: validation, actions, limits, lockout and buffering."""

from datetime import timedelta

import pytest
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.hyper_passcode.const import (
    DOMAIN,
    EVENT_SUBMISSION,
    EventType,
    GraceMode,
    Outcome,
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


async def test_a_one_time_code_lets_the_driver_back_in_during_its_grace_period(
    hass: HomeAssistant, coordinator, freezer
):
    scope = await make_scope_with_action(coordinator)
    credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, grace_period_seconds=300
    )
    actions = async_capture_events(hass, ACTION_EVENT)

    first = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    freezer.tick(timedelta(minutes=3))
    second = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    assert first.valid is True
    assert second.valid is True
    assert second.in_grace is True
    # A free use is a real use: the door opens again.
    assert len(actions) == 2
    assert credential.use_count == 2
    assert credential.uncounted_uses == 1
    assert credential.counted_uses == 1

    # And once the window has run out, the one use it was given is gone.
    freezer.tick(timedelta(minutes=3))
    third = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert third.valid is False
    assert third.reason is RejectionReason.MAX_USES_REACHED


async def test_a_sliding_grace_window_keeps_a_code_open_while_the_gaps_stay_short(
    hass: HomeAssistant, coordinator, freezer
):
    scope = await make_scope_with_action(coordinator)
    credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, grace_period_seconds=300
    )
    policy = credential.policy.to_dict()
    policy["grace_mode"] = str(GraceMode.SLIDING)
    await coordinator.async_update_credential(
        credential.credential_id, {"policy": policy}
    )

    for _ in range(3):
        assert (
            await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
        ).valid
        freezer.tick(timedelta(minutes=3))

    # Three uses of a one-time code, because each re-entry pushed the window out.
    assert credential.use_count == 3
    assert credential.counted_uses == 1

    # Let the gap exceed the window and it closes.
    freezer.tick(timedelta(minutes=3))
    lapsed = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert lapsed.reason is RejectionReason.MAX_USES_REACHED


async def test_an_uncounted_use_still_reaches_the_audit_log_and_the_uses_sensor(
    hass: HomeAssistant, coordinator, freezer
):
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, grace_period_seconds=300, label="Delivery"
    )
    await hass.async_block_till_done()

    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    freezer.tick(timedelta(minutes=1))
    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    # Both entries are on the record: the exemption is about the allowance, not
    # about hiding that somebody came through the door.
    assert sum(1 for row in coordinator.data.audit if row.outcome is Outcome.VALID) == 2

    state = state_of(hass, "sensor.delivery_uses")
    assert state.state == "2"
    assert state.attributes["uncounted_uses"] == 1
    assert state.attributes["remaining_uses"] == 0
    assert state.attributes["grace_period_seconds"] == 300

    # And the same three readings as entities of their own, so they show up on the
    # code's device rather than only inside the Uses sensor's attributes.
    assert state_of(hass, "sensor.delivery_uncounted_uses").state == "1"
    last_used = state_of(hass, "sensor.delivery_last_used").state
    last_uncounted = state_of(hass, "sensor.delivery_last_uncounted_use").state
    assert last_used != STATE_UNKNOWN
    # The second entry was the free one, so the two agree.
    assert last_uncounted == last_used


async def test_a_graced_use_says_which_code_it_was_and_that_it_was_free(
    hass: HomeAssistant, entry, coordinator, freezer
):
    scope = await make_scope_with_action(coordinator)
    await hass.async_block_till_done()
    credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, grace_period_seconds=300, label="Delivery"
    )
    event_entity = er.async_get(hass).async_get_entity_id(
        "event", DOMAIN, f"{scope.scope_id}_code"
    )
    last_used = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{scope.scope_id}_last_used"
    )
    events = async_capture_events(hass, EVENT_SUBMISSION)

    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    # The first use is an ordinary one, and every surface says so.
    assert events[0].data["in_grace_period"] is False
    assert state_of(hass, event_entity).attributes["in_grace_period"] is False
    assert state_of(hass, last_used).attributes["in_grace_period"] is False

    freezer.tick(timedelta(minutes=2))
    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    assert events[1].data["credential_id"] == credential.credential_id
    assert events[1].data["in_grace_period"] is True

    attributes = state_of(hass, event_entity).attributes
    assert attributes["credential_id"] == credential.credential_id
    assert attributes["in_grace_period"] is True

    # The scope's last-used sensor carries the id as well as the label, so an
    # automation can match on something that renaming the code will not change.
    attributes = state_of(hass, last_used).attributes
    assert attributes["label"] == "Delivery"
    assert attributes["credential_id"] == credential.credential_id
    assert attributes["in_grace_period"] is True

    assert [row.in_grace for row in coordinator.data.audit] == [False, True]


async def test_a_refused_code_is_never_reported_as_graced(
    hass: HomeAssistant, entry, coordinator, freezer
):
    # in_grace answers "is the window open", which stays true of a code that has
    # just been refused for something else. Nothing came through the door, so the
    # activity surfaces must not claim a use was excused.
    scope = await make_scope_with_action(coordinator)
    await hass.async_block_till_done()
    credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, grace_period_seconds=300
    )
    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await coordinator.async_update_credential(
        credential.credential_id, {"enabled": False}
    )
    freezer.tick(timedelta(minutes=1))
    events = async_capture_events(hass, EVENT_SUBMISSION)

    result = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    assert result.reason is RejectionReason.DISABLED
    assert result.in_grace is True
    assert result.accepted_in_grace is False
    assert events[0].data["in_grace_period"] is False
    assert coordinator.data.audit[-1].in_grace is False


async def test_the_scope_remembers_the_graced_use_across_a_reload(
    hass: HomeAssistant, entry, coordinator, freezer
):
    # Last-used is rebuilt from the audit log rather than persisted separately, so
    # the grace flag has to survive in the row for the sensor to still know.
    scope = await make_scope_with_action(coordinator)
    credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, grace_period_seconds=300, label="Delivery"
    )
    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    freezer.tick(timedelta(minutes=2))
    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data.runtime(scope.scope_id)
    assert runtime.last_label == "Delivery"
    assert runtime.last_credential_id == credential.credential_id
    assert runtime.last_in_grace is True


async def test_testing_a_code_inside_its_grace_period_records_nothing(
    hass: HomeAssistant, coordinator, freezer
):
    scope = await make_scope_with_action(coordinator)
    credential, code = await coordinator.async_create_otp(
        scope_id=scope.scope_id, grace_period_seconds=300
    )
    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    freezer.tick(timedelta(minutes=1))

    result = await coordinator.async_submit(
        scope.scope_id, code, Source.SERVICE, dry_run=True
    )

    # A dry run reports the grace without consuming it.
    assert result.valid is True
    assert result.in_grace is True
    assert credential.use_count == 1
    assert credential.uncounted_uses == 0


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


async def test_the_last_result_sensor_follows_every_submission(
    hass: HomeAssistant, coordinator
):
    # It is not limited to the "Test a code" page: a keypad entry moves it too, and
    # that is what makes it the scope's "what happened last" reading.
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )
    await hass.async_block_till_done()
    assert state_of(hass, "sensor.front_door_last_result").state == STATE_UNKNOWN

    await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    await hass.async_block_till_done()

    state = state_of(hass, "sensor.front_door_last_result")
    assert state.state == str(Outcome.VALID)
    assert state.attributes["label"] == "Household"
    assert state.attributes["source"] == str(Source.KEYPAD)
    assert state.attributes["dry_run"] is False

    await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)
    await hass.async_block_till_done()

    state = state_of(hass, "sensor.front_door_last_result")
    assert state.state == str(RejectionReason.UNKNOWN_CODE)
    assert state.attributes["label"] is None


async def test_a_dry_run_leaves_its_verdict_on_the_last_result_sensor(
    hass: HomeAssistant, coordinator
):
    # The one thing a dry run does record. Without it a test would leave no trace at
    # all, and a surface that checks codes without counting failures needs one.
    scope = await make_scope_with_action(coordinator)
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )

    await coordinator.async_submit(scope.scope_id, code, Source.SERVICE, dry_run=True)
    await hass.async_block_till_done()

    state = state_of(hass, "sensor.front_door_last_result")
    assert state.state == str(Outcome.VALID)
    assert state.attributes["dry_run"] is True
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


async def test_lockout_escalates_and_then_caps(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(
        name="Gate",
        lockout_threshold=3,
        lockout_duration=10,
        lockout_backoff_factor=2,
        lockout_max_duration=30,
    )

    async def trip_lockout():
        for _ in range(3):
            await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)

    now = dt_util.utcnow()

    # First lockout: unaffected by the factor.
    await trip_lockout()
    runtime = coordinator.runtime(scope.scope_id)
    assert runtime.lockout_streak == 1
    assert runtime.locked_until is not None
    assert (runtime.locked_until - now).total_seconds() == pytest.approx(10, abs=1)

    # Second consecutive lockout, no success in between: duration doubles.
    runtime.locked_until = None
    await trip_lockout()
    assert runtime.lockout_streak == 2
    assert runtime.locked_until is not None
    assert (runtime.locked_until - now).total_seconds() == pytest.approx(20, abs=1)

    # Third: would be 40s uncapped, but lockout_max_duration caps it at 30s.
    runtime.locked_until = None
    await trip_lockout()
    assert runtime.lockout_streak == 3
    assert runtime.locked_until is not None
    assert (runtime.locked_until - now).total_seconds() == pytest.approx(30, abs=1)


async def test_a_success_resets_the_lockout_streak(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(
        name="Gate",
        lockout_threshold=3,
        lockout_duration=10,
        lockout_backoff_factor=2,
    )
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )
    runtime = coordinator.runtime(scope.scope_id)

    for _ in range(3):
        await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)
    assert runtime.lockout_streak == 1

    runtime.locked_until = None
    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid
    assert runtime.lockout_streak == 0

    # Streak restarts from scratch after the success.
    now = dt_util.utcnow()
    for _ in range(3):
        await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)
    assert runtime.lockout_streak == 1
    assert runtime.locked_until is not None
    assert (runtime.locked_until - now).total_seconds() == pytest.approx(10, abs=1)


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


async def test_revoke_all_filters_by_scope(hass: HomeAssistant, coordinator):
    front = await make_scope_with_action(coordinator)
    garage = await make_scope_with_action(coordinator, name="Garage")
    guest, _ = await coordinator.async_create_credential(
        label="Guest", scope_ids=[front.scope_id]
    )
    household, _ = await coordinator.async_create_credential(
        label="Household", scope_ids=[garage.scope_id]
    )

    revoked = await coordinator.async_revoke_all(scope_id=front.scope_id)

    assert revoked == 1
    assert guest.revoked is True
    assert household.revoked is False
