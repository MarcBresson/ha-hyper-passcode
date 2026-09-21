"""Config and options flow.

HyperPasscode is a single hub entry; scopes and credentials are managed in the UI
rather than through the config flow. The options flow carries the integration-level
settings described in the README.

These dialogs only carry what an entity cannot: identity, the code itself, which
scopes it opens, and the entity pickers. Every threshold, limit and free-text field
lives on the scope's or the code's own device instead, where it can be read in a
template and changed without opening the integration page. That means the forms here
must never write a field they no longer show -- both edit steps merge their input
onto what is already stored rather than rebuilding it.
"""

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
    EntitySelector,
    EntitySelectorConfig,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
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
    DEFAULT_TERMINATOR_KEYS,
    DEFAULT_WEAK_CODE_BLOCKLIST,
    DOMAIN,
    SUBENTRY_TYPE_CREDENTIAL,
    SUBENTRY_TYPE_SCOPE,
)
from .exceptions import CodeCollisionError, WeakCodeError
from .models import Policy, Scope

ATTR_DEFAULT_ACTIONS = "default_actions"
ATTR_TERMINATOR_KEYS = "terminator_keys"
ATTR_CODE = "code"
ATTR_SCOPE_IDS = "scope_ids"
ATTR_KEEP_VIEWABLE = "keep_viewable"
ATTR_OWNER = "owner"
ATTR_SCHEDULE_ENTITIES = "schedule_entities"
ATTR_CONDITION_ENTITIES = "condition_entities"

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

        This is what puts "Add scope" and "Add code" buttons on the integration page,
        and gives each of them its own configure dialog and delete option.
        """
        return {
            SUBENTRY_TYPE_SCOPE: ScopeSubentryFlow,
            SUBENTRY_TYPE_CREDENTIAL: CredentialSubentryFlow,
        }


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

    Only what no entity can express. The code length, the inter-key timeout and the
    two lockout settings are number entities on the scope's own device, so a door's
    threshold can be changed from a dashboard rather than from here.
    """
    current = current or {}

    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=current.get(CONF_NAME, vol.UNDEFINED)
            ): TextSelector(),
            vol.Optional(
                CONF_ICON, default=current.get(CONF_ICON) or "mdi:dialpad"
            ): IconSelector(),
            vol.Optional(
                ATTR_DEFAULT_ACTIONS, default=current.get(ATTR_DEFAULT_ACTIONS) or []
            ): ActionSelector(),
            vol.Optional(
                ATTR_TERMINATOR_KEYS,
                default=current.get(ATTR_TERMINATOR_KEYS)
                or list(DEFAULT_TERMINATOR_KEYS),
            ): TextSelector(TextSelectorConfig(multiple=True)),
        }
    )


def _scope_entry(
    user_input: dict[str, Any], current: Mapping[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """Turn form input into a subentry title and payload.

    ``current`` is the scope's stored data, and the form is laid over it rather than
    replacing it: the fields this dialog no longer shows are owned by the scope's
    number entities, and an edit here must leave them exactly as they were.

    Routed through ``Scope`` so a scope created here and one created through the
    ``create_scope`` action are stored in exactly the same shape.
    """
    data = {**(current or {}), **user_input}
    name = data.pop(CONF_NAME)

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
            title, data = _scope_entry(user_input, subentry.data)
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=title, data=data
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_scope_schema({**subentry.data, CONF_NAME: subentry.title}),
        )


def _credential_schema(
    scope_options: list[SelectOptionDict],
    current: Mapping[str, Any] | None = None,
    *,
    editing: bool = False,
) -> vol.Schema:
    """Build the add/edit form for one credential.

    Only what no entity can express. The validity window, the use limits, the notes
    and the tags are all entities on the code's own device, so a guest code can be
    extended from a dashboard instead of through this dialog.

    When editing, the code field is left blank and means "leave the code alone" --
    a code that is not viewable cannot be shown back, so there is nothing to
    pre-fill it with. ``keep_viewable`` only appears when adding, because it decides
    whether a readable copy of the code is kept at the one moment the code exists in
    clear. Afterwards it is a switch, and one that can only be turned off.
    """
    current = current or {}
    policy = dict(current.get("policy") or {})

    def default(value: Any) -> Any:
        return vol.UNDEFINED if value is None else value

    schema: dict[Any, Any] = {
        vol.Required(
            CONF_NAME, default=default(current.get(CONF_NAME))
        ): TextSelector(),
        # Left blank on add it is generated, and on edit the existing code is kept.
        vol.Optional(ATTR_CODE): TextSelector(),
        vol.Optional(
            ATTR_SCOPE_IDS, default=current.get(ATTR_SCOPE_IDS) or []
        ): SelectSelector(SelectSelectorConfig(options=scope_options, multiple=True)),
    }

    if not editing:
        schema[
            vol.Optional(
                ATTR_KEEP_VIEWABLE, default=current.get(ATTR_KEEP_VIEWABLE, False)
            )
        ] = BooleanSelector()

    schema.update(
        {
            vol.Optional(ATTR_OWNER, default=default(current.get(ATTR_OWNER))): (
                EntitySelector(EntitySelectorConfig(domain="person"))
            ),
            vol.Optional(
                ATTR_SCHEDULE_ENTITIES,
                default=policy.get(ATTR_SCHEDULE_ENTITIES) or [],
            ): EntitySelector(EntitySelectorConfig(domain="schedule", multiple=True)),
            vol.Optional(
                ATTR_CONDITION_ENTITIES,
                default=policy.get(ATTR_CONDITION_ENTITIES) or [],
            ): EntitySelector(
                EntitySelectorConfig(
                    domain=["binary_sensor", "switch", "input_boolean", "calendar"],
                    multiple=True,
                )
            ),
        }
    )
    return vol.Schema(schema)


class CredentialSubentryFlow(ConfigSubentryFlow):
    """Add and edit codes from the integration page."""

    #: Held between the two add steps, so the code can be shown before it is committed.
    _credential: Any = None
    _code: str = ""

    def _scope_options(self) -> list[SelectOptionDict]:
        """List the scopes a code can be granted on."""
        return [
            SelectOptionDict(value=subentry_id, label=subentry.title)
            for subentry_id, subentry in self._get_entry().subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_SCOPE
        ]

    def _coordinator(self) -> Any:
        """Return the loaded coordinator behind this entry."""
        return self._get_entry().runtime_data

    @staticmethod
    def _policy(user_input: Mapping[str, Any], current: Policy | None = None) -> Policy:
        """Lay the two entity pickers over the policy the credential already has.

        Everything else in a policy -- the window, the limits, the cooldown, the
        allowed sources -- is set through entities or actions, so an edit here must
        carry it across untouched rather than reset it to the form's idea of empty.
        """
        policy = Policy.from_dict(current.to_dict()) if current else Policy()
        policy.schedule_entities = list(user_input.get(ATTR_SCHEDULE_ENTITIES) or [])
        policy.condition_entities = list(user_input.get(ATTR_CONDITION_ENTITIES) or [])
        return policy

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect the details for a new code."""
        errors: dict[str, str] = {}
        coordinator = self._coordinator()

        if user_input is not None:
            try:
                credential, code = await coordinator.async_create_credential(
                    label=user_input[CONF_NAME],
                    code=user_input.get(ATTR_CODE) or None,
                    scope_ids=list(user_input.get(ATTR_SCOPE_IDS) or []),
                    keep_viewable=user_input.get(ATTR_KEEP_VIEWABLE, False),
                    owner=user_input.get(ATTR_OWNER),
                    policy=self._policy(user_input),
                    persist=False,
                )
            except CodeCollisionError:
                errors["base"] = "code_collision"
            except WeakCodeError:
                errors["base"] = "weak_code"
            else:
                self._credential = credential
                self._code = code
                return await self.async_step_created()

        return self.async_show_form(
            step_id="user",
            data_schema=_credential_schema(self._scope_options(), user_input),
            errors=errors,
        )

    async def async_step_created(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the code once, then commit it.

        A code that is not kept viewable can never be shown again, so this step
        exists to give the user their one chance to write it down.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="created",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "code": self._code,
                    "label": self._credential.label,
                },
            )

        self._coordinator().async_stash_secret(self._credential)
        return self.async_create_entry(
            title=self._credential.label, data=self._credential.config_dict()
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing code."""
        subentry = self._get_reconfigure_subentry()
        coordinator = self._coordinator()
        credential_id = subentry.data["credential_id"]
        errors: dict[str, str] = {}

        if user_input is not None:
            changes: dict[str, Any] = {
                "label": user_input[CONF_NAME],
                "scope_ids": list(user_input.get(ATTR_SCOPE_IDS) or []),
                ATTR_OWNER: user_input.get(ATTR_OWNER),
                "policy": self._policy(
                    user_input, coordinator.credentials[credential_id].policy
                ).to_dict(),
            }
            if new_code := user_input.get(ATTR_CODE):
                changes["code"] = new_code

            try:
                credential = await coordinator.async_update_credential(
                    credential_id, changes
                )
            except CodeCollisionError:
                errors["base"] = "code_collision"
            except WeakCodeError:
                errors["base"] = "weak_code"
            else:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=credential.label,
                    data=credential.config_dict(),
                )

        credential = coordinator.credentials[credential_id]
        current = {
            **credential.config_dict(),
            CONF_NAME: credential.label,
            ATTR_SCOPE_IDS: [g.scope_id for g in credential.grants],
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credential_schema(
                self._scope_options(), current, editing=True
            ),
            errors=errors,
        )
