"""Small shared lookups.

Kept separate from the coordinator so the device automation modules, which Home
Assistant imports on their own, do not have to reach into it.
"""

from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import CREDENTIAL_DEVICE_PREFIX, DOMAIN, KEYPAD_DEVICE_PREFIX
from .coordinator import HyperPasscodeCoordinator

#: A scope's own device identifier carries no prefix, unlike a credential's or a
#: keypad's, so a scope is whatever is left once those two are excluded.
_NON_SCOPE_PREFIXES = (f"{CREDENTIAL_DEVICE_PREFIX}_", f"{KEYPAD_DEVICE_PREFIX}_")


@callback
def async_scope_id_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the scope a device represents, or None if it is not a scope."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None

    for domain, identifier in device.identifiers:
        if domain == DOMAIN and not identifier.startswith(_NON_SCOPE_PREFIXES):
            return identifier
    return None


@callback
def async_get_coordinator(hass: HomeAssistant) -> HyperPasscodeCoordinator | None:
    """Return the loaded coordinator, if the integration is set up."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


def to_utc(value: datetime | None) -> datetime | None:
    """Normalise a user-supplied datetime to aware UTC.

    A naive value is read as local time, which is what somebody typing
    ``2026-09-21 14:00`` into a form means.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_utc(value)
