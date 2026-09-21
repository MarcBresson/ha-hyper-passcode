"""HyperPasscode: a credential, policy and action broker for Home Assistant.

Codes live in Home Assistant rather than inside a lock, so one policy engine can
govern any credential, from any input surface, authorising any action.
"""

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

    # Before the platforms, because a code's device links to its scope's and Home
    # Assistant refuses a link to a device that does not exist yet.
    coordinator.async_register_scope_devices()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_sync_credential_devices()

    async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    entry.async_on_unload(coordinator.async_shutdown)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HyperPasscodeConfigEntry
) -> bool:
    """Unload a config entry, flushing anything still waiting to be written.

    Saves are debounced, so without this an unload or reload could drop a code that
    was created moments earlier.
    """
    await entry.runtime_data.store.async_save()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_entry_updated(
    hass: HomeAssistant, entry: HyperPasscodeConfigEntry
) -> None:
    """React to a change on the config entry.

    Options and scopes both arrive here, and they want different handling. An options
    change can alter which entities should exist at all -- ``per_credential_entities``
    is the clear case -- so it reloads. A scope change is absorbed in place, because
    reloading would reset lockout counters and keypad buffers on every edit.
    """
    coordinator = entry.runtime_data
    if coordinator.async_options_changed():
        await hass.config_entries.async_reload(entry.entry_id)
        return
    coordinator.async_sync_subentries()
    coordinator.async_sync_credential_devices()
