"""The config, options and scope subentry flows.

The subentry flow is what puts an "Add scope" button on the integration page, so
these tests drive it the way the UI does rather than calling the coordinator.
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.hyper_passcode.const import (
    DEFAULT_LOCKOUT_THRESHOLD,
    DOMAIN,
    SUBENTRY_TYPE_SCOPE,
    Source,
)


async def add_scope(hass: HomeAssistant, entry, **fields) -> str:
    """Add a scope through the subentry flow, returning its id."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SCOPE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Front Door", **fields}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    scope_id = next(
        sid
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_TYPE_SCOPE
    )
    return scope_id


async def test_user_flow_creates_the_hub(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "HyperPasscode"


async def test_only_one_hub_can_be_added(hass: HomeAssistant, entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_the_integration_offers_a_scope_subentry(hass: HomeAssistant, entry):
    # This is what renders as an "Add scope" button on the integration page.
    handler = config_entries.HANDLERS[DOMAIN]
    assert SUBENTRY_TYPE_SCOPE in handler.async_get_supported_subentry_types(entry)


async def test_adding_a_scope_creates_its_entities(hass: HomeAssistant, entry):
    scope_id = await add_scope(hass, entry)
    coordinator = entry.runtime_data

    assert coordinator.scopes[scope_id].name == "Front Door"

    registry = er.async_get(hass)
    for platform, suffix in (
        ("event", "code"),
        ("sensor", "last_used"),
        ("binary_sensor", "lockout"),
        ("button", "generate_delivery_code"),
    ):
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{scope_id}_{suffix}"
        )
        assert entity_id, f"{platform}.{suffix} was not created"
        # Entities belong to the subentry, so removing the scope removes them too.
        assert registry.async_get(entity_id).config_subentry_id == scope_id


async def test_scope_lockout_is_configured_per_scope(hass: HomeAssistant, entry):
    scope_id = await add_scope(
        hass, entry, lockout_threshold=2, lockout_duration=45
    )
    coordinator = entry.runtime_data
    scope = coordinator.scopes[scope_id]

    assert coordinator.lockout_threshold(scope) == 2
    assert coordinator.lockout_duration(scope) == 45

    # And it is the threshold that actually governs the lockout.
    for _ in range(2):
        await coordinator.async_submit(scope_id, "000111", Source.KEYPAD)
    await hass.async_block_till_done()
    assert coordinator.is_locked_out(scope_id)


async def test_omitting_lockout_falls_back_to_the_integration_setting(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(hass, entry)
    coordinator = entry.runtime_data
    scope = coordinator.scopes[scope_id]

    assert scope.lockout_threshold is None
    assert coordinator.lockout_threshold(scope) == DEFAULT_LOCKOUT_THRESHOLD


async def test_reconfiguring_a_scope_keeps_its_runtime_state(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(hass, entry, lockout_threshold=9)
    coordinator = entry.runtime_data

    # Something worth preserving across an edit.
    await coordinator.async_submit(scope_id, "000111", Source.KEYPAD)
    assert coordinator.runtime(scope_id).failed_attempts == 1

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SCOPE),
        context={
            "source": "reconfigure",
            "subentry_id": scope_id,
        },
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Back Door", "lockout_threshold": 3}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT

    scope = entry.runtime_data.scopes[scope_id]
    assert scope.name == "Back Door"
    assert entry.runtime_data.lockout_threshold(scope) == 3
    # Editing a scope must not reset counters or half-typed codes.
    assert entry.runtime_data.runtime(scope_id).failed_attempts == 1


async def test_deleting_a_scope_removes_its_entities(hass: HomeAssistant, entry):
    scope_id = await add_scope(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("event", DOMAIN, f"{scope_id}_code")

    assert hass.config_entries.async_remove_subentry(entry, scope_id)
    await hass.async_block_till_done()

    assert registry.async_get_entity_id("event", DOMAIN, f"{scope_id}_code") is None
    assert scope_id not in entry.runtime_data.scopes


async def test_options_flow_saves_settings(hass: HomeAssistant, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "reject_weak_codes": False,
            "per_credential_entities": True,
            "default_code_length": 8,
            "lockout_threshold": 7,
            "lockout_duration": 120,
            "audit_log_size": 50,
            "log_failed_plaintext": False,
            "weak_code_blocklist": ["1979"],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator = entry.runtime_data
    assert coordinator.reject_weak_codes is False
    assert coordinator.default_code_length == 8
    assert coordinator.audit_log_size == 50
    assert coordinator.weak_code_blocklist == ["1979"]
