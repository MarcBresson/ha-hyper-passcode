"""Selects: the credential settings that are a choice between named modes.

A select is the only entity kind whose *options* carry translated labels, which makes
it the right home for a setting whose whole difficulty is explaining what each value
does. The dropdown itself says what a fixed and a sliding grace window mean; a switch
would have had one name and two untranslatable states to say it in.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HyperPasscodeConfigEntry
from .const import GraceMode
from .coordinator import HyperPasscodeCoordinator
from .entity import HyperPasscodeCredentialEntity, async_add_credential_entities
from .models import Credential, Policy


@dataclass(frozen=True, kw_only=True)
class PolicySelectDescription(SelectEntityDescription):
    """One mode on a credential's policy."""

    value_fn: Callable[[Policy], str]


POLICY_SELECTS: tuple[PolicySelectDescription, ...] = (
    PolicySelectDescription(
        key="grace_mode",
        translation_key="grace_mode",
        icon="mdi:timer-cog-outline",
        entity_category=EntityCategory.CONFIG,
        options=[str(mode) for mode in GraceMode],
        value_fn=lambda policy: str(policy.grace_mode),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the policy mode selects on every credential."""
    async_add_credential_entities(
        hass,
        entry,
        entry.runtime_data,
        async_add_entities,
        [partial(PolicySelect, description=d) for d in POLICY_SELECTS],
    )


class PolicySelect(SelectEntity, HyperPasscodeCredentialEntity):
    """One mode on a credential's policy.

    Deliberately not gated on the setting it qualifies being switched on: hiding the
    mode until a grace period exists would mean you could never choose the mode first,
    and an entity that comes and goes is worse to automate against than an inert one.
    """

    entity_description: PolicySelectDescription

    def __init__(
        self,
        coordinator: HyperPasscodeCoordinator,
        credential: Credential,
        description: PolicySelectDescription,
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self.entity_description = description
        self._attr_unique_id = f"{credential.credential_id}_{description.key}"

    @property
    def current_option(self) -> str | None:
        """The mode currently in force."""
        credential = self.credential
        if credential is None:
            return None
        return self.entity_description.value_fn(credential.policy)

    async def async_select_option(self, option: str) -> None:
        """Switch the mode."""
        credential = self.credential
        if credential is None:
            return
        policy = credential.policy.to_dict()
        policy[self.entity_description.key] = option
        await self.coordinator.async_update_credential(
            self.credential_id, {"policy": policy}
        )
