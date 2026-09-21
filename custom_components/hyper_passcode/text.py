"""Texts: a credential's notes and tags, editable from its device page.

Tags are a list in the model and a single comma-separated string here, because a text
entity holds one value. Splitting on commas is enough: a tag with a comma in it would
be unusable in the ``revoke_all`` filter anyway.

Home Assistant caps an entity state at 255 characters, so both of these do too. Notes
longer than that belong in a markdown card, not in a state machine.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HyperPasscodeConfigEntry
from .coordinator import HyperPasscodeCoordinator
from .entity import HyperPasscodeCredentialEntity, async_add_credential_entities
from .models import Credential

#: The longest value Home Assistant will accept as an entity state.
MAX_TEXT_LENGTH = 255


@dataclass(frozen=True, kw_only=True)
class CredentialTextDescription(TextEntityDescription):
    """One editable free-text field on a credential."""

    value_fn: Callable[[Credential], str]
    #: Turns what was typed into what the credential stores under ``key``.
    to_stored: Callable[[str], Any]


def _split_tags(value: str) -> list[str]:
    """Parse the comma-separated tag list back into the model's list."""
    return [tag.strip() for tag in value.split(",") if tag.strip()]


CREDENTIAL_TEXTS: tuple[CredentialTextDescription, ...] = (
    CredentialTextDescription(
        key="notes",
        translation_key="notes",
        icon="mdi:note-text-outline",
        entity_category=EntityCategory.CONFIG,
        mode=TextMode.TEXT,
        native_max=MAX_TEXT_LENGTH,
        value_fn=lambda credential: credential.notes,
        to_stored=str,
    ),
    CredentialTextDescription(
        key="tags",
        translation_key="tags",
        icon="mdi:tag-multiple-outline",
        entity_category=EntityCategory.CONFIG,
        mode=TextMode.TEXT,
        native_max=MAX_TEXT_LENGTH,
        value_fn=lambda credential: ", ".join(credential.tags),
        to_stored=_split_tags,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the free-text fields on every credential."""
    async_add_credential_entities(
        hass,
        entry,
        entry.runtime_data,
        async_add_entities,
        [partial(CredentialText, description=d) for d in CREDENTIAL_TEXTS],
    )


class CredentialText(TextEntity, HyperPasscodeCredentialEntity):
    """One free-text field on a credential."""

    entity_description: CredentialTextDescription

    def __init__(
        self,
        coordinator: HyperPasscodeCoordinator,
        credential: Credential,
        description: CredentialTextDescription,
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self.entity_description = description
        self._attr_unique_id = f"{credential.credential_id}_{description.key}"

    @property
    def native_value(self) -> str | None:
        """The current text, truncated to what a state can hold."""
        credential = self.credential
        if credential is None:
            return None
        return self.entity_description.value_fn(credential)[:MAX_TEXT_LENGTH]

    async def async_set_value(self, value: str) -> None:
        """Write the new text back to the credential."""
        await self.coordinator.async_update_credential(
            self.credential_id,
            {self.entity_description.key: self.entity_description.to_stored(value)},
        )
