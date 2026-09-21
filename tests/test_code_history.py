"""Deleting a discarded code's recorded history.

``sensor.<code>_code`` carries the code as its state, so the recorder keeps every
value it ever had. Discarding the stored copy therefore has to reach into the
recorder too, or "the code is gone" would be true of the store and false of the
database for another ``purge_keep_days``.

These drive a fake recorder: what matters is that the right entity is purged at the
right moment, and that a purge that does not happen is reported rather than assumed.
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hyper_passcode.const import DOMAIN
from custom_components.hyper_passcode.coordinator import (
    ISSUE_CODE_HISTORY,
    RECORDER_DOMAIN,
    SERVICE_PURGE_ENTITIES,
)

CODE_ENTITY = "sensor.cleaner_code"
ISSUE_ID = ISSUE_CODE_HISTORY.format(CODE_ENTITY)


@pytest.fixture
def purges(hass: HomeAssistant):
    """Pretend a recorder is loaded, logging what it is asked to purge."""
    hass.config.components.add(RECORDER_DOMAIN)
    return async_mock_service(hass, RECORDER_DOMAIN, SERVICE_PURGE_ENTITIES)


@pytest.fixture
def failing_purges(hass: HomeAssistant):
    """A recorder whose purge refuses, so the failure path can be exercised."""
    hass.config.components.add(RECORDER_DOMAIN)
    return async_mock_service(
        hass,
        RECORDER_DOMAIN,
        SERVICE_PURGE_ENTITIES,
        raise_exception=HomeAssistantError("the database is locked"),
    )


async def a_code(hass: HomeAssistant, coordinator, scope, *, keep_viewable=True):
    """Create a credential named Cleaner and let its entities register."""
    credential, code = await coordinator.async_create_credential(
        label="Cleaner", scope_ids=[scope.scope_id], keep_viewable=keep_viewable
    )
    await hass.async_block_till_done()
    return credential, code


def issue(hass: HomeAssistant):
    """The repair raised for the code sensor, if there is one."""
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)


async def test_discarding_a_viewable_code_purges_its_recorded_history(
    hass: HomeAssistant, coordinator, scope, purges
):
    credential, _code = await a_code(hass, coordinator, scope)

    await coordinator.async_update_credential(
        credential.credential_id, {"keep_viewable": False}
    )
    await hass.async_block_till_done()

    assert len(purges) == 1
    assert purges[0].data["entity_id"] == [CODE_ENTITY]
    # Nothing kept: every row of that entity's history is a copy of the code.
    assert purges[0].data["keep_days"] == 0
    assert issue(hass) is None


async def test_the_purge_happens_after_the_sensor_has_dropped_the_code(
    hass: HomeAssistant, coordinator, scope, purges
):
    # Otherwise the sensor would write the code once more on its way out and leave
    # the newest row in the history holding it.
    credential, _code = await a_code(hass, coordinator, scope)
    seen: list[str] = []

    async def record_state(call):
        seen.append(hass.states.get(CODE_ENTITY).state)

    hass.services.async_remove(RECORDER_DOMAIN, SERVICE_PURGE_ENTITIES)
    hass.services.async_register(RECORDER_DOMAIN, SERVICE_PURGE_ENTITIES, record_state)

    await coordinator.async_update_credential(
        credential.credential_id, {"keep_viewable": False}
    )
    await hass.async_block_till_done()

    assert seen == ["unknown"]


async def test_deleting_a_code_purges_its_recorded_history(
    hass: HomeAssistant, coordinator, scope, purges
):
    credential, _code = await a_code(hass, coordinator, scope)

    await coordinator.async_delete_credential(credential.credential_id)
    await hass.async_block_till_done()

    assert len(purges) == 1
    assert purges[0].data["entity_id"] == [CODE_ENTITY]


async def test_a_code_that_was_never_viewable_is_still_purged_on_delete(
    hass: HomeAssistant, coordinator, scope, purges
):
    # It may have been viewable earlier in its life, and that history is the code.
    credential, _code = await a_code(hass, coordinator, scope, keep_viewable=False)

    await coordinator.async_delete_credential(credential.credential_id)
    await hass.async_block_till_done()

    assert len(purges) == 1


async def test_an_edit_that_discards_nothing_purges_nothing(
    hass: HomeAssistant, coordinator, scope, purges
):
    credential, _code = await a_code(hass, coordinator, scope, keep_viewable=False)

    await coordinator.async_update_credential(
        credential.credential_id, {"keep_viewable": False, "notes": "unchanged"}
    )
    await hass.async_block_till_done()

    assert purges == []


async def test_a_failed_purge_is_reported_as_a_repair(
    hass: HomeAssistant, coordinator, scope, failing_purges
):
    credential, _code = await a_code(hass, coordinator, scope)

    await coordinator.async_update_credential(
        credential.credential_id, {"keep_viewable": False}
    )
    await hass.async_block_till_done()

    raised = issue(hass)
    assert raised is not None
    assert raised.severity == ir.IssueSeverity.WARNING
    assert raised.translation_key == "code_history_not_purged"
    # Named, so the repair says which code and which entity to purge by hand.
    assert raised.translation_placeholders == {
        "label": "Cleaner",
        "entity_id": CODE_ENTITY,
    }


async def test_a_purge_that_works_later_clears_the_repair(
    hass: HomeAssistant, coordinator, scope, failing_purges
):
    credential, _code = await a_code(hass, coordinator, scope)
    await coordinator.async_update_credential(
        credential.credential_id, {"keep_viewable": False}
    )
    await hass.async_block_till_done()
    assert issue(hass) is not None

    # The same entity purged successfully on a later attempt -- here, deleting the
    # code outright -- means the warning no longer describes anything true.
    async_mock_service(hass, RECORDER_DOMAIN, SERVICE_PURGE_ENTITIES)
    await coordinator.async_delete_credential(credential.credential_id)
    await hass.async_block_till_done()

    assert issue(hass) is None


async def test_without_a_recorder_nothing_is_purged_or_reported(
    hass: HomeAssistant, coordinator, scope
):
    # No recorder means nothing ever wrote the code to a database, so there is
    # neither anything to purge nor anything to warn about.
    calls = async_mock_service(hass, RECORDER_DOMAIN, SERVICE_PURGE_ENTITIES)
    credential, _code = await a_code(hass, coordinator, scope)

    await coordinator.async_update_credential(
        credential.credential_id, {"keep_viewable": False}
    )
    await hass.async_block_till_done()

    assert calls == []
    assert issue(hass) is None


async def test_no_purge_is_attempted_without_per_credential_entities(
    hass: HomeAssistant, entry, coordinator, scope, purges
):
    # With the entities off there is no code sensor, so nothing recorded it.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "per_credential_entities": False}
    )
    await hass.async_block_till_done()
    credential, _code = await a_code(hass, coordinator, scope)

    await coordinator.async_delete_credential(credential.credential_id)
    await hass.async_block_till_done()

    assert purges == []
