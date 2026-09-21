"""Persistence: serialisation round trips, schema loading and time normalisation."""

from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyper_passcode.const import (
    DEFAULT_LOCKOUT_DURATION,
    DEFAULT_LOCKOUT_THRESHOLD,
    DOMAIN,
    MAX_RECENT_USES,
    STORAGE_KEY,
    SUBENTRY_TYPE_CREDENTIAL,
    SUBENTRY_TYPE_SCOPE,
    CodeType,
    GraceMode,
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

#: What the private store holds in the current (version 1) schema: the key, each
#: credential's secret half, and the audit log. The configuration half of a
#: credential lives in its config subentry, so none of it appears here.
V1_FIXTURE = {
    "key": "a" * 64,
    "secrets": {
        "cred-1": {
            "lookup_index": "b" * 64,
            "plaintext": None,
            "enabled": True,
            "revoked": False,
            "use_count": 7,
            "uncounted_uses": 2,
            "last_counted_use": "2026-09-19T08:00:00+00:00",
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

#: The matching subentry payload, as the credential dialog would store it.
CREDENTIAL_CONFIG = {
    "credential_id": "cred-1",
    "code_type": "pin",
    "keep_viewable": False,
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
        "grace_period_seconds": 300,
        "grace_mode": "fixed",
        "allowed_sources": ["keypad"],
    },
    "grants": [{"scope_id": "scope-1", "policy": None, "actions": None}],
}


def test_v1_payload_loads_completely():
    data = StoredData.from_dict(V1_FIXTURE)

    assert data.key == "a" * 64
    assert data.secrets["cred-1"]["use_count"] == 7
    assert data.audit[0].outcome is Outcome.VALID
    assert data.audit[0].label == "Cleaner"


def test_a_credential_is_assembled_from_both_halves():
    data = StoredData.from_dict(V1_FIXTURE)
    credential = Credential.assemble(
        "Cleaner", CREDENTIAL_CONFIG, data.secrets["cred-1"]
    )

    # Configuration comes from the subentry...
    assert credential.label == "Cleaner"
    assert credential.code_type is CodeType.PIN
    assert credential.owner == "person.alex"
    assert credential.policy.max_uses == 50
    assert credential.policy.schedule_entities == ["schedule.cleaner"]
    assert credential.grants[0].scope_id == "scope-1"
    # ...and the secret and counters from the private store.
    assert credential.lookup_index == "b" * 64
    assert credential.use_count == 7

    # The two halves round trip back to what they came from.
    assert credential.config_dict() == CREDENTIAL_CONFIG
    assert credential.secret_dict() == V1_FIXTURE["secrets"]["cred-1"]


def test_no_code_ever_reaches_the_config_half():
    credential = Credential(
        credential_id="c",
        label="Guest",
        lookup_index="deadbeef",
        plaintext="495162",
        keep_viewable=True,
    )
    config = credential.config_dict()

    assert "495162" not in str(config)
    assert "lookup_index" not in config
    assert "plaintext" not in config


def test_round_trip_is_lossless():
    original = StoredData.from_dict(V1_FIXTURE)
    restored = StoredData.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()


def test_a_sparse_payload_gets_sensible_defaults():
    data = StoredData.from_dict({"key": "c" * 64})
    credential = Credential.assemble("New", {"credential_id": "cred-2"}, {})

    assert data.secrets == {}
    assert data.audit == []
    assert credential.enabled is True
    assert credential.revoked is False
    assert credential.tags == []
    assert credential.grants == []
    assert credential.policy.max_uses is None


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


def test_an_uncounted_use_is_recorded_without_moving_the_counted_anchor():
    credential = Credential(credential_id="c", label="L", lookup_index="i")
    credential.record_use(WHEN)
    credential.record_use(WHEN + timedelta(minutes=1), counted=False)

    # It happened, so it lands everywhere a use lands...
    assert credential.use_count == 2
    assert credential.last_used == WHEN + timedelta(minutes=1)
    assert len(credential.recent_uses) == 2
    # ...it just did not eat into the allowance, and did not move the fixed window.
    assert credential.uncounted_uses == 1
    assert credential.counted_uses == 1
    assert credential.last_counted_use == WHEN


def test_a_credential_stored_before_grace_periods_loads_with_them_off():
    # No STORAGE_VERSION bump: every new field reads back through a defaulted get(),
    # so a credential written by an older release behaves exactly as it used to.
    credential = Credential.assemble(
        "Old",
        {"credential_id": "c", "policy": {"max_uses": 5}},
        {"lookup_index": "i", "use_count": 3},
    )

    assert credential.policy.grace_period_seconds is None
    assert credential.policy.grace_mode is GraceMode.FIXED
    assert credential.uncounted_uses == 0
    assert credential.last_counted_use is None
    assert credential.counted_uses == 3


def test_a_policy_mode_survives_a_round_trip_through_the_store():
    policy = Policy.from_dict(
        Policy(grace_period_seconds=300, grace_mode=GraceMode.SLIDING).to_dict()
    )
    assert policy.grace_period_seconds == 300
    assert policy.grace_mode is GraceMode.SLIDING
    # A subentry holds plain JSON, so the mode has to leave as a string.
    assert policy.to_dict()["grace_mode"] == "sliding"


def test_a_null_grace_mode_falls_back_to_fixed():
    # null is how update_code removes a rule, and a mode has no "unset".
    assert Policy.from_dict({"grace_mode": None}).grace_mode is GraceMode.FIXED


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


async def test_the_integration_loads_from_both_stores(
    hass: HomeAssistant, hass_storage
):
    # Credentials come from the store; scopes come from config subentries.
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": V1_FIXTURE,
    }

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HyperPasscode",
        data={},
        subentries_data=[
            ConfigSubentryData(
                data={
                    "icon": "mdi:door",
                    "default_actions": [{"event": "opened"}],
                    "code_length": 6,
                    "terminator_keys": ["#", "*"],
                    "inter_key_timeout": 8.0,
                    "lockout_threshold": 4,
                    "lockout_duration": 120,
                },
                subentry_type=SUBENTRY_TYPE_SCOPE,
                title="Front Door",
                unique_id=None,
            ),
            ConfigSubentryData(
                data=CREDENTIAL_CONFIG,
                subentry_type=SUBENTRY_TYPE_CREDENTIAL,
                title="Cleaner",
                unique_id=None,
            ),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    credential = coordinator.credentials["cred-1"]
    assert credential.label == "Cleaner"
    assert credential.use_count == 7
    assert credential.lookup_index == "b" * 64

    (scope,) = coordinator.scopes.values()
    assert scope.name == "Front Door"
    assert scope.terminator_keys == ["#", "*"]
    assert scope.code_length == 6
    # The scope's own lockout settings are restored as they were stored.
    assert scope.lockout_threshold == 4
    assert scope.lockout_duration == 120


async def test_scope_lockout_defaults_when_unset(
    hass: HomeAssistant, entry, coordinator
):
    configured = await coordinator.async_create_scope(
        name="Gate", lockout_threshold=2, lockout_duration=60
    )
    untouched = await coordinator.async_create_scope(name="Front Door")

    assert configured.lockout_threshold == 2
    assert configured.lockout_duration == 60
    assert untouched.lockout_threshold == DEFAULT_LOCKOUT_THRESHOLD
    assert untouched.lockout_duration == DEFAULT_LOCKOUT_DURATION


async def test_an_entry_from_before_lockout_moved_to_the_scope_still_loads(
    hass: HomeAssistant, hass_storage
):
    # Lockout used to be an integration option a scope could override, so an entry
    # written then carries the settings in its options and a None on every scope that
    # never overrode them. There is no migration: the stale keys are simply ignored
    # and the scope comes up at the defaults.
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": V1_FIXTURE,
    }

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HyperPasscode",
        data={},
        options={"lockout_threshold": 9, "lockout_duration": 600, "audit_log_size": 50},
        subentries_data=[
            ConfigSubentryData(
                data={
                    "icon": "mdi:door",
                    "terminator_keys": ["#"],
                    "lockout_threshold": None,
                    "lockout_duration": None,
                },
                subentry_type=SUBENTRY_TYPE_SCOPE,
                title="Front Door",
                unique_id=None,
            ),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    (scope,) = coordinator.scopes.values()
    assert scope.lockout_threshold == DEFAULT_LOCKOUT_THRESHOLD
    assert scope.lockout_duration == DEFAULT_LOCKOUT_DURATION
    # Settings that did not move are still read from the options.
    assert coordinator.audit_log_size == 50


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
