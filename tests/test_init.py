"""Setup, entity creation and scope lifecycle."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.hyper_passcode.const import DOMAIN


async def test_entry_sets_up(entry):
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None


async def test_services_are_registered(hass: HomeAssistant, entry):
    for service in (
        "submit",
        "submit_key",
        "clear_buffer",
        "test_code",
        "create_code",
        "create_otp",
        "revoke",
        "delete_code",
        "set_enabled",
        "revoke_all",
        "export_audit",
        "create_scope",
        "update_scope",
        "delete_scope",
    ):
        assert hass.services.has_service(DOMAIN, service), service


async def test_creating_a_scope_creates_its_entities(
    hass: HomeAssistant, entry, coordinator
):
    scope = await coordinator.async_create_scope(name="Front Door")
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    unique_ids = {
        entity.unique_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
    }
    assert f"{scope.scope_id}_code" in unique_ids
    assert f"{scope.scope_id}_last_used" in unique_ids
    assert f"{scope.scope_id}_failed_attempts" in unique_ids
    assert f"{scope.scope_id}_lockout" in unique_ids
    assert f"{scope.scope_id}_generate_delivery_code" in unique_ids

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, scope.scope_id), entry.entry_id
    )
    assert device is not None
    assert device.name == "Front Door"


async def test_deleting_a_scope_removes_its_device(
    hass: HomeAssistant, entry, coordinator
):
    scope = await coordinator.async_create_scope(name="Gate")
    await hass.async_block_till_done()

    await coordinator.async_delete_scope(scope.scope_id)
    await hass.async_block_till_done()

    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, scope.scope_id), entry.entry_id
        )
        is None
    )


async def test_creating_a_credential_creates_its_entities(
    hass: HomeAssistant, coordinator, scope
):
    credential, _code = await coordinator.async_create_credential(
        label="Cleaner", scope_ids=[scope.scope_id]
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    unique_ids = {
        entity.unique_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
    }
    assert f"{credential.credential_id}_uses" in unique_ids
    assert f"{credential.credential_id}_enabled" in unique_ids


async def test_deleting_a_credential_removes_its_entities(
    hass: HomeAssistant, coordinator, scope
):
    credential, _code = await coordinator.async_create_credential(
        label="Temp", scope_ids=[scope.scope_id]
    )
    await hass.async_block_till_done()

    await coordinator.async_delete_credential(credential.credential_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    remaining = {
        entity.unique_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
    }
    assert f"{credential.credential_id}_uses" not in remaining
    assert f"{credential.credential_id}_enabled" not in remaining
