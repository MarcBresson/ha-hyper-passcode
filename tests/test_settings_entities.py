"""The settings that live on a scope's or a code's own device.

These used to be fields in the add/edit dialogs. What matters now is that they are
the same settings -- written through to the subentry, honoured by the engine, and
left alone by a dialog that no longer shows them.
"""

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.hyper_passcode.const import (
    DEFAULT_INTER_KEY_TIMEOUT,
    DEFAULT_LOCKOUT_DURATION,
    DEFAULT_LOCKOUT_THRESHOLD,
    GraceMode,
    RejectionReason,
    Source,
    StoreMethod,
)
from tests.helpers import set_number, set_select, set_text, state_of


async def a_code(coordinator, scope, **kwargs):
    """Create a credential named Cleaner, granted on ``scope``."""
    credential, code = await coordinator.async_create_credential(
        label="Cleaner", scope_ids=[scope.scope_id], **kwargs
    )
    return credential, code


async def press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button entity."""
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def switch(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    """Flip a switch entity."""
    await hass.services.async_call(
        "switch",
        "turn_on" if on else "turn_off",
        {"entity_id": entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()


# ----------------------------------------------------------------------
# Scope numbers
# ----------------------------------------------------------------------


async def test_a_new_scope_reports_the_settings_in_effect(
    hass: HomeAssistant, entry, scope
):
    await hass.async_block_till_done()

    def number(entity_id: str) -> float:
        return float(state_of(hass, entity_id).state)

    # Nothing has been written yet, so the two lockout numbers show the per-scope
    # defaults rather than a blank.
    assert number("number.front_door_lockout_threshold") == DEFAULT_LOCKOUT_THRESHOLD
    assert number("number.front_door_lockout_duration") == DEFAULT_LOCKOUT_DURATION
    assert number("number.front_door_inter_key_timeout") == DEFAULT_INTER_KEY_TIMEOUT
    # Zero is how a number entity says "no fixed length, wait for a terminator".
    assert number("number.front_door_code_length") == 0


async def test_a_scope_number_writes_through_to_the_scope(
    hass: HomeAssistant, entry, scope
):
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    await set_number(hass, "number.front_door_code_length", 4)
    assert coordinator.scopes[scope.scope_id].code_length == 4

    # And back to unset, which the model spells None.
    await set_number(hass, "number.front_door_code_length", 0)
    assert coordinator.scopes[scope.scope_id].code_length is None


async def test_a_scope_number_survives_a_restart(hass: HomeAssistant, entry, scope):
    # A value set here is worth nothing if it only lives in the entity: it has to
    # reach the subentry, which is what is actually persisted.
    await hass.async_block_till_done()
    await set_number(hass, "number.front_door_lockout_duration", 45)

    assert entry.subentries[scope.scope_id].data["lockout_duration"] == 45


async def test_a_code_length_set_from_its_entity_governs_the_keypad(
    hass: HomeAssistant, entry, scope
):
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    _credential, code = await coordinator.async_create_credential(
        label="Cleaner", code="4951", scope_ids=[scope.scope_id]
    )
    await hass.async_block_till_done()

    await set_number(hass, "number.front_door_code_length", 4)

    # Four keystrokes and no terminator: the buffer submits on length alone.
    for key in code[:-1]:
        assert await coordinator.async_submit_key(scope.scope_id, key) is None
    result = await coordinator.async_submit_key(scope.scope_id, code[-1])
    assert result is not None
    assert result.valid is True


async def test_a_lockout_threshold_set_from_its_entity_is_enforced(
    hass: HomeAssistant, entry, scope
):
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await set_number(hass, "number.front_door_lockout_threshold", 2)

    for _ in range(2):
        await coordinator.async_submit(scope.scope_id, "000111", Source.KEYPAD)
    await hass.async_block_till_done()

    assert coordinator.is_locked_out(scope.scope_id)


# ----------------------------------------------------------------------
# Credential policy numbers
# ----------------------------------------------------------------------


async def test_policy_numbers_start_at_zero_for_no_limit(
    hass: HomeAssistant, entry, scope
):
    await a_code(entry.runtime_data, scope)
    await hass.async_block_till_done()

    for entity_id in (
        "number.cleaner_max_uses",
        "number.cleaner_uses_per_hour",
        "number.cleaner_uses_per_day",
        "number.cleaner_cooldown",
        "number.cleaner_re_entry_grace_period",
    ):
        assert float(state_of(hass, entity_id).state) == 0, entity_id


async def test_a_use_limit_set_from_its_entity_is_enforced(
    hass: HomeAssistant, entry, scope
):
    coordinator = entry.runtime_data
    credential, code = await a_code(coordinator, scope)
    await hass.async_block_till_done()

    await set_number(hass, "number.cleaner_max_uses", 1)
    assert credential.policy.max_uses == 1

    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid
    refused = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert refused.valid is False
    assert refused.reason is RejectionReason.MAX_USES_REACHED

    # Zero lifts the limit again, and the code works on the very next submission.
    await set_number(hass, "number.cleaner_max_uses", 0)
    assert credential.policy.max_uses is None
    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid


async def test_a_policy_number_does_not_disturb_the_rest_of_the_policy(
    hass: HomeAssistant, entry, scope
):
    from custom_components.hyper_passcode.models import Policy

    coordinator = entry.runtime_data
    credential, _code = await a_code(
        coordinator, scope, policy=Policy(uses_per_day=3, allowed_sources=["keypad"])
    )
    await hass.async_block_till_done()

    await set_number(hass, "number.cleaner_cooldown", 60)

    assert credential.policy.cooldown_seconds == 60
    assert credential.policy.uses_per_day == 3
    assert credential.policy.allowed_sources == ["keypad"]


# ----------------------------------------------------------------------
# Re-entry grace period
# ----------------------------------------------------------------------


async def test_the_grace_window_starts_off_and_fixed(hass: HomeAssistant, entry, scope):
    await a_code(entry.runtime_data, scope)
    await hass.async_block_till_done()

    assert float(state_of(hass, "number.cleaner_re_entry_grace_period").state) == 0
    assert state_of(hass, "select.cleaner_re_entry_grace_window").state == "fixed"


async def test_a_fresh_codes_usage_readings_start_empty(
    hass: HomeAssistant, entry, scope
):
    await a_code(entry.runtime_data, scope)
    await hass.async_block_till_done()

    assert state_of(hass, "sensor.cleaner_uses").state == "0"
    assert state_of(hass, "sensor.cleaner_uncounted_uses").state == "0"
    # Never used, so there is no timestamp to show rather than a misleading epoch.
    assert state_of(hass, "sensor.cleaner_last_used").state == STATE_UNKNOWN
    assert state_of(hass, "sensor.cleaner_last_uncounted_use").state == STATE_UNKNOWN


async def test_a_grace_period_set_from_its_entity_is_enforced(
    hass: HomeAssistant, entry, scope, freezer
):
    coordinator = entry.runtime_data
    credential, code = await a_code(coordinator, scope)
    await hass.async_block_till_done()

    await set_number(hass, "number.cleaner_max_uses", 1)
    await set_number(hass, "number.cleaner_re_entry_grace_period", 300)
    assert credential.policy.grace_period_seconds == 300

    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid
    freezer.tick(timedelta(minutes=2))
    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid

    freezer.tick(timedelta(minutes=4))
    refused = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert refused.reason is RejectionReason.MAX_USES_REACHED

    # Zero turns it off, and the exemption stops on the next submission.
    await set_number(hass, "number.cleaner_re_entry_grace_period", 0)
    assert credential.policy.grace_period_seconds is None


async def test_the_grace_window_mode_is_written_through_to_the_subentry(
    hass: HomeAssistant, entry, scope
):
    coordinator = entry.runtime_data
    credential, _code = await a_code(coordinator, scope)
    await hass.async_block_till_done()

    await set_select(hass, "select.cleaner_re_entry_grace_window", "sliding")

    assert credential.policy.grace_mode is GraceMode.SLIDING
    subentry = coordinator.async_credential_subentry(credential.credential_id)
    # A subentry holds plain JSON, and it is what survives a restart.
    assert subentry.data["policy"]["grace_mode"] == "sliding"


async def test_a_grace_entity_does_not_disturb_the_rest_of_the_policy(
    hass: HomeAssistant, entry, scope
):
    from custom_components.hyper_passcode.models import Policy

    coordinator = entry.runtime_data
    credential, _code = await a_code(
        coordinator, scope, policy=Policy(uses_per_day=3, allowed_sources=["keypad"])
    )
    await hass.async_block_till_done()

    await set_number(hass, "number.cleaner_re_entry_grace_period", 120)
    await set_select(hass, "select.cleaner_re_entry_grace_window", "sliding")

    assert credential.policy.grace_period_seconds == 120
    assert credential.policy.grace_mode is GraceMode.SLIDING
    assert credential.policy.uses_per_day == 3
    assert credential.policy.allowed_sources == ["keypad"]


# ----------------------------------------------------------------------
# Validity window
# ----------------------------------------------------------------------


async def test_the_validity_window_can_be_moved_and_cleared(
    hass: HomeAssistant, entry, scope
):
    coordinator = entry.runtime_data
    credential, code = await a_code(coordinator, scope)
    await hass.async_block_till_done()

    assert state_of(hass, "datetime.cleaner_valid_until").state == "unknown"

    await hass.services.async_call(
        "datetime",
        "set_value",
        {
            "entity_id": "datetime.cleaner_valid_until",
            "datetime": datetime(2020, 1, 1, 9, 0, tzinfo=UTC),
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert credential.policy.valid_until == datetime(2020, 1, 1, 9, 0, tzinfo=UTC)
    expired = await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)
    assert expired.reason is RejectionReason.EXPIRED

    # A datetime entity can report "no bound" but cannot be set back to one, so the
    # button is the only way to reopen the window.
    await press(hass, "button.cleaner_clear_validity_window")

    assert credential.policy.valid_until is None
    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid


# ----------------------------------------------------------------------
# Notes, tags, keep viewable
# ----------------------------------------------------------------------


async def test_notes_and_tags_round_trip(hass: HomeAssistant, entry, scope):
    credential, _code = await a_code(entry.runtime_data, scope)
    await hass.async_block_till_done()

    await set_text(hass, "text.cleaner_notes", "Thursdays, back door")
    await set_text(hass, "text.cleaner_tags", "guest, cleaning")

    assert credential.notes == "Thursdays, back door"
    assert credential.tags == ["guest", "cleaning"]
    assert state_of(hass, "text.cleaner_tags").state == "guest, cleaning"


async def test_tags_set_from_the_entity_drive_bulk_revocation(
    hass: HomeAssistant, entry, scope
):
    coordinator = entry.runtime_data
    credential, _code = await a_code(coordinator, scope)
    await hass.async_block_till_done()

    await set_text(hass, "text.cleaner_tags", "guest")
    assert await coordinator.async_revoke_all(tags=["guest"]) == 1
    assert credential.revoked is True


async def test_keep_viewable_can_be_turned_off_but_not_back_on(
    hass: HomeAssistant, entry, scope
):
    credential, code = await a_code(entry.runtime_data, scope, keep_viewable=True)
    await hass.async_block_till_done()

    assert state_of(hass, "switch.cleaner_keep_viewable").state == "on"
    assert credential.plaintext == code
    # While it is on, the code is readable on the device rather than only in the
    # dialog that created it.
    assert state_of(hass, "sensor.cleaner_code").state == code
    assert state_of(hass, "sensor.cleaner_store_method").state == str(
        StoreMethod.PLAINTEXT
    )

    await switch(hass, "switch.cleaner_keep_viewable", False)
    assert credential.keep_viewable is False
    assert credential.plaintext is None
    assert state_of(hass, "sensor.cleaner_code").state == STATE_UNKNOWN
    assert state_of(hass, "sensor.cleaner_store_method").state == str(
        StoreMethod.HASHED
    )

    # Turning it back on cannot recover a code nothing holds any more, so it says so
    # rather than reporting a viewable code that is not there.
    with pytest.raises(ServiceValidationError):
        await switch(hass, "switch.cleaner_keep_viewable", True)
    assert credential.keep_viewable is False


async def test_turning_the_setting_entities_off_leaves_the_scope_ones_alone(
    hass: HomeAssistant, entry, scope
):
    # ``per_credential_entities`` is about entity-list size, which is a per-code
    # problem; a scope's settings must stay reachable either way.
    hass.config_entries.async_update_entry(
        entry, options={"per_credential_entities": False}
    )
    await hass.async_block_till_done()
    await a_code(entry.runtime_data, scope)
    await hass.async_block_till_done()

    assert hass.states.get("number.cleaner_max_uses") is None
    assert hass.states.get("number.front_door_lockout_threshold") is not None


# ----------------------------------------------------------------------
# The action behind the same settings
# ----------------------------------------------------------------------


async def test_update_code_touches_only_what_it_was_given(
    hass: HomeAssistant, entry, scope
):
    from custom_components.hyper_passcode.const import DOMAIN
    from custom_components.hyper_passcode.models import Policy

    coordinator = entry.runtime_data
    credential, code = await a_code(
        coordinator,
        scope,
        notes="Weekly",
        policy=Policy(max_uses=4, valid_until=datetime(2020, 1, 1, tzinfo=UTC)),
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_code",
        {"credential_id": credential.credential_id, "uses_per_day": 2},
        blocking=True,
    )

    assert credential.policy.uses_per_day == 2
    assert credential.policy.max_uses == 4
    assert credential.notes == "Weekly"

    # An empty value is how a rule is removed, which is what the entities cannot do
    # and what makes this action the fallback when they are turned off.
    await hass.services.async_call(
        DOMAIN,
        "update_code",
        {"credential_id": credential.credential_id, "valid_until": None},
        blocking=True,
    )

    assert credential.policy.valid_until is None
    assert (await coordinator.async_submit(scope.scope_id, code, Source.KEYPAD)).valid


async def test_update_code_can_set_and_clear_the_grace_window(
    hass: HomeAssistant, entry, scope
):
    from custom_components.hyper_passcode.const import DOMAIN
    from custom_components.hyper_passcode.models import Policy

    coordinator = entry.runtime_data
    credential, _code = await a_code(coordinator, scope, policy=Policy(max_uses=4))
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_code",
        {
            "credential_id": credential.credential_id,
            "grace_period_seconds": 300,
            "grace_mode": "sliding",
        },
        blocking=True,
    )

    assert credential.policy.grace_period_seconds == 300
    assert credential.policy.grace_mode is GraceMode.SLIDING
    assert credential.policy.max_uses == 4

    # Leaving the mode out has to leave it alone, which is why POLICY_FIELDS carries
    # no default for it -- UPDATE_POLICY_FIELDS is derived from the same dict.
    await hass.services.async_call(
        DOMAIN,
        "update_code",
        {"credential_id": credential.credential_id, "uses_per_day": 2},
        blocking=True,
    )
    assert credential.policy.grace_mode is GraceMode.SLIDING

    # A mode has no "unset", so null puts it back to the default.
    await hass.services.async_call(
        DOMAIN,
        "update_code",
        {"credential_id": credential.credential_id, "grace_mode": None},
        blocking=True,
    )
    assert credential.policy.grace_mode is GraceMode.FIXED
    assert credential.policy.grace_period_seconds == 300
