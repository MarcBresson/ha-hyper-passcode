"""The config, options and scope subentry flows.

The subentry flow is what puts an "Add scope" button on the integration page, so
these tests drive it the way the UI does rather than calling the coordinator.
"""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.hyper_passcode.const import (
    DEFAULT_LOCKOUT_THRESHOLD,
    DOMAIN,
    SUBENTRY_TYPE_CREDENTIAL,
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
        registered = registry.async_get(entity_id)
        assert registered is not None
        # Entities belong to the subentry, so removing the scope removes them too.
        assert registered.config_subentry_id == scope_id


async def test_scope_lockout_is_configured_per_scope(hass: HomeAssistant, entry):
    scope_id = await add_scope(hass, entry, lockout_threshold=2, lockout_duration=45)
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


async def add_code(hass: HomeAssistant, entry, **fields) -> tuple[str, str]:
    """Add a code through the subentry flow, returning its id and the code shown."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CREDENTIAL),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Cleaner", **fields}
    )
    # The generated code is shown once before anything is committed.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "created"
    placeholders = result["description_placeholders"]
    assert placeholders is not None
    code = placeholders["code"]

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    credential_id = next(
        sub.data["credential_id"]
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CREDENTIAL
    )
    return credential_id, code


async def test_the_integration_offers_a_credential_subentry(hass: HomeAssistant, entry):
    handler = config_entries.HANDLERS[DOMAIN]
    assert SUBENTRY_TYPE_CREDENTIAL in handler.async_get_supported_subentry_types(entry)


async def test_adding_a_code_shows_it_once_and_makes_it_work(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(hass, entry)
    credential_id, code = await add_code(hass, entry, scope_ids=[scope_id])

    coordinator = entry.runtime_data
    assert coordinator.credentials[credential_id].label == "Cleaner"
    assert code

    result = await coordinator.async_submit(scope_id, code, Source.KEYPAD)
    assert result.valid is True
    assert result.label == "Cleaner"


async def test_an_added_code_creates_its_entities(hass: HomeAssistant, entry):
    credential_id, _code = await add_code(hass, entry)
    # A code added through the dialog gets a subentry id Home Assistant chose, which
    # is not its credential id.
    subentry_id = entry.runtime_data.async_credential_subentry_id(credential_id)
    assert subentry_id

    registry = er.async_get(hass)
    for platform, suffix in (("sensor", "uses"), ("switch", "enabled")):
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{credential_id}_{suffix}"
        )
        assert entity_id, f"{platform}.{suffix} was not created"
        registered = registry.async_get(entity_id)
        assert registered is not None
        assert registered.config_subentry_id == subentry_id


async def test_the_code_never_reaches_the_config_entry(hass: HomeAssistant, entry):
    # The whole point of the split: subentries are not written with restricted
    # permissions, so no code may appear in one.
    _credential_id, code = await add_code(hass, entry, keep_viewable=True)

    subentry = next(
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_CREDENTIAL
    )
    assert code not in str(dict(subentry.data))
    assert "plaintext" not in subentry.data
    assert "lookup_index" not in subentry.data


async def test_a_chosen_code_is_used_as_given(hass: HomeAssistant, entry):
    scope_id = await add_scope(hass, entry)
    _credential_id, code = await add_code(
        hass, entry, code="495162", scope_ids=[scope_id]
    )

    assert code == "495162"
    assert (
        await entry.runtime_data.async_submit(scope_id, "495162", Source.KEYPAD)
    ).valid is True


async def test_a_weak_code_is_reported_on_the_form(hass: HomeAssistant, entry):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CREDENTIAL),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Weak", "code": "123456"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "weak_code"}


async def test_a_colliding_code_is_reported_on_the_form(hass: HomeAssistant, entry):
    await add_code(hass, entry, code="495162")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CREDENTIAL),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Duplicate", "code": "495162"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "code_collision"}


async def test_abandoning_the_add_dialog_leaves_nothing_behind(
    hass: HomeAssistant, entry
):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CREDENTIAL),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Abandoned"}
    )
    assert result["step_id"] == "created"

    hass.config_entries.subentries.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert entry.runtime_data.credentials == {}


async def test_editing_a_code_keeps_its_use_count(hass: HomeAssistant, entry):
    scope_id = await add_scope(hass, entry)
    credential_id, code = await add_code(hass, entry, scope_ids=[scope_id])
    coordinator = entry.runtime_data

    await coordinator.async_submit(scope_id, code, Source.KEYPAD)
    assert coordinator.credentials[credential_id].use_count == 1

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CREDENTIAL),
        context={
            "source": "reconfigure",
            "subentry_id": coordinator.async_credential_subentry_id(credential_id),
        },
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Weekly cleaner", "scope_ids": [scope_id], "max_uses": 5},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT

    credential = entry.runtime_data.credentials[credential_id]
    assert credential.label == "Weekly cleaner"
    assert credential.policy.max_uses == 5
    # Editing configuration must not reset history.
    assert credential.use_count == 1
    # And the original code still works.
    assert (
        await entry.runtime_data.async_submit(scope_id, code, Source.KEYPAD)
    ).valid is True


async def test_editing_can_replace_the_code(hass: HomeAssistant, entry):
    scope_id = await add_scope(hass, entry)
    credential_id, old_code = await add_code(hass, entry, scope_ids=[scope_id])

    subentry_id = entry.runtime_data.async_credential_subentry_id(credential_id)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CREDENTIAL),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Cleaner", "scope_ids": [scope_id], "code": "857314"},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT

    coordinator = entry.runtime_data
    assert (await coordinator.async_submit(scope_id, "857314", Source.KEYPAD)).valid
    assert not (await coordinator.async_submit(scope_id, old_code, Source.KEYPAD)).valid


async def test_deleting_a_code_removes_its_entities_and_secret(
    hass: HomeAssistant, entry
):
    credential_id, _code = await add_code(hass, entry)
    subentry_id = entry.runtime_data.async_credential_subentry_id(credential_id)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{credential_id}_uses")

    assert hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert credential_id not in coordinator.credentials
    assert credential_id not in coordinator.data.secrets
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{credential_id}_uses") is None
    )
