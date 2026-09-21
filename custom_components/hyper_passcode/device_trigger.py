"""Device triggers.

This is what makes codes usable without writing YAML: the automation editor offers
"Front Door: valid code entered" directly, and optionally narrows it to one credential.
"""

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CREDENTIAL_ID,
    DOMAIN,
    EVENT_SUBMISSION,
    EventType,
)
from .helpers import async_scope_id_for_device

#: Trigger type to the event type it listens for.
TRIGGER_TYPES: dict[str, EventType] = {
    "valid_code": EventType.VALID,
    "invalid_code": EventType.INVALID,
    "expired_code": EventType.EXPIRED,
    "rate_limited": EventType.RATE_LIMITED,
    "lockout": EventType.LOCKOUT,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(ATTR_CREDENTIAL_ID): cv.string,
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List the triggers a scope device offers."""
    if async_scope_id_for_device(hass, device_id) is None:
        return []

    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_TYPES
    ]


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Offer an optional credential filter on code-related triggers.

    Lockout is not about any one credential, so it takes no filter.
    """
    if config[CONF_TYPE] == "lockout":
        return {}
    return {
        "extra_fields": vol.Schema({vol.Optional(ATTR_CREDENTIAL_ID): cv.string}),
    }


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach the trigger by listening for this integration's submission event."""
    event_data: dict[str, Any] = {
        CONF_DEVICE_ID: config[CONF_DEVICE_ID],
        "event_type": str(TRIGGER_TYPES[config[CONF_TYPE]]),
    }
    if credential_id := config.get(ATTR_CREDENTIAL_ID):
        event_data[ATTR_CREDENTIAL_ID] = credential_id

    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_SUBMISSION,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
