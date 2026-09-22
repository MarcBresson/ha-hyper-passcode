"""Service actions.

These are the whole of M1's interface: everything the management card will eventually
do is reachable here from Developer Tools first.
"""

from datetime import datetime
from typing import Any, cast

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util.json import JsonValueType

from . import audit as audit_log
from .const import (
    ATTR_CODE,
    ATTR_CREDENTIAL_ID,
    ATTR_KEY,
    ATTR_KEYPAD_ID,
    ATTR_LABEL,
    ATTR_SCOPE_ID,
    ATTR_SOURCE,
    DEFAULT_GRACE_MODE,
    DOMAIN,
    SERVICE_CLEAR_BUFFER,
    SERVICE_CREATE_CODE,
    SERVICE_CREATE_OTP,
    SERVICE_EXPORT_AUDIT,
    SERVICE_REVOKE,
    SERVICE_SET_ENABLED,
    SERVICE_SUBMIT,
    SERVICE_SUBMIT_KEY,
    SERVICE_TEST_CODE,
    CodeType,
    GraceMode,
    Source,
)
from .coordinator import HyperPasscodeCoordinator
from .helpers import to_utc as _to_utc
from .models import Policy

SERVICE_UPDATE_CODE = "update_code"
SERVICE_DELETE_CODE = "delete_code"
SERVICE_REVOKE_ALL = "revoke_all"
SERVICE_CREATE_SCOPE = "create_scope"
SERVICE_UPDATE_SCOPE = "update_scope"
SERVICE_DELETE_SCOPE = "delete_scope"
SERVICE_CREATE_KEYPAD = "create_keypad"
SERVICE_UPDATE_KEYPAD = "update_keypad"
SERVICE_DELETE_KEYPAD = "delete_keypad"

#: Policy fields are flattened into the service schemas rather than nested, because a
#: nested mapping is painful to fill in from the Developer Tools UI.
POLICY_FIELDS: dict[Any, Any] = {
    vol.Optional("valid_from"): cv.datetime,
    vol.Optional("valid_until"): cv.datetime,
    vol.Optional("schedule_entities"): cv.entity_ids,
    vol.Optional("condition_entities"): cv.entity_ids,
    vol.Optional("max_uses"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("uses_per_hour"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("uses_per_day"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("cooldown_seconds"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    vol.Optional("grace_period_seconds"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    # No ``default=`` on either of these. UPDATE_POLICY_FIELDS is derived from this
    # dict, and a default there would make every update_code call that left the mode
    # out silently reset it. create_code's default comes from _policy_from_call.
    vol.Optional("grace_mode"): vol.Coerce(GraceMode),
    vol.Optional("allowed_sources"): vol.All(cv.ensure_list, [cv.string]),
}

#: The same fields for an update, where ``null`` is how a rule is removed. Without
#: this there would be no way to lift an expiry from YAML, since leaving a field out
#: has to mean "leave it alone".
UPDATE_POLICY_FIELDS: dict[Any, Any] = {
    marker: vol.Any(None, validator) for marker, validator in POLICY_FIELDS.items()
}

#: Which of an update's fields belong to the policy rather than the credential.
POLICY_FIELD_NAMES: frozenset[str] = frozenset(
    str(marker.schema) for marker in POLICY_FIELDS
)

SUBMIT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCOPE_ID): cv.string,
        vol.Required(ATTR_CODE): cv.string,
        vol.Optional(ATTR_SOURCE, default=str(Source.SERVICE)): cv.string,
    }
)

SUBMIT_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_KEYPAD_ID): cv.string,
        vol.Required(ATTR_KEY): cv.string,
        vol.Optional(ATTR_SOURCE, default=str(Source.KEYPAD)): cv.string,
    }
)

SCOPE_ONLY_SCHEMA = vol.Schema({vol.Required(ATTR_SCOPE_ID): cv.string})

KEYPAD_ONLY_SCHEMA = vol.Schema({vol.Required(ATTR_KEYPAD_ID): cv.string})

CREATE_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LABEL): cv.string,
        vol.Optional(ATTR_CODE): cv.string,
        vol.Optional("scope_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("length"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional("code_type", default=str(CodeType.PIN)): vol.Coerce(CodeType),
        vol.Optional("keep_viewable", default=False): cv.boolean,
        vol.Optional("owner"): cv.entity_id,
        vol.Optional("notes", default=""): cv.string,
        **POLICY_FIELDS,
    }
)

CREATE_OTP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCOPE_ID): cv.string,
        vol.Optional(ATTR_LABEL, default="One-time code"): cv.string,
        vol.Optional("valid_from"): cv.datetime,
        vol.Optional("valid_until"): cv.datetime,
        vol.Optional("duration"): cv.positive_time_period,
        vol.Optional("max_uses", default=1): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("grace_period_seconds"): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
        vol.Optional("length"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional("keep_viewable", default=True): cv.boolean,
    }
)

CREDENTIAL_ONLY_SCHEMA = vol.Schema({vol.Required(ATTR_CREDENTIAL_ID): cv.string})

UPDATE_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CREDENTIAL_ID): cv.string,
        vol.Optional(ATTR_LABEL): cv.string,
        vol.Optional(ATTR_CODE): cv.string,
        vol.Optional("scope_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("keep_viewable"): cv.boolean,
        vol.Optional("owner"): vol.Any(None, cv.entity_id),
        vol.Optional("notes"): cv.string,
        **UPDATE_POLICY_FIELDS,
    }
)

SET_ENABLED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CREDENTIAL_ID): cv.string,
        vol.Required("enabled"): cv.boolean,
    }
)

REVOKE_ALL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SCOPE_ID): cv.string,
    }
)

TEST_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCOPE_ID): cv.string,
        vol.Required(ATTR_CODE): cv.string,
        vol.Optional(ATTR_SOURCE, default=str(Source.SERVICE)): cv.string,
    }
)

EXPORT_AUDIT_SCHEMA = vol.Schema(
    {
        vol.Optional("format", default="json"): vol.In(["json", "csv"]),
        vol.Optional(ATTR_SCOPE_ID): cv.string,
        vol.Optional("limit"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

CREATE_SCOPE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("default_actions"): cv.SCRIPT_SCHEMA,
        vol.Optional("lockout_threshold"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("lockout_duration"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)

UPDATE_SCOPE_SCHEMA = CREATE_SCOPE_SCHEMA.extend(
    {
        vol.Required(ATTR_SCOPE_ID): cv.string,
        vol.Optional("name"): cv.string,
    }
)

CREATE_KEYPAD_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required(ATTR_SCOPE_ID): cv.string,
        vol.Optional("code_length"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional("terminator_keys"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("inter_key_timeout"): vol.Coerce(float),
    }
)

UPDATE_KEYPAD_SCHEMA = CREATE_KEYPAD_SCHEMA.extend(
    {
        vol.Required(ATTR_KEYPAD_ID): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional(ATTR_SCOPE_ID): cv.string,
    }
)


def _iso(value: datetime | None) -> str | None:
    """Render a datetime for a service response, which has to be JSON."""
    return None if value is None else value.isoformat()


def _policy_from_call(data: dict[str, Any]) -> Policy:
    """Build a Policy from flattened service fields."""
    return Policy(
        valid_from=_to_utc(data.get("valid_from")),
        valid_until=_to_utc(data.get("valid_until")),
        schedule_entities=list(data.get("schedule_entities") or []),
        condition_entities=list(data.get("condition_entities") or []),
        max_uses=data.get("max_uses"),
        uses_per_hour=data.get("uses_per_hour"),
        uses_per_day=data.get("uses_per_day"),
        cooldown_seconds=data.get("cooldown_seconds"),
        grace_period_seconds=data.get("grace_period_seconds"),
        grace_mode=GraceMode(data.get("grace_mode") or DEFAULT_GRACE_MODE),
        allowed_sources=list(data.get("allowed_sources") or []),
    )


def _coordinator(hass: HomeAssistant) -> HyperPasscodeCoordinator:
    """Return the loaded coordinator, or explain why there isn't one."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("HyperPasscode is not set up")
    return entries[0].runtime_data


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register every service action. Safe to call on each reload."""
    if hass.services.has_service(DOMAIN, SERVICE_SUBMIT):
        return

    async def submit(call: ServiceCall) -> ServiceResponse:
        """Validate a code and act on the outcome."""
        result = await _coordinator(hass).async_submit(
            call.data[ATTR_SCOPE_ID],
            call.data[ATTR_CODE],
            call.data[ATTR_SOURCE],
            context=call.context,
        )
        return result.as_response()

    async def submit_key(call: ServiceCall) -> ServiceResponse:
        """Feed one keystroke into a keypad buffer."""
        result = await _coordinator(hass).async_submit_key(
            call.data[ATTR_KEYPAD_ID], call.data[ATTR_KEY], call.data[ATTR_SOURCE]
        )
        return result.as_response() if result else {"valid": None, "pending": True}

    async def clear_buffer(call: ServiceCall) -> None:
        """Discard a partially entered code."""
        _coordinator(hass).async_clear_buffer(call.data[ATTR_KEYPAD_ID])

    async def test_code(call: ServiceCall) -> ServiceResponse:
        """Validate without recording a use or running any action."""
        result = await _coordinator(hass).async_submit(
            call.data[ATTR_SCOPE_ID],
            call.data[ATTR_CODE],
            call.data[ATTR_SOURCE],
            dry_run=True,
        )
        return result.as_response()

    async def create_code(call: ServiceCall) -> ServiceResponse:
        """Create a credential, generating a code when none was given."""
        data = dict(call.data)
        credential, code = await _coordinator(hass).async_create_credential(
            label=data[ATTR_LABEL],
            code=data.get(ATTR_CODE),
            scope_ids=data.get("scope_ids"),
            code_type=data["code_type"],
            keep_viewable=data["keep_viewable"],
            owner=data.get("owner"),
            notes=data["notes"],
            policy=_policy_from_call(data),
            length=data.get("length"),
        )
        return {
            ATTR_CODE: code,
            ATTR_CREDENTIAL_ID: credential.credential_id,
            ATTR_LABEL: credential.label,
        }

    async def create_otp(call: ServiceCall) -> ServiceResponse:
        """Create a single-use, time-limited code."""
        data = dict(call.data)
        credential, code = await _coordinator(hass).async_create_otp(
            scope_id=data[ATTR_SCOPE_ID],
            label=data[ATTR_LABEL],
            valid_from=_to_utc(data.get("valid_from")),
            valid_until=_to_utc(data.get("valid_until")),
            duration=data.get("duration"),
            max_uses=data["max_uses"],
            grace_period_seconds=data.get("grace_period_seconds"),
            length=data.get("length"),
            keep_viewable=data["keep_viewable"],
        )
        return {
            ATTR_CODE: code,
            ATTR_CREDENTIAL_ID: credential.credential_id,
            "valid_from": _iso(credential.policy.valid_from),
            "valid_until": _iso(credential.policy.valid_until),
        }

    async def revoke(call: ServiceCall) -> None:
        """Permanently disable a credential."""
        await _coordinator(hass).async_revoke(call.data[ATTR_CREDENTIAL_ID])

    async def update_code(call: ServiceCall) -> None:
        """Change a credential, leaving out whatever should stay as it is.

        The counterpart to the code's entities, and the only way to reach these
        settings when ``per_credential_entities`` is off. Only the fields actually
        passed are touched, so a policy is edited rather than replaced.
        """
        coordinator = _coordinator(hass)
        changes = dict(call.data)
        credential_id = changes.pop(ATTR_CREDENTIAL_ID)
        policy = coordinator.get_credential(credential_id).policy.to_dict()

        rules = {key: changes.pop(key) for key in POLICY_FIELD_NAMES & set(changes)}
        if rules:
            for key, value in rules.items():
                policy[key] = (
                    _iso(_to_utc(value)) if isinstance(value, datetime) else value
                )
            changes["policy"] = policy

        if ATTR_LABEL in changes:
            changes["label"] = changes.pop(ATTR_LABEL)

        await coordinator.async_update_credential(credential_id, changes)

    async def delete_code(call: ServiceCall) -> None:
        """Delete a credential outright."""
        await _coordinator(hass).async_delete_credential(call.data[ATTR_CREDENTIAL_ID])

    async def set_enabled(call: ServiceCall) -> None:
        """Enable or disable a credential."""
        await _coordinator(hass).async_set_enabled(
            call.data[ATTR_CREDENTIAL_ID], call.data["enabled"]
        )

    async def revoke_all(call: ServiceCall) -> ServiceResponse:
        """Revoke every matching credential. The panic wipe."""
        count = await _coordinator(hass).async_revoke_all(
            scope_id=call.data.get(ATTR_SCOPE_ID)
        )
        return {"revoked": count}

    async def export_audit(call: ServiceCall) -> ServiceResponse:
        """Export the submission log."""
        coordinator = _coordinator(hass)
        entries = audit_log.recent(
            coordinator.data.audit,
            limit=call.data.get("limit"),
            scope_id=call.data.get(ATTR_SCOPE_ID),
        )
        if call.data["format"] == "csv":
            return {"format": "csv", "content": audit_log.to_csv(entries)}
        return {
            "format": "json",
            "entries": cast("list[JsonValueType]", audit_log.as_dicts(entries)),
        }

    async def create_scope(call: ServiceCall) -> ServiceResponse:
        """Add a scope."""
        scope = await _coordinator(hass).async_create_scope(**dict(call.data))
        return {ATTR_SCOPE_ID: scope.scope_id, "name": scope.name}

    async def update_scope(call: ServiceCall) -> None:
        """Change a scope's configuration."""
        changes = dict(call.data)
        scope_id = changes.pop(ATTR_SCOPE_ID)
        await _coordinator(hass).async_update_scope(scope_id, changes)

    async def delete_scope(call: ServiceCall) -> None:
        """Remove a scope and its entities."""
        await _coordinator(hass).async_delete_scope(call.data[ATTR_SCOPE_ID])

    async def create_keypad(call: ServiceCall) -> ServiceResponse:
        """Add a keypad buffer."""
        keypad = await _coordinator(hass).async_create_keypad(**dict(call.data))
        return {ATTR_KEYPAD_ID: keypad.keypad_id, "name": keypad.name}

    async def update_keypad(call: ServiceCall) -> None:
        """Change a keypad buffer's configuration."""
        changes = dict(call.data)
        keypad_id = changes.pop(ATTR_KEYPAD_ID)
        await _coordinator(hass).async_update_keypad(keypad_id, changes)

    async def delete_keypad(call: ServiceCall) -> None:
        """Remove a keypad buffer."""
        await _coordinator(hass).async_delete_keypad(call.data[ATTR_KEYPAD_ID])

    optional = SupportsResponse.OPTIONAL
    registrations: list[tuple[str, Any, vol.Schema, SupportsResponse | None]] = [
        (SERVICE_SUBMIT, submit, SUBMIT_SCHEMA, optional),
        (SERVICE_SUBMIT_KEY, submit_key, SUBMIT_KEY_SCHEMA, optional),
        (SERVICE_CLEAR_BUFFER, clear_buffer, KEYPAD_ONLY_SCHEMA, None),
        (SERVICE_TEST_CODE, test_code, TEST_CODE_SCHEMA, optional),
        (SERVICE_CREATE_CODE, create_code, CREATE_CODE_SCHEMA, optional),
        (SERVICE_CREATE_OTP, create_otp, CREATE_OTP_SCHEMA, optional),
        (SERVICE_REVOKE, revoke, CREDENTIAL_ONLY_SCHEMA, None),
        (SERVICE_UPDATE_CODE, update_code, UPDATE_CODE_SCHEMA, None),
        (SERVICE_DELETE_CODE, delete_code, CREDENTIAL_ONLY_SCHEMA, None),
        (SERVICE_SET_ENABLED, set_enabled, SET_ENABLED_SCHEMA, None),
        (SERVICE_REVOKE_ALL, revoke_all, REVOKE_ALL_SCHEMA, optional),
        (SERVICE_EXPORT_AUDIT, export_audit, EXPORT_AUDIT_SCHEMA, optional),
        (SERVICE_CREATE_SCOPE, create_scope, CREATE_SCOPE_SCHEMA, optional),
        (SERVICE_UPDATE_SCOPE, update_scope, UPDATE_SCOPE_SCHEMA, None),
        (SERVICE_DELETE_SCOPE, delete_scope, SCOPE_ONLY_SCHEMA, None),
        (SERVICE_CREATE_KEYPAD, create_keypad, CREATE_KEYPAD_SCHEMA, optional),
        (SERVICE_UPDATE_KEYPAD, update_keypad, UPDATE_KEYPAD_SCHEMA, None),
        (SERVICE_DELETE_KEYPAD, delete_keypad, KEYPAD_ONLY_SCHEMA, None),
    ]

    for name, handler, schema, supports_response in registrations:
        if supports_response is None:
            hass.services.async_register(DOMAIN, name, handler, schema=schema)
        else:
            hass.services.async_register(
                DOMAIN,
                name,
                handler,
                schema=schema,
                supports_response=supports_response,
            )
