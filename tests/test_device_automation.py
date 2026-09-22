"""Device triggers and conditions.

These are the payoff for the automation editor: a trigger like "Front Door: valid code
entered" with no YAML at all.
"""

from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_get_device_automations

from custom_components.hyper_passcode.const import DOMAIN

ACTION_EVENT = "hyper_passcode_trigger_fired"


async def scope_device_id(hass: HomeAssistant, entry, scope_id: str) -> str:
    """Resolve a scope's device id."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, scope_id), entry.entry_id
    )
    assert device is not None
    return device.id


async def test_scope_devices_offer_triggers(hass: HomeAssistant, entry, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    await hass.async_block_till_done()
    device_id = await scope_device_id(hass, entry, scope.scope_id)

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_id
    )
    # The helper also returns entity triggers from the device's entities.
    types = {t["type"] for t in triggers if t.get("domain") == DOMAIN}

    assert types == {
        "valid_code",
        "invalid_code",
        "expired_code",
        "rate_limited",
        "lockout",
    }


async def test_credential_devices_offer_no_triggers(
    hass: HomeAssistant, entry, coordinator
):
    # Triggers belong to scopes; a credential has no submissions of its own.
    credential, _code = await coordinator.async_create_credential(label="Cleaner")
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"credential_{credential.credential_id}"), entry.entry_id
    )
    assert device is not None

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device.id
    )
    assert [t for t in triggers if t.get("domain") == DOMAIN] == []


async def test_valid_code_trigger_fires(hass: HomeAssistant, entry, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    await hass.async_block_till_done()
    device_id = await scope_device_id(hass, entry, scope.scope_id)
    _credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": "valid_code",
                },
                "action": {
                    "event": ACTION_EVENT,
                    "event_data": {"label": "{{ trigger.event.data.label }}"},
                },
            }
        },
    )
    await hass.async_block_till_done()

    fired = []
    hass.bus.async_listen(ACTION_EVENT, lambda event: fired.append(event))

    await coordinator.async_submit(scope.scope_id, code)
    await hass.async_block_till_done()

    assert len(fired) == 1
    assert fired[0].data["label"] == "Household"


async def test_wrong_code_does_not_fire_the_valid_trigger(
    hass: HomeAssistant, entry, coordinator
):
    scope = await coordinator.async_create_scope(name="Front Door")
    await hass.async_block_till_done()
    device_id = await scope_device_id(hass, entry, scope.scope_id)

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": "valid_code",
                },
                "action": {"event": ACTION_EVENT},
            }
        },
    )
    await hass.async_block_till_done()

    fired = []
    hass.bus.async_listen(ACTION_EVENT, lambda event: fired.append(event))

    await coordinator.async_submit(scope.scope_id, "000111")
    await hass.async_block_till_done()

    assert not fired


async def test_trigger_can_be_narrowed_to_one_credential(
    hass: HomeAssistant, entry, coordinator
):
    scope = await coordinator.async_create_scope(name="Front Door")
    await hass.async_block_till_done()
    device_id = await scope_device_id(hass, entry, scope.scope_id)

    watched, watched_code = await coordinator.async_create_credential(
        label="Cleaner", scope_ids=[scope.scope_id]
    )
    _other, other_code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": "valid_code",
                    "credential_id": watched.credential_id,
                },
                "action": {"event": ACTION_EVENT},
            }
        },
    )
    await hass.async_block_till_done()

    fired = []
    hass.bus.async_listen(ACTION_EVENT, lambda event: fired.append(event))

    await coordinator.async_submit(scope.scope_id, other_code)
    await hass.async_block_till_done()
    assert not fired

    await coordinator.async_submit(scope.scope_id, watched_code)
    await hass.async_block_till_done()
    assert len(fired) == 1


async def test_scope_devices_offer_conditions(hass: HomeAssistant, entry, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    await hass.async_block_till_done()
    device_id = await scope_device_id(hass, entry, scope.scope_id)

    conditions = await async_get_device_automations(
        hass, DeviceAutomationType.CONDITION, device_id
    )
    types = {c["type"] for c in conditions if c.get("domain") == DOMAIN}

    assert types == {"is_locked_out", "credential_valid"}


async def test_lockout_condition_gates_an_automation(
    hass: HomeAssistant, entry, coordinator
):
    # Exercised through a real automation rather than the condition helper directly,
    # since that is the path users actually take.
    scope = await coordinator.async_create_scope(
        name="Gate", lockout_threshold=2, lockout_duration=300
    )
    await hass.async_block_till_done()
    device_id = await scope_device_id(hass, entry, scope.scope_id)

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {"platform": "event", "event_type": "hyper_passcode_probe"},
                "condition": {
                    "condition": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": "is_locked_out",
                },
                "action": {"event": ACTION_EVENT},
            }
        },
    )
    await hass.async_block_till_done()

    fired = []
    hass.bus.async_listen(ACTION_EVENT, lambda event: fired.append(event))

    hass.bus.async_fire("hyper_passcode_probe")
    await hass.async_block_till_done()
    assert not fired

    for _ in range(2):
        await coordinator.async_submit(scope.scope_id, "000111")
    await hass.async_block_till_done()
    assert coordinator.is_locked_out(scope.scope_id)

    hass.bus.async_fire("hyper_passcode_probe")
    await hass.async_block_till_done()
    assert len(fired) == 1


async def test_credential_valid_condition(hass: HomeAssistant, entry, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    await hass.async_block_till_done()
    device_id = await scope_device_id(hass, entry, scope.scope_id)
    credential, _code = await coordinator.async_create_credential(
        label="Guest", scope_ids=[scope.scope_id]
    )

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {"platform": "event", "event_type": "hyper_passcode_probe"},
                "condition": {
                    "condition": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": "credential_valid",
                    "credential_id": credential.credential_id,
                },
                "action": {"event": ACTION_EVENT},
            }
        },
    )
    await hass.async_block_till_done()

    fired = []
    hass.bus.async_listen(ACTION_EVENT, lambda event: fired.append(event))

    hass.bus.async_fire("hyper_passcode_probe")
    await hass.async_block_till_done()
    assert len(fired) == 1

    await coordinator.async_revoke(credential.credential_id)
    hass.bus.async_fire("hyper_passcode_probe")
    await hass.async_block_till_done()
    assert len(fired) == 1
