"""Sensors: per-scope activity and per-credential use counts."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HyperPasscodeConfigEntry
from .const import ATTR_LABEL
from .coordinator import HyperPasscodeCoordinator
from .entity import (
    HyperPasscodeCredentialEntity,
    HyperPasscodeScopeEntity,
    async_add_credential_entities,
    async_add_scope_entities,
)
from .models import Credential, Scope


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up scope and credential sensors."""
    coordinator = entry.runtime_data
    async_add_scope_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        [ScopeLastUsedSensor, ScopeFailedAttemptsSensor],
    )
    async_add_credential_entities(
        hass, entry, coordinator, async_add_entities, [CredentialUsesSensor]
    )


class ScopeLastUsedSensor(SensorEntity, HyperPasscodeScopeEntity):
    """When this scope last accepted a code."""

    _attr_translation_key = "last_used"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_last_used"

    @property
    def native_value(self) -> datetime | None:
        """The timestamp of the last accepted code."""
        return self.coordinator.runtime(self.scope_id).last_used

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Which credential it was."""
        return {ATTR_LABEL: self.coordinator.runtime(self.scope_id).last_label}


class ScopeFailedAttemptsSensor(SensorEntity, HyperPasscodeScopeEntity):
    """Consecutive failed attempts since the last success.

    Resets on a success and when a lockout trips, since the lockout is what the
    counter was building towards.
    """

    _attr_translation_key = "failed_attempts"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_failed_attempts"

    @property
    def native_value(self) -> int:
        """The current failure count."""
        return self.coordinator.runtime(self.scope_id).failed_attempts


class CredentialUsesSensor(SensorEntity, HyperPasscodeCredentialEntity):
    """How many times a credential has been accepted."""

    _attr_translation_key = "uses"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_uses"

    @property
    def native_value(self) -> int | None:
        """The lifetime use count."""
        credential = self.credential
        return credential.use_count if credential else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Remaining uses and validity window, for at-a-glance debugging."""
        credential = self.credential
        if credential is None:
            return {}
        policy = credential.policy
        remaining = (
            max(policy.max_uses - credential.use_count, 0)
            if policy.max_uses is not None
            else None
        )
        return {
            "remaining_uses": remaining,
            "max_uses": policy.max_uses,
            "valid_from": policy.valid_from,
            "valid_until": policy.valid_until,
            "enabled": credential.enabled,
            "revoked": credential.revoked,
            "tags": credential.tags,
            "last_used": credential.last_used,
        }
