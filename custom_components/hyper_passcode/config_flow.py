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
    TextSelectorType,
)

from .const import (
    CONF_AUDIT_LOG_SIZE,
    CONF_DEFAULT_CODE_LENGTH,
    CONF_LOG_FAILED_PLAINTEXT,
    CONF_PER_CREDENTIAL_ENTITIES,
    CONF_REJECT_WEAK_CODES,
    CONF_WEAK_CODE_BLOCKLIST,
    DEFAULT_AUDIT_LOG_SIZE,
    DEFAULT_CODE_LENGTH,
    DEFAULT_LOG_FAILED_PLAINTEXT,
    DEFAULT_PER_CREDENTIAL_ENTITIES,
    DEFAULT_REJECT_WEAK_CODES,
    DEFAULT_TERMINATOR_KEYS,
    DEFAULT_WEAK_CODE_BLOCKLIST,
    DOMAIN,
    SUBENTRY_TYPE_CREDENTIAL,
    SUBENTRY_TYPE_SCOPE,
    Source,
)
from .coordinator import SubmissionResult
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
ATTR_SCOPE_ID = "scope_id"
ATTR_SOURCE = "source"
ATTR_DRY_RUN = "dry_run"

TITLE = "HyperPasscode"

#: Shown in place of the code on the confirmation step when no readable copy is being
#: kept. The code exists in clear for exactly the length of that step, and showing it
#: there would put on screen the one thing the user just asked not to keep.
MASKED_CODE = "****"

#: What the test page can submit as. ``unknown`` is left out: it is what the engine
#: uses for a submission with no stated origin, not something worth testing as.
TESTABLE_SOURCES = [str(source) for source in Source if source is not Source.UNKNOWN]


def _describe(result: SubmissionResult, dry_run: bool) -> str:
    """Put a submission's verdict into a sentence for the test page.

    Built here rather than translated: it stitches together a credential's label and
    its owner, which no static string can do. Never includes the code itself.
    """
    prefix = "Tested" if dry_run else "Submitted"
    if result.valid:
        who = f" as **{result.label}**" if result.label else ""
        owner = f", owned by `{result.person}`" if result.person else ""
        ran = " No actions were run." if dry_run else " The scope's actions ran."
        return f"{prefix}: **accepted**{who}{owner}.{ran}"

    reason = str(result.reason) if result.reason else "unknown"
    # A refusal only names a code when one was actually matched; an unknown code
    # has nothing to name.
    matched = f" It matched **{result.label}**." if result.label else ""
    return f"{prefix}: **refused** -- `{reason}`.{matched}"


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
    """The integration-level settings, and the page for trying a code by hand."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between the settings and the test page."""
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "test_code"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the settings form."""
        if user_input is not None:
            # Number selectors hand back floats; the rest of the code expects ints.
            for key in (CONF_AUDIT_LOG_SIZE, CONF_DEFAULT_CODE_LENGTH):
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
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_test_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Try a code against a scope and say what the engine made of it.

        This is the only place a code can be typed into Home Assistant by hand. It is
        a flow step rather than a ``text`` entity for one reason: setting an entity
        goes through ``text.set_value``, and a service call fires ``call_service``
        carrying its whole ``service_data``, which the recorder keeps by default. A
        flow's input never becomes an event, so the code stays out of the database.

        The step re-shows itself after every submission instead of finishing, so
        several codes can be tried in a row. It never creates an entry, so the
        options are untouched and the integration is never reloaded.
        """
        coordinator = self.config_entry.runtime_data
        if not coordinator.scopes:
            return self.async_abort(reason="no_scopes")

        if user_input is None:
            return self._test_form(None, None, "Nothing submitted yet.")

        if not (code := str(user_input.get(ATTR_CODE) or "").strip()):
            return self._test_form(
                user_input, {ATTR_CODE: "empty_code"}, "Nothing submitted yet."
            )

        dry_run = bool(user_input[ATTR_DRY_RUN])
        result = await coordinator.async_submit(
            user_input[ATTR_SCOPE_ID],
            code,
            user_input[ATTR_SOURCE],
            dry_run=dry_run,
            # A flow carries no Context for the admin who opened it. The submission
            # is recorded with its source either way, so the audit log still says
            # where it came from.
        )
        return self._test_form(user_input, None, _describe(result, dry_run))

    def _test_form(
        self,
        user_input: Mapping[str, Any] | None,
        errors: dict[str, str] | None,
        result: str,
    ) -> ConfigFlowResult:
        """Redraw the test page, carrying everything forward except the code.

        The code is never defaulted back in. Echoing it would put it on screen for
        whoever walks past next, and the verdict already names the code it matched.
        """
        current = user_input or {}
        scope_options = [
            SelectOptionDict(value=scope_id, label=scope.name)
            for scope_id, scope in self.config_entry.runtime_data.scopes.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    ATTR_SCOPE_ID,
                    default=current.get(ATTR_SCOPE_ID, scope_options[0]["value"]),
                ): SelectSelector(SelectSelectorConfig(options=scope_options)),
                vol.Optional(ATTR_CODE): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    ATTR_SOURCE, default=current.get(ATTR_SOURCE, str(Source.UI))
                ): SelectSelector(SelectSelectorConfig(options=TESTABLE_SOURCES)),
                vol.Required(
                    ATTR_DRY_RUN, default=current.get(ATTR_DRY_RUN, True)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="test_code",
            data_schema=schema,
            errors=errors,
            description_placeholders={"result": result},
        )


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
        # Masked while it is typed, like the test page's field: a code being set by
        # hand is a secret on screen whether or not a copy of it is kept afterwards.
        vol.Optional(ATTR_CODE): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
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
        """Confirm the new code, showing it only when a copy is being kept.

        Two variants, because the two cases have nothing to say to each other. With
        "Keep code viewable" ticked the code is printed and stays readable afterwards
        on its own device. Without it the code is masked here as well: it would
        otherwise be revealed on screen at the one moment the user has just said they
        do not want a readable copy of it to exist.
        """
        if user_input is None:
            viewable = self._credential.keep_viewable
            return self.async_show_form(
                step_id="created" if viewable else "created_hidden",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "code": self._code if viewable else MASKED_CODE,
                    "label": self._credential.label,
                },
            )
        return self._commit()

    async def async_step_created_hidden(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Take the submit from the masked variant of the confirmation step.

        Home Assistant routes a form's submit back to the step it was shown under, so
        the second variant needs a handler of its own even though it decides nothing.
        """
        if user_input is None:
            return await self.async_step_created()
        return self._commit()

    def _commit(self) -> SubentryFlowResult:
        """Store the secret half and write the subentry."""
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
