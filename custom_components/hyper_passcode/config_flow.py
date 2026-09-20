"""Config and options flow.

HyperPasscode is a single hub entry; scopes and credentials are managed in the UI
rather than through the config flow. The options flow carries the integration-level
settings described in the README.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_ICON, CONF_NAME
from homeassistant.helpers.selector import (
    ActionSelector,
    BooleanSelector,
    IconSelector,
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
    DEFAULT_INTER_KEY_TIMEOUT,
    DEFAULT_LOCKOUT_DURATION,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_LOG_FAILED_PLAINTEXT,
    DEFAULT_PER_CREDENTIAL_ENTITIES,
    DEFAULT_REJECT_WEAK_CODES,
    DEFAULT_TERMINATOR_KEYS,
    DEFAULT_WEAK_CODE_BLOCKLIST,
    DOMAIN,
    SUBENTRY_TYPE_SCOPE,
)
from .models import Scope

ATTR_DEFAULT_ACTIONS = "default_actions"
ATTR_CODE_LENGTH = "code_length"
ATTR_TERMINATOR_KEYS = "terminator_keys"
ATTR_INTER_KEY_TIMEOUT = "inter_key_timeout"

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

    @classmethod
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Declare scopes as a subentry type.

        This is what puts an "Add scope" button on the integration page, and gives
        each scope its own configure dialog and delete option.
        """
        return {SUBENTRY_TYPE_SCOPE: ScopeSubentryFlow}


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


def _scope_schema(current: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the add/edit form for one scope.

    ``lockout_threshold`` and ``lockout_duration`` are optional here on purpose:
    leaving them blank falls back to the integration-wide setting, and filling them
    in overrides it for this scope alone.
    """
    current = current or {}

    def default(key: str, fallback: Any = vol.UNDEFINED) -> Any:
        value = current.get(key)
        return fallback if value is None else value

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=default(CONF_NAME)): TextSelector(),
            vol.Optional(
                CONF_ICON, default=current.get(CONF_ICON) or "mdi:dialpad"
            ): IconSelector(),
            vol.Optional(
                ATTR_DEFAULT_ACTIONS, default=current.get(ATTR_DEFAULT_ACTIONS) or []
            ): ActionSelector(),
            vol.Optional(ATTR_CODE_LENGTH, default=default(ATTR_CODE_LENGTH)): _count(
                1, 64
            ),
            vol.Optional(
                ATTR_TERMINATOR_KEYS,
                default=current.get(ATTR_TERMINATOR_KEYS)
                or list(DEFAULT_TERMINATOR_KEYS),
            ): TextSelector(TextSelectorConfig(multiple=True)),
            vol.Optional(
                ATTR_INTER_KEY_TIMEOUT,
                default=current.get(ATTR_INTER_KEY_TIMEOUT)
                or DEFAULT_INTER_KEY_TIMEOUT,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=300, step=0.5, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_LOCKOUT_THRESHOLD, default=default(CONF_LOCKOUT_THRESHOLD)
            ): _count(0, 100),
            vol.Optional(
                CONF_LOCKOUT_DURATION, default=default(CONF_LOCKOUT_DURATION)
            ): _count(0, 86400),
        }
    )


def _scope_entry(user_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Turn form input into a subentry title and payload.

    Routed through ``Scope`` so a scope created here and one created through the
    ``create_scope`` action are stored in exactly the same shape.
    """
    data = dict(user_input)
    name = data.pop(CONF_NAME)

    for key in (ATTR_CODE_LENGTH, CONF_LOCKOUT_THRESHOLD, CONF_LOCKOUT_DURATION):
        if data.get(key) is not None:
            data[key] = int(data[key])
    if data.get(ATTR_INTER_KEY_TIMEOUT) is not None:
        data[ATTR_INTER_KEY_TIMEOUT] = float(data[ATTR_INTER_KEY_TIMEOUT])

    scope = Scope.from_dict({**data, "scope_id": "", "name": name})
    payload = scope.to_dict()
    payload.pop("scope_id")
    payload.pop("name")
    return name, payload


class ScopeSubentryFlow(ConfigSubentryFlow):
    """Add and edit scopes from the integration page."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a scope."""
        if user_input is not None:
            title, data = _scope_entry(user_input)
            return self.async_create_entry(title=title, data=data)
        return self.async_show_form(step_id="user", data_schema=_scope_schema())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing scope."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            title, data = _scope_entry(user_input)
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=title, data=data
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_scope_schema({**subentry.data, CONF_NAME: subentry.title}),
        )
