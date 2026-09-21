"""Datetimes: a credential's validity window, editable where it is visible.

Both bounds are optional in the model, and a datetime entity has no way to express
"no bound" -- it can be unknown, but nothing can set it back to unknown. So clearing
the window is a button on the same device rather than a value written here.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from homeassistant.components.datetime import DateTimeEntity, DateTimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HyperPasscodeConfigEntry
from .coordinator import HyperPasscodeCoordinator
from .entity import HyperPasscodeCredentialEntity, async_add_credential_entities
from .models import Credential, Policy


@dataclass(frozen=True, kw_only=True)
class PolicyDateTimeDescription(DateTimeEntityDescription):
    """One end of a credential's validity window."""

    value_fn: Callable[[Policy], datetime | None]


POLICY_DATETIMES: tuple[PolicyDateTimeDescription, ...] = (
    PolicyDateTimeDescription(
        key="valid_from",
        translation_key="valid_from",
        icon="mdi:calendar-start",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda policy: policy.valid_from,
    ),
    PolicyDateTimeDescription(
        key="valid_until",
        translation_key="valid_until",
        icon="mdi:calendar-end",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda policy: policy.valid_until,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up both ends of the validity window on every credential."""
    async_add_credential_entities(
        hass,
        entry,
        entry.runtime_data,
        async_add_entities,
        [partial(PolicyDateTime, description=d) for d in POLICY_DATETIMES],
    )


class PolicyDateTime(DateTimeEntity, HyperPasscodeCredentialEntity):
    """One bound of a credential's validity window."""

    entity_description: PolicyDateTimeDescription

    def __init__(
        self,
        coordinator: HyperPasscodeCoordinator,
        credential: Credential,
        description: PolicyDateTimeDescription,
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self.entity_description = description
        self._attr_unique_id = f"{credential.credential_id}_{description.key}"

    @property
    def native_value(self) -> datetime | None:
        """The bound, or None when the window is open at this end."""
        credential = self.credential
        if credential is None:
            return None
        return self.entity_description.value_fn(credential.policy)

    async def async_set_value(self, value: datetime) -> None:
        """Move this bound of the window."""
        credential = self.credential
        if credential is None:
            return
        policy = credential.policy.to_dict()
        policy[self.entity_description.key] = dt_util.as_utc(value).isoformat()
        await self.coordinator.async_update_credential(
            self.credential_id, {"policy": policy}
        )
