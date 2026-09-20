"""Keystroke buffering.

Physical keypads emit one event per key, so this is the layer that turns a stream of
keystrokes into a submission. Getting it wrong makes every keypad flaky.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hyper_passcode.const import RejectionReason, Source


async def type_keys(coordinator, scope_id: str, keys: str):
    """Feed each character in turn, returning the last result."""
    result = None
    for key in keys:
        result = await coordinator.async_submit_key(scope_id, key)
    return result


async def test_terminator_key_submits_the_buffer(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(name="Keypad", terminator_keys=["#"])
    _credential, code = await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    assert await type_keys(coordinator, scope.scope_id, code) is None
    result = await coordinator.async_submit_key(scope.scope_id, "#")

    assert result is not None
    assert result.valid is True
    assert result.source == Source.KEYPAD


async def test_fixed_length_submits_without_a_terminator(
    hass: HomeAssistant, coordinator
):
    scope = await coordinator.async_create_scope(name="Keypad", code_length=6)
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    result = await type_keys(coordinator, scope.scope_id, "495162")

    assert result is not None
    assert result.valid is True


async def test_the_buffer_clears_after_the_inter_key_timeout(
    hass: HomeAssistant, coordinator
):
    scope = await coordinator.async_create_scope(
        name="Keypad", terminator_keys=["#"], inter_key_timeout=10
    )
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    await type_keys(coordinator, scope.scope_id, "4951")
    assert coordinator.runtime(scope.scope_id).buffer == "4951"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert coordinator.runtime(scope.scope_id).buffer == ""

    # Finishing the old code now submits only the remaining digits.
    result = await type_keys(coordinator, scope.scope_id, "62")
    assert result is None
    result = await coordinator.async_submit_key(scope.scope_id, "#")
    assert result is not None
    assert result.reason is RejectionReason.UNKNOWN_CODE


async def test_each_keystroke_restarts_the_timeout(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(
        name="Keypad", terminator_keys=["#"], inter_key_timeout=10
    )
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[scope.scope_id]
    )

    for key in "495162":
        await coordinator.async_submit_key(scope.scope_id, key)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()

    assert coordinator.runtime(scope.scope_id).buffer == "495162"
    result = await coordinator.async_submit_key(scope.scope_id, "#")
    assert result is not None
    assert result.valid is True


async def test_clear_buffer_discards_a_half_typed_code(
    hass: HomeAssistant, coordinator
):
    scope = await coordinator.async_create_scope(name="Keypad", terminator_keys=["#"])

    await type_keys(coordinator, scope.scope_id, "4951")
    coordinator.async_clear_buffer(scope.scope_id)

    assert coordinator.runtime(scope.scope_id).buffer == ""


async def test_a_bare_terminator_submits_nothing(hass: HomeAssistant, coordinator):
    scope = await coordinator.async_create_scope(name="Keypad", terminator_keys=["#"])

    assert await coordinator.async_submit_key(scope.scope_id, "#") is None
    assert not coordinator.data.audit


async def test_buffers_are_independent_per_scope(hass: HomeAssistant, coordinator):
    front = await coordinator.async_create_scope(name="Front", terminator_keys=["#"])
    gate = await coordinator.async_create_scope(name="Gate", terminator_keys=["#"])
    await coordinator.async_create_credential(
        label="Household", code="495162", scope_ids=[front.scope_id, gate.scope_id]
    )

    await type_keys(coordinator, front.scope_id, "4951")
    await type_keys(coordinator, gate.scope_id, "495162")

    assert coordinator.runtime(front.scope_id).buffer == "4951"
    result = await coordinator.async_submit_key(gate.scope_id, "#")
    assert result is not None
    assert result.valid is True
    assert coordinator.runtime(front.scope_id).buffer == "4951"
