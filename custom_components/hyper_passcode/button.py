"""Buttons: the two things that are a press rather than a value.

Issuing a delivery code really is an action, and its code is surfaced as a persistent
notification since a button has no way to return a value. Clearing a validity window
is a button for a duller reason: a datetime entity can report "no bound" but has no
way to be set back to it.
"""

from datetime import timedelta

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HyperPasscodeConfigEntry
from .const import DOMAIN
from .coordinator import HyperPasscodeCoordinator
from .entity import (
    HyperPasscodeCredentialEntity,
    HyperPasscodeScopeEntity,
    async_add_credential_entities,
    async_add_scope_entities,
)
from .models import Credential, Scope

#: How long a one-press delivery code stays valid. Anything configurable belongs in
#: the ``create_otp`` action; this button is the zero-decision path.
DELIVERY_CODE_DURATION = timedelta(hours=2)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the scope and credential buttons."""
    coordinator = entry.runtime_data
    async_add_scope_entities(
        hass, entry, coordinator, async_add_entities, [GenerateDeliveryCodeButton]
    )
    async_add_credential_entities(
        hass, entry, coordinator, async_add_entities, [ClearValidityWindowButton]
    )


class GenerateDeliveryCodeButton(ButtonEntity, HyperPasscodeScopeEntity):
    """Creates a single-use code valid for the next couple of hours."""

    _attr_translation_key = "generate_delivery_code"
    _attr_icon = "mdi:package-variant-closed"

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_generate_delivery_code"

    async def async_press(self) -> None:
        """Issue the code and show it once."""
        scope = self.scope
        if scope is None:
            return

        credential, code = await self.coordinator.async_create_otp(
            scope_id=self.scope_id,
            label="Delivery code",
            duration=DELIVERY_CODE_DURATION,
        )
        valid_until = credential.policy.valid_until
        until_local = (
            dt_util.as_local(valid_until).strftime("%H:%M on %d %b")
            if valid_until
            else "further notice"
        )

        async_create_notification(
            self.hass,
            (
                f"**{code}**\n\n"
                f"Single use on **{scope.name}**, valid until {until_local}."
            ),
            title="Delivery code",
            notification_id=f"{DOMAIN}_delivery_{credential.credential_id}",
        )


class ClearValidityWindowButton(ButtonEntity, HyperPasscodeCredentialEntity):
    """Reopens a credential's validity window at both ends.

    The counterpart to the two datetime entities: they can move a bound but cannot
    remove one, and a guest code whose stay was extended needs the expiry gone
    rather than pushed out.
    """

    _attr_translation_key = "clear_validity_window"
    _attr_icon = "mdi:calendar-remove-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_clear_validity_window"

    async def async_press(self) -> None:
        """Drop both bounds, leaving the rest of the policy alone."""
        credential = self.credential
        if credential is None:
            return
        policy = credential.policy.to_dict()
        policy["valid_from"] = None
        policy["valid_until"] = None
        await self.coordinator.async_update_credential(
            self.credential_id, {"policy": policy}
        )
