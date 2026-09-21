"""Switches: the two credential flags worth flipping without a dialog."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HyperPasscodeConfigEntry
from .const import DOMAIN
from .coordinator import HyperPasscodeCoordinator
from .entity import HyperPasscodeCredentialEntity, async_add_credential_entities
from .models import Credential


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the enable and keep-viewable switches on every credential."""
    async_add_credential_entities(
        hass,
        entry,
        entry.runtime_data,
        async_add_entities,
        [CredentialEnabledSwitch, CredentialKeepViewableSwitch],
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


class CredentialKeepViewableSwitch(SwitchEntity, HyperPasscodeCredentialEntity):
    """Whether the code is stored in clear so it can be read back and re-shared.

    Only ever a one-way trip in practice: turning it off discards the stored copy,
    and there is nothing left to put back. Turning it on is refused rather than
    silently doing nothing, because a switch that looked on while the code was
    unrecoverable would be a lie.
    """

    _attr_translation_key = "keep_viewable"
    _attr_icon = "mdi:eye-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_keep_viewable"

    @property
    def is_on(self) -> bool | None:
        """Whether a readable copy of the code is being kept."""
        credential = self.credential
        if credential is None:
            return None
        return credential.keep_viewable

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Refuse, unless a readable copy happens to still be there."""
        credential = self.credential
        if credential is None:
            return
        if credential.plaintext is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="code_not_recoverable",
                translation_placeholders={"label": credential.label},
            )
        await self.coordinator.async_update_credential(
            self.credential_id, {"keep_viewable": True}
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Discard the stored copy of the code."""
        await self.coordinator.async_update_credential(
            self.credential_id, {"keep_viewable": False}
        )
