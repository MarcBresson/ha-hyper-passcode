"""Data model for HyperPasscode.

Deliberately free of Home Assistant imports so the model and its serialisation stay
unit-testable on their own.
"""

from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

from .const import (
    DEFAULT_INTER_KEY_TIMEOUT,
    DEFAULT_TERMINATOR_KEYS,
    MAX_RECENT_USES,
    CodeType,
    Outcome,
)


def _dt_to_str(value: datetime | None) -> str | None:
    """Serialise a datetime as a UTC ISO 8601 string."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _dt_from_str(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string, normalising to an aware UTC datetime."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class Policy:
    """When a credential may be used.

    Every populated rule must pass. An empty policy means "always valid", which is
    what a plain household code wants.
    """

    valid_from: datetime | None = None
    valid_until: datetime | None = None
    #: ``schedule.*`` helper entities; recurring weekly, so they cannot express a
    #: date range on their own -- that is what valid_from/valid_until are for.
    schedule_entities: list[str] = field(default_factory=list)
    #: Any on/off entity: binary_sensor, switch, input_boolean, calendar.
    condition_entities: list[str] = field(default_factory=list)
    max_uses: int | None = None
    uses_per_hour: int | None = None
    uses_per_day: int | None = None
    cooldown_seconds: int | None = None
    #: Empty means every source is allowed.
    allowed_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the store."""
        return {
            "valid_from": _dt_to_str(self.valid_from),
            "valid_until": _dt_to_str(self.valid_until),
            "schedule_entities": list(self.schedule_entities),
            "condition_entities": list(self.condition_entities),
            "max_uses": self.max_uses,
            "uses_per_hour": self.uses_per_hour,
            "uses_per_day": self.uses_per_day,
            "cooldown_seconds": self.cooldown_seconds,
            "allowed_sources": list(self.allowed_sources),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        """Rebuild from the store."""
        return cls(
            valid_from=_dt_from_str(data.get("valid_from")),
            valid_until=_dt_from_str(data.get("valid_until")),
            schedule_entities=list(data.get("schedule_entities") or []),
            condition_entities=list(data.get("condition_entities") or []),
            max_uses=data.get("max_uses"),
            uses_per_hour=data.get("uses_per_hour"),
            uses_per_day=data.get("uses_per_day"),
            cooldown_seconds=data.get("cooldown_seconds"),
            allowed_sources=list(data.get("allowed_sources") or []),
        )


@dataclass
class Grant:
    """Links a credential to a scope.

    ``policy`` and ``actions`` are per-grant overrides. The v1 UI never sets them, but
    they live in the schema from day one: retrofitting many-to-many later would mean a
    painful store migration, whereas carrying the columns now costs nothing.
    """

    scope_id: str
    policy: Policy | None = None
    actions: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the store."""
        return {
            "scope_id": self.scope_id,
            "policy": self.policy.to_dict() if self.policy else None,
            "actions": self.actions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Grant:
        """Rebuild from the store."""
        raw_policy = data.get("policy")
        return cls(
            scope_id=data["scope_id"],
            policy=Policy.from_dict(raw_policy) if raw_policy else None,
            actions=data.get("actions"),
        )


@dataclass
class Credential:
    """A secret plus the policy governing it."""

    credential_id: str
    label: str
    #: ``HMAC-SHA256(code, integration_key)``. Lets a submission be matched in O(1)
    #: instead of hashing once per stored credential.
    lookup_index: str
    code_type: CodeType = CodeType.PIN
    #: Populated only when ``keep_viewable`` is set, and then it is the code in clear.
    plaintext: str | None = None
    keep_viewable: bool = False
    enabled: bool = True
    revoked: bool = False
    #: A ``person.*`` entity id, when the credential belongs to somebody.
    owner: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    policy: Policy = field(default_factory=Policy)
    grants: list[Grant] = field(default_factory=list)
    use_count: int = 0
    last_used: datetime | None = None
    #: Recent successful uses, newest last, capped at MAX_RECENT_USES. Backs the
    #: per-hour / per-day limits and the anti-replay cooldown.
    recent_uses: list[datetime] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def grant_for(self, scope_id: str) -> Grant | None:
        """Return this credential's grant on ``scope_id``, if it has one."""
        return next((g for g in self.grants if g.scope_id == scope_id), None)

    def policy_for(self, scope_id: str) -> Policy:
        """Return the effective policy on ``scope_id``.

        A per-grant override wins over the credential-wide policy.
        """
        grant = self.grant_for(scope_id)
        if grant is not None and grant.policy is not None:
            return grant.policy
        return self.policy

    def update_from(self, other: Credential) -> None:
        """Copy every field from ``other`` onto this instance.

        Used when a credential is re-read after its subentry changed. Mutating in
        place rather than replacing the object keeps references held elsewhere --
        by a running flow, or by a caller mid-operation -- pointing at live data.
        """
        for field_ in fields(self):
            setattr(self, field_.name, getattr(other, field_.name))

    def record_use(self, when: datetime) -> None:
        """Record a successful use, trimming the rate-limiting window."""
        self.use_count += 1
        self.last_used = when
        self.recent_uses.append(when)
        if len(self.recent_uses) > MAX_RECENT_USES:
            del self.recent_uses[:-MAX_RECENT_USES]

    def config_dict(self) -> dict[str, Any]:
        """Return the half that lives in the credential's config subentry.

        Configuration only: what the user typed into the dialog. Nothing secret and
        nothing that changes on its own, so editing a code does not churn through
        Home Assistant's config entry file.
        """
        return {
            "credential_id": self.credential_id,
            "code_type": str(self.code_type),
            "keep_viewable": self.keep_viewable,
            "owner": self.owner,
            "tags": list(self.tags),
            "notes": self.notes,
            "policy": self.policy.to_dict(),
            "grants": [g.to_dict() for g in self.grants],
        }

    def secret_dict(self) -> dict[str, Any]:
        """Return the half that stays in the private store.

        The lookup index, any plaintext the user asked to keep viewable, and the
        counters that change on every use. These would otherwise end up in
        ``core.config_entries``, which is not written with restricted permissions.
        """
        return {
            "lookup_index": self.lookup_index,
            "plaintext": self.plaintext,
            "enabled": self.enabled,
            "revoked": self.revoked,
            "use_count": self.use_count,
            "last_used": _dt_to_str(self.last_used),
            "recent_uses": [_dt_to_str(d) for d in self.recent_uses],
            "created_at": _dt_to_str(self.created_at),
            "updated_at": _dt_to_str(self.updated_at),
        }

    @classmethod
    def assemble(
        cls, label: str, config: dict[str, Any], secret: dict[str, Any]
    ) -> Credential:
        """Rebuild a credential from its subentry and its stored secret."""
        return cls(
            credential_id=config["credential_id"],
            label=label,
            lookup_index=secret.get("lookup_index", ""),
            code_type=CodeType(config.get("code_type", CodeType.PIN)),
            plaintext=secret.get("plaintext"),
            keep_viewable=config.get("keep_viewable", False),
            enabled=secret.get("enabled", True),
            revoked=secret.get("revoked", False),
            owner=config.get("owner"),
            tags=list(config.get("tags") or []),
            notes=config.get("notes", ""),
            policy=Policy.from_dict(config.get("policy") or {}),
            grants=[Grant.from_dict(g) for g in config.get("grants") or []],
            use_count=secret.get("use_count", 0),
            last_used=_dt_from_str(secret.get("last_used")),
            recent_uses=[
                parsed
                for raw in secret.get("recent_uses") or []
                if (parsed := _dt_from_str(raw)) is not None
            ],
            created_at=_dt_from_str(secret.get("created_at")),
            updated_at=_dt_from_str(secret.get("updated_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the whole credential, for diagnostics and exports."""
        return {
            **self.config_dict(),
            **self.secret_dict(),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Credential:
        """Rebuild from the store."""
        return cls(
            credential_id=data["credential_id"],
            label=data.get("label", ""),
            lookup_index=data["lookup_index"],
            code_type=CodeType(data.get("code_type", CodeType.PIN)),
            plaintext=data.get("plaintext"),
            keep_viewable=data.get("keep_viewable", False),
            enabled=data.get("enabled", True),
            revoked=data.get("revoked", False),
            owner=data.get("owner"),
            tags=list(data.get("tags") or []),
            notes=data.get("notes", ""),
            policy=Policy.from_dict(data.get("policy") or {}),
            grants=[Grant.from_dict(g) for g in data.get("grants") or []],
            use_count=data.get("use_count", 0),
            last_used=_dt_from_str(data.get("last_used")),
            recent_uses=[
                parsed
                for raw in data.get("recent_uses") or []
                if (parsed := _dt_from_str(raw)) is not None
            ],
            created_at=_dt_from_str(data.get("created_at")),
            updated_at=_dt_from_str(data.get("updated_at")),
        )


@dataclass
class Scope:
    """A target codes are entered against -- a door, a gate, an alarm.

    Becomes one Home Assistant Device carrying the scope's entities.
    """

    scope_id: str
    name: str
    icon: str = "mdi:dialpad"
    #: Home Assistant action sequence run on a valid code, so the common case needs
    #: no automation at all.
    default_actions: list[dict[str, Any]] = field(default_factory=list)
    #: Fixed code length for keystroke auto-submit. None means wait for a terminator.
    code_length: int | None = None
    terminator_keys: list[str] = field(
        default_factory=lambda: list(DEFAULT_TERMINATOR_KEYS)
    )
    inter_key_timeout: float = DEFAULT_INTER_KEY_TIMEOUT
    #: None falls back to the integration-level setting.
    lockout_threshold: int | None = None
    lockout_duration: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the store."""
        return {
            "scope_id": self.scope_id,
            "name": self.name,
            "icon": self.icon,
            "default_actions": self.default_actions,
            "code_length": self.code_length,
            "terminator_keys": list(self.terminator_keys),
            "inter_key_timeout": self.inter_key_timeout,
            "lockout_threshold": self.lockout_threshold,
            "lockout_duration": self.lockout_duration,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scope:
        """Rebuild from the store."""
        return cls(
            scope_id=data["scope_id"],
            name=data.get("name", ""),
            icon=data.get("icon", "mdi:dialpad"),
            default_actions=list(data.get("default_actions") or []),
            code_length=data.get("code_length"),
            terminator_keys=list(
                data.get("terminator_keys")
                if data.get("terminator_keys") is not None
                else DEFAULT_TERMINATOR_KEYS
            ),
            inter_key_timeout=data.get("inter_key_timeout", DEFAULT_INTER_KEY_TIMEOUT),
            lockout_threshold=data.get("lockout_threshold"),
            lockout_duration=data.get("lockout_duration"),
        )


@dataclass
class AuditEntry:
    """One line of the append-only submission log."""

    timestamp: datetime
    scope_id: str
    outcome: Outcome
    source: str
    credential_id: str | None = None
    label: str | None = None
    person: str | None = None
    reason: str | None = None
    #: What was actually typed. Only ever populated when the ``log_failed_plaintext``
    #: setting is on, because a failed attempt is usually a typo of a *real* code.
    typed: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the store."""
        return {
            "timestamp": _dt_to_str(self.timestamp),
            "scope_id": self.scope_id,
            "outcome": str(self.outcome),
            "source": self.source,
            "credential_id": self.credential_id,
            "label": self.label,
            "person": self.person,
            "reason": self.reason,
            "typed": self.typed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEntry:
        """Rebuild from the store."""
        timestamp = _dt_from_str(data.get("timestamp"))
        if timestamp is None:  # pragma: no cover - defensive, store always writes one
            timestamp = datetime.now(UTC)
        return cls(
            timestamp=timestamp,
            scope_id=data.get("scope_id", ""),
            outcome=Outcome(data.get("outcome", Outcome.INVALID)),
            source=data.get("source", ""),
            credential_id=data.get("credential_id"),
            label=data.get("label"),
            person=data.get("person"),
            reason=data.get("reason"),
            typed=data.get("typed"),
        )
