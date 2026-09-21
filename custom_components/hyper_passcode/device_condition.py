"""Device conditions.

Both conditions hang off a scope device, because validity is always relative to a
scope: the same credential can be live on the gate and expired on the front door.
"""

from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_CONDITION, CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.condition import ConditionCheckerType
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_CREDENTIAL_ID, DOMAIN
from .helpers import async_get_coordinator, async_scope_id_for_device

CONDITION_TYPES = {"is_locked_out", "credential_valid"}

CONDITION_SCHEMA = cv.DEVICE_CONDITION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(CONDITION_TYPES),
        vol.Optional(ATTR_CREDENTIAL_ID): cv.string,
    }
)


async def async_get_conditions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List the conditions a scope device offers."""
    if async_scope_id_for_device(hass, device_id) is None:
        return []

    return [
        {
            CONF_CONDITION: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: condition_type,
        }
        for condition_type in CONDITION_TYPES
    ]


async def async_get_condition_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Ask which credential to check, for the credential condition."""
    if config[CONF_TYPE] != "credential_valid":
        return {}
    return {
        "extra_fields": vol.Schema({vol.Required(ATTR_CREDENTIAL_ID): cv.string}),
    }


@callback
def async_condition_from_config(
    hass: HomeAssistant, config: ConfigType
) -> ConditionCheckerType:
    """Build the condition checker."""
    device_id = config[CONF_DEVICE_ID]
    condition_type = config[CONF_TYPE]
    credential_id = config.get(ATTR_CREDENTIAL_ID)

    @callback
    def test_condition(hass: HomeAssistant, variables: Any) -> bool:
        """Evaluate against live state.

        Resolved on every call rather than at setup, so the condition keeps working
        across reloads and does not hold a stale coordinator.
        """
        coordinator = async_get_coordinator(hass)
        scope_id = async_scope_id_for_device(hass, device_id)
        if coordinator is None or scope_id is None:
            return False

        if condition_type == "is_locked_out":
            return coordinator.is_locked_out(scope_id)

        if credential_id is None:
            return False
        return coordinator.is_currently_valid(credential_id, scope_id)

    return test_condition
