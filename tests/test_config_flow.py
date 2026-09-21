"""The config, options and scope subentry flows.

The subentry flow is what puts an "Add scope" button on the integration page, so
these tests drive it the way the UI does rather than calling the coordinator.
"""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.hyper_passcode.const import (
    DEFAULT_LOCKOUT_THRESHOLD,
    DOMAIN,
    SUBENTRY_TYPE_CREDENTIAL,
    SUBENTRY_TYPE_SCOPE,
    Outcome,
    RejectionReason,
    Source,
)
from custom_components.hyper_passcode.models import Policy
from tests.helpers import set_number, state_of


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
    # The dialog no longer carries these; the scope's number entities do.
    scope_id = await add_scope(hass, entry)
    coordinator = entry.runtime_data
    await set_number(hass, "number.front_door_lockout_threshold", 2)
    await set_number(hass, "number.front_door_lockout_duration", 45)

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
    scope_id = await add_scope(hass, entry)
    coordinator = entry.runtime_data
    await set_number(hass, "number.front_door_lockout_threshold", 9)

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
        result["flow_id"], {"name": "Back Door"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT

    scope = entry.runtime_data.scopes[scope_id]
    assert scope.name == "Back Door"
    # The dialog does not show the lockout settings any more, so it must not wipe
    # the override the number entity wrote either.
    assert entry.runtime_data.lockout_threshold(scope) == 9
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


async def open_options(
    hass: HomeAssistant, entry, step: str
) -> config_entries.ConfigFlowResult:
    """Open the options flow and pick one of its menu entries."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def submit_test_code(
    hass: HomeAssistant, flow_id: str, **fields
) -> config_entries.ConfigFlowResult:
    """Fill in the "Test a code" page once."""
    result = await hass.config_entries.options.async_configure(flow_id, fields)
    await hass.async_block_till_done()
    return result


def verdict(result: config_entries.ConfigFlowResult) -> str:
    """The sentence the test page reports back."""
    placeholders = result["description_placeholders"]
    assert placeholders is not None
    return placeholders["result"]


async def test_options_flow_offers_settings_and_the_test_page(
    hass: HomeAssistant, entry
):
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "test_code"]


async def test_options_flow_saves_settings(hass: HomeAssistant, entry):
    result = await open_options(hass, entry, "settings")
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

    await set_number(hass, "number.cleaner_max_uses", 5)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Weekly cleaner", "scope_ids": [scope_id]},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT

    credential = entry.runtime_data.credentials[credential_id]
    assert credential.label == "Weekly cleaner"
    # Set through the code's own entity, and left alone by an edit that cannot show
    # it any more.
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


# ----------------------------------------------------------------------
# The "Test a code" page
# ----------------------------------------------------------------------
#
# This is the only place a code can be typed into Home Assistant by hand, and it
# sits behind the hub's Configure button rather than on a scope's device page.
# What matters is that the safe mode is genuinely inert, that the real mode is
# genuinely the real path, and that the page never writes options or echoes a code
# back onto the screen.


async def test_the_test_page_needs_a_scope_to_submit_against(
    hass: HomeAssistant, entry
):
    result = await open_options(hass, entry, "test_code")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_scopes"


async def test_a_tested_code_reports_its_verdict_and_changes_nothing(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(
        hass, entry, default_actions=[{"event": "front_door_opened"}]
    )
    credential_id, code = await add_code(hass, entry, scope_ids=[scope_id])
    coordinator = entry.runtime_data
    opened = async_capture_events(hass, "front_door_opened")

    result = await open_options(hass, entry, "test_code")
    result = await submit_test_code(
        hass,
        result["flow_id"],
        scope_id=scope_id,
        code=code,
        source=str(Source.UI),
        dry_run=True,
    )

    # The page loops rather than finishing, so several codes can be tried in a row.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "test_code"
    assert "accepted" in verdict(result)
    assert "Cleaner" in verdict(result)

    assert coordinator.credentials[credential_id].use_count == 0
    assert coordinator.data.audit == []
    assert coordinator.runtime(scope_id).last_used is None
    assert opened == []


async def test_a_tested_code_still_lands_on_the_result_sensor(
    hass: HomeAssistant, entry
):
    # A dry run writes no audit row, so this sensor is the only trace it leaves.
    scope_id = await add_scope(hass, entry)
    await add_code(hass, entry, scope_ids=[scope_id])

    result = await open_options(hass, entry, "test_code")
    await submit_test_code(
        hass,
        result["flow_id"],
        scope_id=scope_id,
        code="000111",
        source=str(Source.UI),
        dry_run=True,
    )

    state = state_of(hass, "sensor.front_door_last_result")
    assert state.state == str(RejectionReason.UNKNOWN_CODE)
    assert state.attributes["dry_run"] is True
    assert state.attributes["source"] == str(Source.UI)


async def test_a_tested_code_does_not_count_towards_the_lockout(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(hass, entry)
    coordinator = entry.runtime_data
    await set_number(hass, "number.front_door_lockout_threshold", 2)

    result = await open_options(hass, entry, "test_code")
    for _ in range(3):
        await submit_test_code(
            hass,
            result["flow_id"],
            scope_id=scope_id,
            code="000111",
            source=str(Source.UI),
            dry_run=True,
        )

    assert coordinator.runtime(scope_id).failed_attempts == 0
    assert not coordinator.is_locked_out(scope_id)


async def test_submitting_for_real_counts_the_use_and_runs_the_actions(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(
        hass, entry, default_actions=[{"event": "front_door_opened"}]
    )
    credential_id, code = await add_code(hass, entry, scope_ids=[scope_id])
    coordinator = entry.runtime_data
    opened = async_capture_events(hass, "front_door_opened")

    result = await open_options(hass, entry, "test_code")
    result = await submit_test_code(
        hass,
        result["flow_id"],
        scope_id=scope_id,
        code=code,
        source=str(Source.UI),
        dry_run=False,
    )

    assert "accepted" in verdict(result)
    assert coordinator.credentials[credential_id].use_count == 1
    assert len(coordinator.data.audit) == 1
    assert len(opened) == 1

    state = state_of(hass, "sensor.front_door_last_result")
    assert state.state == str(Outcome.VALID)
    assert state.attributes["dry_run"] is False


async def test_the_source_field_decides_an_allowed_sources_verdict(
    hass: HomeAssistant, entry
):
    # The whole point of the field: a code pinned to the wall keypad can only be
    # seen working by submitting as the keypad.
    scope_id = await add_scope(hass, entry)
    coordinator = entry.runtime_data
    _credential, code = await coordinator.async_create_credential(
        label="Keypad only",
        scope_ids=[scope_id],
        policy=Policy(allowed_sources=[str(Source.KEYPAD)]),
    )
    await hass.async_block_till_done()

    result = await open_options(hass, entry, "test_code")
    result = await submit_test_code(
        hass,
        result["flow_id"],
        scope_id=scope_id,
        code=code,
        source=str(Source.UI),
        dry_run=True,
    )
    assert str(RejectionReason.WRONG_SOURCE) in verdict(result)

    result = await submit_test_code(
        hass,
        result["flow_id"],
        scope_id=scope_id,
        code=code,
        source=str(Source.KEYPAD),
        dry_run=True,
    )
    assert "accepted" in verdict(result)


async def test_an_empty_code_is_refused_without_submitting_anything(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(hass, entry)
    coordinator = entry.runtime_data

    result = await open_options(hass, entry, "test_code")
    result = await submit_test_code(
        hass,
        result["flow_id"],
        scope_id=scope_id,
        code="   ",
        source=str(Source.UI),
        dry_run=True,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"code": "empty_code"}
    assert coordinator.runtime(scope_id).last_result is None


async def test_the_page_never_echoes_the_code_back(hass: HomeAssistant, entry):
    scope_id = await add_scope(hass, entry)
    _credential_id, code = await add_code(hass, entry, scope_ids=[scope_id])

    result = await open_options(hass, entry, "test_code")
    result = await submit_test_code(
        hass,
        result["flow_id"],
        scope_id=scope_id,
        code=code,
        source=str(Source.UI),
        dry_run=True,
    )

    assert code not in verdict(result)
    # Nor as the field's default, which would put it straight back on screen.
    schema = result["data_schema"]
    assert schema is not None
    code_key = next(key for key in schema.schema if str(key) == "code")
    assert code_key.default is vol.UNDEFINED


async def test_the_page_never_writes_options_or_reloads_the_entry(
    hass: HomeAssistant, entry
):
    scope_id = await add_scope(hass, entry)
    await add_code(hass, entry, scope_ids=[scope_id])
    coordinator = entry.runtime_data
    before = dict(entry.options)

    result = await open_options(hass, entry, "test_code")
    for _ in range(2):
        await submit_test_code(
            hass,
            result["flow_id"],
            scope_id=scope_id,
            code="000111",
            source=str(Source.UI),
            dry_run=True,
        )

    assert dict(entry.options) == before
    # A reload would replace runtime_data, throwing away lockout counters and any
    # half-typed keypad code along with it.
    assert entry.runtime_data is coordinator
