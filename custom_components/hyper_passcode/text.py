"""Texts: a keypad buffer's terminator keys.

The terminator keys are a list in the model and a single comma-separated string here,
because a text entity holds one value. Splitting on commas is enough: a keypad that
sends a comma as its terminator is not a keypad anyone has.

Home Assistant caps an entity state at 255 characters, so this does too.
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
from .entity import HyperPasscodeKeypadEntity, async_add_keypad_entities
from .models import Keypad

#: The longest value Home Assistant will accept as an entity state.
MAX_TEXT_LENGTH = 255


@dataclass(frozen=True, kw_only=True)
class KeypadTextDescription(TextEntityDescription):
    """One editable free-text field on a keypad buffer."""

    value_fn: Callable[[Keypad], str]
    #: Turns what was typed into what the keypad stores under ``key``.
    to_stored: Callable[[str], Any]


def _split_list(value: str) -> list[str]:
    """Parse a comma-separated field back into the model's list."""
    return [item.strip() for item in value.split(",") if item.strip()]


KEYPAD_TEXTS: tuple[KeypadTextDescription, ...] = (
    KeypadTextDescription(
        key="terminator_keys",
        translation_key="terminator_keys",
        icon="mdi:keyboard-return",
        entity_category=EntityCategory.CONFIG,
        mode=TextMode.TEXT,
        native_max=MAX_TEXT_LENGTH,
        value_fn=lambda keypad: ", ".join(keypad.terminator_keys),
        to_stored=_split_list,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the free-text fields on every keypad buffer."""
    coordinator = entry.runtime_data
    async_add_keypad_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        [partial(KeypadText, description=d) for d in KEYPAD_TEXTS],
    )


class KeypadText(TextEntity, HyperPasscodeKeypadEntity):
    """One free-text field on a keypad buffer."""

    entity_description: KeypadTextDescription

    def __init__(
        self,
        coordinator: HyperPasscodeCoordinator,
        keypad: Keypad,
        description: KeypadTextDescription,
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, keypad)
        self.entity_description = description
        self._attr_unique_id = f"{keypad.keypad_id}_{description.key}"

    @property
    def native_value(self) -> str | None:
        """The current text, truncated to what a state can hold."""
        keypad = self.keypad
        if keypad is None:
            return None
        return self.entity_description.value_fn(keypad)[:MAX_TEXT_LENGTH]

    async def async_set_value(self, value: str) -> None:
        """Write the new text back to the keypad's subentry."""
        await self.coordinator.async_update_keypad(
            self.keypad_id,
            {self.entity_description.key: self.entity_description.to_stored(value)},
        )
