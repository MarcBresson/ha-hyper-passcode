"""Button: issue a delivery code for a scope in one press.

This is the one place a button entity is the right primitive -- pressing it really is
the action. The generated code is surfaced as a persistent notification, since a
button entity has no way to return a value.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HyperPasscodeConfigEntry
from .const import DOMAIN
from .coordinator import HyperPasscodeCoordinator
from .entity import HyperPasscodeScopeEntity, async_add_scope_entities
from .models import Scope

#: How long a one-press delivery code stays valid. Anything configurable belongs in
#: the ``create_otp`` action; this button is the zero-decision path.
DELIVERY_CODE_DURATION = timedelta(hours=2)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one delivery-code button per scope."""
    async_add_scope_entities(
        hass,
        entry,
        entry.runtime_data,
        async_add_entities,
        [GenerateDeliveryCodeButton],
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
