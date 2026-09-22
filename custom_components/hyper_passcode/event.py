"""Event entity: the automation surface for code submissions.

An ``event`` entity rather than a button, because a button is something you press --
it cannot represent "a valid code was entered". Event types distinguish an ordinary
wrong code from a real code that has expired or been rate limited, so automations can
respond differently to each.
"""

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HyperPasscodeConfigEntry
from .const import (
    ATTR_CREDENTIAL_ID,
    ATTR_IN_GRACE_PERIOD,
    ATTR_LABEL,
    ATTR_PERSON,
    ATTR_REASON,
    ATTR_SOURCE,
    EventType,
)
from .coordinator import SIGNAL_SUBMISSION, HyperPasscodeCoordinator, SubmissionResult
from .entity import HyperPasscodeScopeEntity, async_add_scope_entities
from .models import Scope


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one event entity per scope."""
    async_add_scope_entities(
        hass, entry, entry.runtime_data, async_add_entities, [ScopeCodeEventEntity]
    )


class ScopeCodeEventEntity(EventEntity, HyperPasscodeScopeEntity):
    """Fires whenever a code is submitted against this scope."""

    _attr_translation_key = "code"
    _attr_icon = "mdi:dialpad"

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_code"
        self._attr_event_types = [str(e) for e in EventType]

    async def async_added_to_hass(self) -> None:
        """Listen for submissions on this scope."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SUBMISSION.format(self.scope_id),
                self._handle_submission,
            )
        )

    @callback
    def _handle_submission(self, result: SubmissionResult) -> None:
        """Turn a submission result into an event."""
        self._trigger_event(
            str(result.event_type),
            {
                ATTR_CREDENTIAL_ID: result.credential_id,
                ATTR_LABEL: result.label,
                ATTR_PERSON: result.person,
                ATTR_SOURCE: result.source,
                ATTR_REASON: str(result.reason) if result.reason else None,
                ATTR_IN_GRACE_PERIOD: result.accepted_in_grace,
            },
        )
        self.async_write_ha_state()
