"""HyperPasscode: a credential, policy and action broker for Home Assistant.

Codes live in Home Assistant rather than inside a lock, so one policy engine can
govern any credential, from any input surface, authorising any action.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import HyperPasscodeCoordinator
from .services import async_register_services
from .store import HyperPasscodeStore

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type HyperPasscodeConfigEntry = ConfigEntry[HyperPasscodeCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: HyperPasscodeConfigEntry
) -> bool:
    """Set up HyperPasscode from a config entry."""
    store = HyperPasscodeStore(hass)
    coordinator = HyperPasscodeCoordinator(hass, entry, store)
    await coordinator.async_load()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    entry.async_on_unload(coordinator.async_shutdown)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HyperPasscodeConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(
    hass: HomeAssistant, entry: HyperPasscodeConfigEntry
) -> None:
    """Reload when options change.

    A reload is the honest response: settings such as ``per_credential_entities``
    change which entities should exist at all.
    """
    await hass.config_entries.async_reload(entry.entry_id)
