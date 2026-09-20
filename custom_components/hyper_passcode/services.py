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
    ATTR_LABEL,
    ATTR_SCOPE_ID,
    ATTR_SOURCE,
    ATTR_TAGS,
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
    Source,
)
from .coordinator import HyperPasscodeCoordinator
from .helpers import to_utc as _to_utc
from .models import Policy

SERVICE_DELETE_CODE = "delete_code"
SERVICE_REVOKE_ALL = "revoke_all"
SERVICE_CREATE_SCOPE = "create_scope"
SERVICE_UPDATE_SCOPE = "update_scope"
SERVICE_DELETE_SCOPE = "delete_scope"

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
    vol.Optional("allowed_sources"): vol.All(cv.ensure_list, [cv.string]),
}

SUBMIT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCOPE_ID): cv.string,
        vol.Required(ATTR_CODE): cv.string,
        vol.Optional(ATTR_SOURCE, default=str(Source.SERVICE)): cv.string,
    }
)

SUBMIT_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCOPE_ID): cv.string,
        vol.Required(ATTR_KEY): cv.string,
        vol.Optional(ATTR_SOURCE, default=str(Source.KEYPAD)): cv.string,
    }
)

SCOPE_ONLY_SCHEMA = vol.Schema({vol.Required(ATTR_SCOPE_ID): cv.string})

CREATE_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LABEL): cv.string,
        vol.Optional(ATTR_CODE): cv.string,
        vol.Optional("scope_ids"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("length"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional("code_type", default=str(CodeType.PIN)): vol.Coerce(CodeType),
        vol.Optional("keep_viewable", default=False): cv.boolean,
        vol.Optional("owner"): cv.entity_id,
        vol.Optional(ATTR_TAGS): vol.All(cv.ensure_list, [cv.string]),
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
        vol.Optional("length"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional("keep_viewable", default=True): cv.boolean,
    }
)

CREDENTIAL_ONLY_SCHEMA = vol.Schema({vol.Required(ATTR_CREDENTIAL_ID): cv.string})

SET_ENABLED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CREDENTIAL_ID): cv.string,
        vol.Required("enabled"): cv.boolean,
    }
)

REVOKE_ALL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SCOPE_ID): cv.string,
        vol.Optional(ATTR_TAGS): vol.All(cv.ensure_list, [cv.string]),
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
        vol.Optional("icon", default="mdi:dialpad"): cv.icon,
        vol.Optional("default_actions"): cv.SCRIPT_SCHEMA,
        vol.Optional("code_length"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional("terminator_keys"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("inter_key_timeout"): vol.Coerce(float),
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
        """Feed one keystroke into a scope's buffer."""
        result = await _coordinator(hass).async_submit_key(
            call.data[ATTR_SCOPE_ID], call.data[ATTR_KEY], call.data[ATTR_SOURCE]
        )
        return result.as_response() if result else {"valid": None, "pending": True}

    async def clear_buffer(call: ServiceCall) -> None:
        """Discard a partially entered code."""
        _coordinator(hass).async_clear_buffer(call.data[ATTR_SCOPE_ID])

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
            tags=data.get(ATTR_TAGS),
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
            scope_id=call.data.get(ATTR_SCOPE_ID), tags=call.data.get(ATTR_TAGS)
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

    optional = SupportsResponse.OPTIONAL
    registrations: list[tuple[str, Any, vol.Schema, SupportsResponse | None]] = [
        (SERVICE_SUBMIT, submit, SUBMIT_SCHEMA, optional),
        (SERVICE_SUBMIT_KEY, submit_key, SUBMIT_KEY_SCHEMA, optional),
        (SERVICE_CLEAR_BUFFER, clear_buffer, SCOPE_ONLY_SCHEMA, None),
        (SERVICE_TEST_CODE, test_code, TEST_CODE_SCHEMA, optional),
        (SERVICE_CREATE_CODE, create_code, CREATE_CODE_SCHEMA, optional),
        (SERVICE_CREATE_OTP, create_otp, CREATE_OTP_SCHEMA, optional),
        (SERVICE_REVOKE, revoke, CREDENTIAL_ONLY_SCHEMA, None),
        (SERVICE_DELETE_CODE, delete_code, CREDENTIAL_ONLY_SCHEMA, None),
        (SERVICE_SET_ENABLED, set_enabled, SET_ENABLED_SCHEMA, None),
        (SERVICE_REVOKE_ALL, revoke_all, REVOKE_ALL_SCHEMA, optional),
        (SERVICE_EXPORT_AUDIT, export_audit, EXPORT_AUDIT_SCHEMA, optional),
        (SERVICE_CREATE_SCOPE, create_scope, CREATE_SCOPE_SCHEMA, optional),
        (SERVICE_UPDATE_SCOPE, update_scope, UPDATE_SCOPE_SCHEMA, None),
        (SERVICE_DELETE_SCOPE, delete_scope, SCOPE_ONLY_SCHEMA, None),
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
