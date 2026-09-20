"""Binary sensor: brute-force lockout state per scope."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time

from . import HyperPasscodeConfigEntry
from .coordinator import SIGNAL_SCOPE_UPDATED, HyperPasscodeCoordinator
from .entity import HyperPasscodeScopeEntity, async_add_scope_entities
from .models import Scope


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one lockout sensor per scope."""
    async_add_scope_entities(
        hass, entry, entry.runtime_data, async_add_entities, [ScopeLockoutBinarySensor]
    )


class ScopeLockoutBinarySensor(BinarySensorEntity, HyperPasscodeScopeEntity):
    """On while a scope is refusing submissions after too many failures."""

    _attr_translation_key = "lockout"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:lock-alert"

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_lockout"
        self._cancel_expiry: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool:
        """Whether the lockout is currently active."""
        return self.coordinator.is_locked_out(self.scope_id)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """When the lockout lifts."""
        return {"locked_until": self.coordinator.runtime(self.scope_id).locked_until}

    async def async_added_to_hass(self) -> None:
        """Track the lockout expiry as well as scope updates.

        A lockout ends by the clock rather than by an event, so without a timer the
        sensor would stay on until some later submission happened to disprove it. The
        base class already writes state on scope updates; this adds the re-arm.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SCOPE_UPDATED.format(self.scope_id),
                self._schedule_expiry,
            )
        )
        self.async_on_remove(self._cancel_scheduled_expiry)
        self._schedule_expiry()

    @callback
    def _schedule_expiry(self) -> None:
        """Arrange a state write for the moment the lockout lifts."""
        self._cancel_scheduled_expiry()
        locked_until = self.coordinator.runtime(self.scope_id).locked_until
        if locked_until is not None:
            self._cancel_expiry = async_track_point_in_utc_time(
                self.hass, self._handle_expiry, locked_until
            )

    @callback
    def _handle_expiry(self, _now: datetime) -> None:
        """Publish the state once the lockout has lapsed."""
        self._cancel_expiry = None
        self.async_write_ha_state()

    @callback
    def _cancel_scheduled_expiry(self) -> None:
        """Drop any pending expiry timer."""
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None
