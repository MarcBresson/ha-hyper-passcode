"""Persistence: serialisation round trips, schema loading and time normalisation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyper_passcode.const import (
    DOMAIN,
    MAX_RECENT_USES,
    STORAGE_KEY,
    CodeType,
    Outcome,
)
from custom_components.hyper_passcode.models import (
    AuditEntry,
    Credential,
    Grant,
    Policy,
    Scope,
)
from custom_components.hyper_passcode.store import StoredData

WHEN = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

#: A complete payload in the current (version 1) schema. Future migrations are tested
#: by adding their own fixture alongside this one.
V1_FIXTURE = {
    "key": "a" * 64,
    "scopes": {
        "scope-1": {
            "scope_id": "scope-1",
            "name": "Front Door",
            "icon": "mdi:door",
            "default_actions": [{"event": "opened"}],
            "code_length": 6,
            "terminator_keys": ["#", "*"],
            "inter_key_timeout": 8.0,
            "lockout_threshold": 4,
            "lockout_duration": 120,
        }
    },
    "credentials": {
        "cred-1": {
            "credential_id": "cred-1",
            "label": "Cleaner",
            "lookup_index": "b" * 64,
            "code_type": "pin",
            "plaintext": None,
            "keep_viewable": False,
            "enabled": True,
            "revoked": False,
            "owner": "person.alex",
            "tags": ["staff"],
            "notes": "Tuesdays",
            "policy": {
                "valid_from": "2026-09-01T00:00:00+00:00",
                "valid_until": "2026-12-01T00:00:00+00:00",
                "schedule_entities": ["schedule.cleaner"],
                "condition_entities": [],
                "max_uses": 50,
                "uses_per_hour": None,
                "uses_per_day": 2,
                "cooldown_seconds": 30,
                "allowed_sources": ["keypad"],
            },
            "grants": [{"scope_id": "scope-1", "policy": None, "actions": None}],
            "use_count": 7,
            "last_used": "2026-09-19T08:30:00+00:00",
            "recent_uses": ["2026-09-19T08:30:00+00:00"],
            "created_at": "2026-09-01T09:00:00+00:00",
            "updated_at": "2026-09-19T08:30:00+00:00",
        }
    },
    "audit": [
        {
            "timestamp": "2026-09-19T08:30:00+00:00",
            "scope_id": "scope-1",
            "outcome": "valid",
            "source": "keypad",
            "credential_id": "cred-1",
            "label": "Cleaner",
            "person": "person.alex",
            "reason": None,
            "typed": None,
        }
    ],
}


def test_v1_payload_loads_completely():
    data = StoredData.from_dict(V1_FIXTURE)

    assert data.key == "a" * 64

    scope = data.scopes["scope-1"]
    assert scope.name == "Front Door"
    assert scope.terminator_keys == ["#", "*"]
    assert scope.lockout_threshold == 4

    credential = data.credentials["cred-1"]
    assert credential.label == "Cleaner"
    assert credential.code_type is CodeType.PIN
    assert credential.owner == "person.alex"
    assert credential.use_count == 7
    assert credential.policy.max_uses == 50
    assert credential.policy.uses_per_day == 2
    assert credential.policy.schedule_entities == ["schedule.cleaner"]
    assert credential.grants[0].scope_id == "scope-1"

    assert data.audit[0].outcome is Outcome.VALID
    assert data.audit[0].label == "Cleaner"


def test_round_trip_is_lossless():
    original = StoredData.from_dict(V1_FIXTURE)
    restored = StoredData.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()


def test_a_sparse_payload_gets_sensible_defaults():
    data = StoredData.from_dict(
        {
            "key": "c" * 64,
            "credentials": {
                "cred-2": {"credential_id": "cred-2", "lookup_index": "d" * 64}
            },
        }
    )

    credential = data.credentials["cred-2"]
    assert credential.enabled is True
    assert credential.revoked is False
    assert credential.tags == []
    assert credential.grants == []
    assert credential.policy.max_uses is None
    assert data.scopes == {}
    assert data.audit == []


def test_a_payload_without_a_key_gets_a_fresh_one():
    data = StoredData.from_dict({})
    assert len(data.key) == 64


def test_naive_datetimes_are_normalised_to_utc():
    policy = Policy.from_dict({"valid_from": "2026-09-20T12:00:00"})
    assert policy.valid_from == WHEN
    assert policy.valid_from.tzinfo is not None


def test_non_utc_datetimes_are_converted():
    policy = Policy.from_dict({"valid_from": "2026-09-20T14:00:00+02:00"})
    assert policy.valid_from == WHEN


def test_recent_uses_are_capped():
    credential = Credential(credential_id="c", label="L", lookup_index="i")
    for offset in range(MAX_RECENT_USES + 25):
        credential.record_use(WHEN + timedelta(minutes=offset))

    assert credential.use_count == MAX_RECENT_USES + 25
    assert len(credential.recent_uses) == MAX_RECENT_USES
    # The oldest are the ones dropped.
    assert credential.recent_uses[-1] == WHEN + timedelta(minutes=MAX_RECENT_USES + 24)


def test_per_grant_policy_overrides_the_credential_policy():
    override = Policy(max_uses=1)
    credential = Credential(
        credential_id="c",
        label="L",
        lookup_index="i",
        policy=Policy(max_uses=99),
        grants=[Grant(scope_id="s1", policy=override), Grant(scope_id="s2")],
    )

    assert credential.policy_for("s1") is override
    assert credential.policy_for("s2").max_uses == 99


def test_scope_and_audit_round_trip():
    scope = Scope(scope_id="s", name="Gate", code_length=4)
    assert Scope.from_dict(scope.to_dict()).to_dict() == scope.to_dict()

    entry = AuditEntry(
        timestamp=WHEN, scope_id="s", outcome=Outcome.INVALID, source="ui"
    )
    assert AuditEntry.from_dict(entry.to_dict()).to_dict() == entry.to_dict()


async def test_the_integration_loads_persisted_data(
    hass: HomeAssistant, hass_storage
):
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": V1_FIXTURE,
    }

    entry = MockConfigEntry(domain=DOMAIN, title="HyperPasscode", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert "scope-1" in coordinator.scopes
    assert coordinator.credentials["cred-1"].label == "Cleaner"
    # last_used is recovered from the audit log rather than stored separately.
    assert coordinator.runtime("scope-1").last_label == "Cleaner"


async def test_credentials_survive_a_reload(hass: HomeAssistant, entry, coordinator):
    scope = await coordinator.async_create_scope(name="Front Door")
    credential, code = await coordinator.async_create_credential(
        label="Household", scope_ids=[scope.scope_id]
    )
    await coordinator.store.async_save()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    reloaded = entry.runtime_data
    assert credential.credential_id in reloaded.credentials
    # The lookup index was rebuilt, so the original code still resolves.
    result = await reloaded.async_submit(scope.scope_id, code)
    assert result.valid is True
