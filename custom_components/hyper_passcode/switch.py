"""Switch: enable or disable a credential without deleting it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HyperPasscodeConfigEntry
from .coordinator import HyperPasscodeCoordinator
from .entity import HyperPasscodeCredentialEntity, async_add_credential_entities
from .models import Credential


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one enable switch per credential."""
    async_add_credential_entities(
        hass,
        entry,
        entry.runtime_data,
        async_add_entities,
        [CredentialEnabledSwitch],
    )


class CredentialEnabledSwitch(SwitchEntity, HyperPasscodeCredentialEntity):
    """Turns a credential on and off.

    A revoked credential stays off and cannot be switched back on -- revocation is
    meant to be final, which is the whole point of having it alongside disabling.
    """

    _attr_translation_key = "enabled"
    _attr_icon = "mdi:key"

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_enabled"
        self._attr_name = credential.label

    @property
    def is_on(self) -> bool | None:
        """Whether the credential is currently usable."""
        credential = self.credential
        if credential is None:
            return None
        return credential.enabled and not credential.revoked

    @property
    def available(self) -> bool:
        """A revoked credential is shown but cannot be toggled."""
        credential = self.credential
        return credential is not None and not credential.revoked

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the credential."""
        await self.coordinator.async_set_enabled(self.credential_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the credential."""
        await self.coordinator.async_set_enabled(self.credential_id, False)
