"""The plan's acceptance walkthrough, driven entirely through service actions.

Everything else tests the coordinator directly; this exercises the layer a user
actually touches -- schemas, defaults, response data -- against a real target entity.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.hyper_passcode.const import DOMAIN, EventType, RejectionReason

TARGET = "input_boolean.door_relay"


async def call(hass: HomeAssistant, service: str, data: dict, *, response=True):
    """Invoke one of this integration's actions."""
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=response
    )


async def test_delivery_code_walkthrough(hass: HomeAssistant, entry):
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"door_relay": None}}
    )

    # 1. A scope whose default action drives a real entity.
    scope = await call(
        hass,
        "create_scope",
        {
            "name": "Front Door",
            "default_actions": [
                {"action": "input_boolean.turn_on", "target": {"entity_id": TARGET}}
            ],
        },
    )
    scope_id = scope["scope_id"]
    await hass.async_block_till_done()

    # 2. A one-time code valid for the next two hours.
    otp = await call(
        hass,
        "create_otp",
        {"scope_id": scope_id, "label": "Grocery delivery", "duration": "02:00:00"},
    )
    code = otp["code"]
    assert code
    assert otp["valid_until"] is not None

    registry = er.async_get(hass)
    event_entity = registry.async_get_entity_id("event", DOMAIN, f"{scope_id}_code")
    last_used = registry.async_get_entity_id("sensor", DOMAIN, f"{scope_id}_last_used")
    uses = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{otp['credential_id']}_uses"
    )
    assert hass.states.get(last_used).state == "unknown"
    assert hass.states.get(uses).state == "0"

    # 3. Submitting it opens the door and records the use.
    result = await call(hass, "submit", {"scope_id": scope_id, "code": code})
    await hass.async_block_till_done()

    assert result["valid"] is True
    assert result["label"] == "Grocery delivery"
    assert hass.states.get(TARGET).state == "on"
    assert hass.states.get(event_entity).attributes["event_type"] == str(
        EventType.VALID
    )
    assert hass.states.get(last_used).state != "unknown"
    assert hass.states.get(last_used).attributes["label"] == "Grocery delivery"
    assert hass.states.get(uses).state == "1"

    # 4. A second attempt is refused, and the door stays as it was.
    await hass.services.async_call(
        "input_boolean", "turn_off", {"entity_id": TARGET}, blocking=True
    )
    again = await call(hass, "submit", {"scope_id": scope_id, "code": code})
    await hass.async_block_till_done()

    assert again["valid"] is False
    assert again["reason"] == RejectionReason.MAX_USES_REACHED
    assert hass.states.get(TARGET).state == "off"
    assert hass.states.get(uses).state == "1"


async def test_test_code_reports_without_acting(hass: HomeAssistant, entry):
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"door_relay": None}}
    )
    scope = await call(
        hass,
        "create_scope",
        {
            "name": "Gate",
            "default_actions": [
                {"action": "input_boolean.turn_on", "target": {"entity_id": TARGET}}
            ],
        },
    )
    scope_id = scope["scope_id"]
    created = await call(
        hass,
        "create_code",
        {"label": "Cleaner", "scope_ids": [scope_id], "max_uses": 1},
    )

    verdict = await call(
        hass, "test_code", {"scope_id": scope_id, "code": created["code"]}
    )

    assert verdict["valid"] is True
    assert hass.states.get(TARGET).state == "off"

    # Still usable afterwards, because the dry run counted for nothing.
    used = await call(hass, "submit", {"scope_id": scope_id, "code": created["code"]})
    assert used["valid"] is True


async def test_lockout_through_services(hass: HomeAssistant, entry):
    scope = await call(
        hass,
        "create_scope",
        {"name": "Gate", "lockout_threshold": 3, "lockout_duration": 300},
    )
    scope_id = scope["scope_id"]
    await hass.async_block_till_done()

    lockout = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{scope_id}_lockout"
    )

    for _ in range(3):
        await call(hass, "submit", {"scope_id": scope_id, "code": "000111"})
    await hass.async_block_till_done()

    assert hass.states.get(lockout).state == "on"
    refused = await call(hass, "submit", {"scope_id": scope_id, "code": "000111"})
    assert refused["reason"] == RejectionReason.LOCKED_OUT


async def test_audit_export_round_trip(hass: HomeAssistant, entry):
    scope = await call(hass, "create_scope", {"name": "Front Door"})
    scope_id = scope["scope_id"]
    created = await call(
        hass, "create_code", {"label": "Household", "scope_ids": [scope_id]}
    )

    await call(hass, "submit", {"scope_id": scope_id, "code": created["code"]})
    await call(hass, "submit", {"scope_id": scope_id, "code": "000111"})

    as_json = await call(hass, "export_audit", {"format": "json"})
    assert len(as_json["entries"]) == 2
    assert as_json["entries"][0]["outcome"] == "invalid"

    as_csv = await call(hass, "export_audit", {"format": "csv"})
    assert as_csv["content"].startswith("timestamp,scope_id,outcome")
    assert "Household" in as_csv["content"]


async def test_scope_deletion_through_services(hass: HomeAssistant, entry):
    scope = await call(hass, "create_scope", {"name": "Temporary"})
    scope_id = scope["scope_id"]
    await hass.async_block_till_done()

    await call(hass, "delete_scope", {"scope_id": scope_id}, response=False)
    await hass.async_block_till_done()

    assert scope_id not in entry.runtime_data.scopes
