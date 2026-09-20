"""Config and options flow.

HyperPasscode is a single hub entry; scopes and credentials are managed in the UI
rather than through the config flow. The options flow carries the integration-level
settings described in the README.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_AUDIT_LOG_SIZE,
    CONF_DEFAULT_CODE_LENGTH,
    CONF_LOCKOUT_DURATION,
    CONF_LOCKOUT_THRESHOLD,
    CONF_LOG_FAILED_PLAINTEXT,
    CONF_PER_CREDENTIAL_ENTITIES,
    CONF_REJECT_WEAK_CODES,
    CONF_WEAK_CODE_BLOCKLIST,
    DEFAULT_AUDIT_LOG_SIZE,
    DEFAULT_CODE_LENGTH,
    DEFAULT_LOCKOUT_DURATION,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_LOG_FAILED_PLAINTEXT,
    DEFAULT_PER_CREDENTIAL_ENTITIES,
    DEFAULT_REJECT_WEAK_CODES,
    DEFAULT_WEAK_CODE_BLOCKLIST,
    DOMAIN,
)

TITLE = "HyperPasscode"


def _count(minimum: int, maximum: int) -> NumberSelector:
    """Return a plain integer box selector."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
        )
    )


class HyperPasscodeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single hub entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then create the entry. There is nothing to configure up front."""
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title=TITLE, data={})

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:  # noqa: ANN001
        """Return the options flow handler."""
        return HyperPasscodeOptionsFlow()


class HyperPasscodeOptionsFlow(OptionsFlow):
    """Edit the integration-level settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the settings form."""
        if user_input is not None:
            # Number selectors hand back floats; the rest of the code expects ints.
            for key in (
                CONF_AUDIT_LOG_SIZE,
                CONF_DEFAULT_CODE_LENGTH,
                CONF_LOCKOUT_THRESHOLD,
                CONF_LOCKOUT_DURATION,
            ):
                if key in user_input:
                    user_input[key] = int(user_input[key])
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_REJECT_WEAK_CODES,
                    default=options.get(
                        CONF_REJECT_WEAK_CODES, DEFAULT_REJECT_WEAK_CODES
                    ),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_WEAK_CODE_BLOCKLIST,
                    default=options.get(
                        CONF_WEAK_CODE_BLOCKLIST, DEFAULT_WEAK_CODE_BLOCKLIST
                    ),
                ): TextSelector(TextSelectorConfig(multiple=True)),
                vol.Optional(
                    CONF_PER_CREDENTIAL_ENTITIES,
                    default=options.get(
                        CONF_PER_CREDENTIAL_ENTITIES, DEFAULT_PER_CREDENTIAL_ENTITIES
                    ),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_DEFAULT_CODE_LENGTH,
                    default=options.get(CONF_DEFAULT_CODE_LENGTH, DEFAULT_CODE_LENGTH),
                ): _count(3, 32),
                vol.Optional(
                    CONF_LOCKOUT_THRESHOLD,
                    default=options.get(
                        CONF_LOCKOUT_THRESHOLD, DEFAULT_LOCKOUT_THRESHOLD
                    ),
                ): _count(0, 100),
                vol.Optional(
                    CONF_LOCKOUT_DURATION,
                    default=options.get(
                        CONF_LOCKOUT_DURATION, DEFAULT_LOCKOUT_DURATION
                    ),
                ): _count(0, 86400),
                vol.Optional(
                    CONF_AUDIT_LOG_SIZE,
                    default=options.get(CONF_AUDIT_LOG_SIZE, DEFAULT_AUDIT_LOG_SIZE),
                ): _count(0, 100000),
                vol.Optional(
                    CONF_LOG_FAILED_PLAINTEXT,
                    default=options.get(
                        CONF_LOG_FAILED_PLAINTEXT, DEFAULT_LOG_FAILED_PLAINTEXT
                    ),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
