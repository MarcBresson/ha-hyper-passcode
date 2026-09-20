"""Small shared lookups.

Kept separate from the coordinator so the device automation modules, which Home
Assistant imports on their own, do not have to reach into it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import CREDENTIAL_DEVICE_PREFIX, DOMAIN
from .coordinator import HyperPasscodeCoordinator


@callback
def async_scope_id_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the scope a device represents, or None if it is not a scope."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None

    prefix = f"{CREDENTIAL_DEVICE_PREFIX}_"
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and not identifier.startswith(prefix):
            return identifier
    return None


@callback
def async_get_coordinator(hass: HomeAssistant) -> HyperPasscodeCoordinator | None:
    """Return the loaded coordinator, if the integration is set up."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None
