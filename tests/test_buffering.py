"""Keystroke buffering.

Physical keypads emit one event per key, so this is the layer that turns a stream of
keystrokes into a submission. Getting it wrong makes every keypad flaky.
"""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hyper_passcode.const import RejectionReason, Source


async def type_keys(coordinator, keypad_id: str, keys: str):
    """Feed each character in turn, returning the last result."""
    result = None
    for key in keys:
        result = await coordinator.async_submit_key(keypad_id, key)
    return result


async def test_terminator_key_submits_the_buffer(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    keypad = await coordinator.async_create_keypad(
        name="Keypad", scope_id=scope.scope_id, terminator_keys=["#"]
    )
    _credential, code = await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    assert await type_keys(coordinator, keypad.keypad_id, code) is None
    result = await coordinator.async_submit_key(keypad.keypad_id, "#")

    assert result is not None
    assert result.valid is True
    assert result.source == Source.KEYPAD


async def test_fixed_length_submits_without_a_terminator(
    hass: HomeAssistant, coordinator
):
    scope = await coordinator.async_create_scope(name="Front Door")
    keypad = await coordinator.async_create_keypad(
        name="Keypad", scope_id=scope.scope_id, code_length=6
    )
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    result = await type_keys(coordinator, keypad.keypad_id, "495162")

    assert result is not None
    assert result.valid is True


async def test_the_buffer_clears_after_the_inter_key_timeout(
    hass: HomeAssistant, coordinator
):
    scope = await coordinator.async_create_scope(name="Front Door")
    keypad = await coordinator.async_create_keypad(
        name="Keypad",
        scope_id=scope.scope_id,
        terminator_keys=["#"],
        inter_key_timeout=10,
    )
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    await type_keys(coordinator, keypad.keypad_id, "4951")
    assert coordinator.keypad_runtime(keypad.keypad_id).buffer == "4951"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert coordinator.keypad_runtime(keypad.keypad_id).buffer == ""

    # Finishing the old code now submits only the remaining digits.
    result = await type_keys(coordinator, keypad.keypad_id, "62")
    assert result is None
    result = await coordinator.async_submit_key(keypad.keypad_id, "#")
    assert result is not None
    assert result.reason is RejectionReason.UNKNOWN_CODE


async def test_each_keystroke_restarts_the_timeout(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    keypad = await coordinator.async_create_keypad(
        name="Keypad",
        scope_id=scope.scope_id,
        terminator_keys=["#"],
        inter_key_timeout=10,
    )
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    for key in "495162":
        await coordinator.async_submit_key(keypad.keypad_id, key)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()

    assert coordinator.keypad_runtime(keypad.keypad_id).buffer == "495162"
    result = await coordinator.async_submit_key(keypad.keypad_id, "#")
    assert result is not None
    assert result.valid is True


async def test_clear_buffer_discards_a_half_typed_code(
    hass: HomeAssistant, coordinator
):
    scope = await coordinator.async_create_scope(name="Front Door")
    keypad = await coordinator.async_create_keypad(
        name="Keypad", scope_id=scope.scope_id, terminator_keys=["#"]
    )

    await type_keys(coordinator, keypad.keypad_id, "4951")
    coordinator.async_clear_buffer(keypad.keypad_id)

    assert coordinator.keypad_runtime(keypad.keypad_id).buffer == ""


async def test_a_bare_terminator_submits_nothing(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    keypad = await coordinator.async_create_keypad(
        name="Keypad", scope_id=scope.scope_id, terminator_keys=["#"]
    )

    assert await coordinator.async_submit_key(keypad.keypad_id, "#") is None
    assert not coordinator.data.audit


async def test_buffers_are_independent_per_keypad(hass: HomeAssistant, coordinator):
    front = await coordinator.async_create_scope(name="Front")
    gate = await coordinator.async_create_scope(name="Gate")
    front_keypad = await coordinator.async_create_keypad(
        name="Front keypad", scope_id=front.scope_id, terminator_keys=["#"]
    )
    gate_keypad = await coordinator.async_create_keypad(
        name="Gate keypad", scope_id=gate.scope_id, terminator_keys=["#"]
    )
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[front.scope_id, gate.scope_id]
    )

    await type_keys(coordinator, front_keypad.keypad_id, "4951")
    await type_keys(coordinator, gate_keypad.keypad_id, "495162")

    assert coordinator.keypad_runtime(front_keypad.keypad_id).buffer == "4951"
    result = await coordinator.async_submit_key(gate_keypad.keypad_id, "#")
    assert result is not None
    assert result.valid is True
    assert coordinator.keypad_runtime(front_keypad.keypad_id).buffer == "4951"


async def test_two_keypads_can_target_the_same_scope(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    front_panel = await coordinator.async_create_keypad(
        name="Front panel", scope_id=scope.scope_id, terminator_keys=["#"]
    )
    rear_panel = await coordinator.async_create_keypad(
        name="Rear panel", scope_id=scope.scope_id, terminator_keys=["#"]
    )
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    result = await type_keys(coordinator, rear_panel.keypad_id, "495162#")
    assert result is not None
    assert result.valid is True
    assert result.scope_id == scope.scope_id
    assert coordinator.keypad_runtime(front_panel.keypad_id).buffer == ""
