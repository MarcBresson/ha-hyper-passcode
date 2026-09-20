"""The central manager: submission handling, lockout, buffering and CRUD.

Everything that mutates HyperPasscode state goes through here, so there is exactly one
place where a code is checked, a use is counted and an action is fired.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.script import Script
from homeassistant.util import dt as dt_util

from . import audit
from .const import (
    ATTR_CREDENTIAL_ID,
    ATTR_LABEL,
    ATTR_OUTCOME,
    ATTR_PERSON,
    ATTR_REASON,
    ATTR_SCOPE_ID,
    ATTR_SOURCE,
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
    EVENT_SUBMISSION,
    REASON_TO_EVENT_TYPE,
    CodeType,
    EventType,
    Outcome,
    RejectionReason,
    Source,
    credential_device_identifier,
)
from .crypto import compute_lookup_index, find_weakness, generate_code, verify
from .exceptions import (
    CodeCollisionError,
    UnknownCredentialError,
    UnknownScopeError,
    WeakCodeError,
)
from .models import AuditEntry, Credential, Grant, Policy, Scope
from .policy import evaluate
from .store import HyperPasscodeStore, StoredData

_LOGGER = logging.getLogger(__name__)

#: Dispatcher signals. Entities subscribe rather than polling.
SIGNAL_SUBMISSION = f"{DOMAIN}_submission_{{}}"
SIGNAL_SCOPES_CHANGED = f"{DOMAIN}_scopes_changed"
SIGNAL_CREDENTIALS_CHANGED = f"{DOMAIN}_credentials_changed"
SIGNAL_CREDENTIAL_UPDATED = f"{DOMAIN}_credential_updated_{{}}"
SIGNAL_SCOPE_UPDATED = f"{DOMAIN}_scope_updated_{{}}"


@dataclass
class SubmissionResult:
    """The outcome of one code submission."""

    valid: bool
    scope_id: str
    source: str
    reason: RejectionReason | None = None
    credential_id: str | None = None
    label: str | None = None
    person: str | None = None

    @property
    def event_type(self) -> EventType:
        """The event entity type this result should fire."""
        if self.valid:
            return EventType.VALID
        if self.reason is not None:
            return REASON_TO_EVENT_TYPE.get(self.reason, EventType.INVALID)
        return EventType.INVALID

    def as_response(self) -> dict[str, Any]:
        """Render for a service call response."""
        return {
            "valid": self.valid,
            "reason": str(self.reason) if self.reason else None,
            "credential_id": self.credential_id,
            "label": self.label,
            "person": self.person,
        }


@dataclass
class ScopeRuntime:
    """Per-scope state that lives only in memory.

    Lockout deliberately does not survive a restart: a reboot is a plausible recovery
    path for a locked-out household, and persisting it would mostly serve to lock
    people out for longer than intended.
    """

    failed_attempts: int = 0
    locked_until: datetime | None = None
    buffer: str = ""
    cancel_buffer_timer: CALLBACK_TYPE | None = None
    last_used: datetime | None = None
    last_label: str | None = None
    script: Script | None = None
    script_source: list[dict[str, Any]] = field(default_factory=list)


class HyperPasscodeCoordinator:
    """Owns the data and every state transition."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, store: HyperPasscodeStore
    ) -> None:
        """Wire the coordinator to its config entry and store."""
        self.hass = hass
        self.entry = entry
        self.store = store
        self._index: dict[str, str] = {}
        self._runtime: dict[str, ScopeRuntime] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load persisted data and rebuild in-memory state."""
        await self.store.async_load()
        self._rebuild_index()
        self._rebuild_runtime()

    def _rebuild_index(self) -> None:
        """Rebuild the lookup-index to credential-id map."""
        self._index = {
            credential.lookup_index: credential_id
            for credential_id, credential in self.data.credentials.items()
        }

    def _rebuild_runtime(self) -> None:
        """Recreate per-scope runtime, recovering last-used from the audit log.

        Deriving last-used from the audit log rather than persisting it separately
        keeps a single source of truth.
        """
        self._runtime = {scope_id: ScopeRuntime() for scope_id in self.data.scopes}
        for entry in self.data.audit:
            if entry.outcome is not Outcome.VALID:
                continue
            runtime = self._runtime.get(entry.scope_id)
            if runtime is None:
                continue
            if runtime.last_used is None or entry.timestamp > runtime.last_used:
                runtime.last_used = entry.timestamp
                runtime.last_label = entry.label

    @callback
    def async_shutdown(self) -> None:
        """Cancel any pending keystroke timers."""
        for runtime in self._runtime.values():
            if runtime.cancel_buffer_timer is not None:
                runtime.cancel_buffer_timer()
                runtime.cancel_buffer_timer = None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def data(self) -> StoredData:
        """The loaded dataset."""
        return self.store.data

    @property
    def scopes(self) -> dict[str, Scope]:
        """Configured scopes by id."""
        return self.data.scopes

    @property
    def credentials(self) -> dict[str, Credential]:
        """Configured credentials by id."""
        return self.data.credentials

    def get_scope(self, scope_id: str) -> Scope:
        """Return a scope or raise."""
        try:
            return self.data.scopes[scope_id]
        except KeyError:
            raise UnknownScopeError(f"No such scope: {scope_id}") from None

    def get_credential(self, credential_id: str) -> Credential:
        """Return a credential or raise."""
        try:
            return self.data.credentials[credential_id]
        except KeyError:
            raise UnknownCredentialError(
                f"No such credential: {credential_id}"
            ) from None

    def runtime(self, scope_id: str) -> ScopeRuntime:
        """Return (creating if needed) the runtime state for a scope."""
        return self._runtime.setdefault(scope_id, ScopeRuntime())

    def setting(self, key: str, default: Any) -> Any:
        """Read an integration-level setting from the config entry's options."""
        return self.entry.options.get(key, default)

    @property
    def reject_weak_codes(self) -> bool:
        """Whether weak codes are refused. On by default."""
        return bool(self.setting(CONF_REJECT_WEAK_CODES, DEFAULT_REJECT_WEAK_CODES))

    @property
    def weak_code_blocklist(self) -> list[str]:
        """Extra values treated as weak."""
        return list(self.setting(CONF_WEAK_CODE_BLOCKLIST, DEFAULT_WEAK_CODE_BLOCKLIST))

    @property
    def per_credential_entities(self) -> bool:
        """Whether each credential gets its own entities."""
        return bool(
            self.setting(CONF_PER_CREDENTIAL_ENTITIES, DEFAULT_PER_CREDENTIAL_ENTITIES)
        )

    @property
    def audit_log_size(self) -> int:
        """Ring buffer length."""
        return int(self.setting(CONF_AUDIT_LOG_SIZE, DEFAULT_AUDIT_LOG_SIZE))

    @property
    def log_failed_plaintext(self) -> bool:
        """Whether to record what was typed on a failed attempt. Off by default."""
        return bool(
            self.setting(CONF_LOG_FAILED_PLAINTEXT, DEFAULT_LOG_FAILED_PLAINTEXT)
        )

    @property
    def default_code_length(self) -> int:
        """Starting length for generated codes."""
        return int(self.setting(CONF_DEFAULT_CODE_LENGTH, DEFAULT_CODE_LENGTH))

    def lockout_threshold(self, scope: Scope) -> int:
        """Failures before a scope locks out, scope override winning."""
        if scope.lockout_threshold is not None:
            return scope.lockout_threshold
        return int(self.setting(CONF_LOCKOUT_THRESHOLD, DEFAULT_LOCKOUT_THRESHOLD))

    def lockout_duration(self, scope: Scope) -> int:
        """Lockout length in seconds, scope override winning."""
        if scope.lockout_duration is not None:
            return scope.lockout_duration
        return int(self.setting(CONF_LOCKOUT_DURATION, DEFAULT_LOCKOUT_DURATION))

    def is_locked_out(self, scope_id: str, now: datetime | None = None) -> bool:
        """Whether a scope is currently refusing submissions."""
        runtime = self.runtime(scope_id)
        if runtime.locked_until is None:
            return False
        return (now or dt_util.utcnow()) < runtime.locked_until

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    async def async_submit(
        self,
        scope_id: str,
        code: str,
        source: str = Source.SERVICE,
        *,
        dry_run: bool = False,
        context: Context | None = None,
    ) -> SubmissionResult:
        """Validate ``code`` against ``scope_id`` and act on the outcome.

        With ``dry_run`` the code is evaluated but nothing is recorded, no action runs
        and no failure is counted -- this backs the ``test_code`` service.
        """
        scope = self.get_scope(scope_id)
        now = dt_util.utcnow()

        result = self._evaluate_submission(scope, code, source, now)

        if dry_run:
            return result

        if result.valid:
            credential = self.get_credential(result.credential_id)  # type: ignore[arg-type]
            credential.record_use(now)
            credential.updated_at = now
            # Set before _reset_failures, which dispatches the scope update the
            # last-used sensor reads synchronously.
            runtime = self.runtime(scope_id)
            runtime.last_used = now
            runtime.last_label = credential.label
            self._reset_failures(scope_id)
            self.store.async_schedule_save()
            async_dispatcher_send(
                self.hass, SIGNAL_CREDENTIAL_UPDATED.format(credential.credential_id)
            )
        else:
            self._register_failure(scope, now)

        self._record_audit(scope_id, code, result, now)
        self._fire_events(scope, result)

        # Actions run last, after the use is counted, so a failing action cannot be
        # retried to burn through a one-time code.
        if result.valid:
            await self._async_run_actions(scope, result, context)

        return result

    def _evaluate_submission(
        self, scope: Scope, code: str, source: str, now: datetime
    ) -> SubmissionResult:
        """Resolve a code to a verdict without mutating anything."""
        if self.is_locked_out(scope.scope_id, now):
            return SubmissionResult(
                valid=False,
                scope_id=scope.scope_id,
                source=source,
                reason=RejectionReason.LOCKED_OUT,
            )

        credential = self._lookup(code)
        if credential is None:
            return SubmissionResult(
                valid=False,
                scope_id=scope.scope_id,
                source=source,
                reason=RejectionReason.UNKNOWN_CODE,
            )

        reason = evaluate(self.hass, credential, scope.scope_id, source, now)
        return SubmissionResult(
            valid=reason is None,
            scope_id=scope.scope_id,
            source=source,
            reason=reason,
            credential_id=credential.credential_id,
            label=credential.label,
            person=credential.owner,
        )

    def _lookup(self, code: str) -> Credential | None:
        """Find the credential holding ``code``.

        The index lookup narrows to a candidate in constant time; the final decision
        is then a timing-safe comparison, so the match never rests on dict behaviour
        alone.
        """
        credential_id = self._index.get(compute_lookup_index(code, self.data.key))
        if credential_id is None:
            return None

        credential = self.data.credentials.get(credential_id)
        if credential is None:
            return None
        if not verify(code, credential.lookup_index, self.data.key):
            return None
        return credential

    def _register_failure(self, scope: Scope, now: datetime) -> None:
        """Count a failed attempt and trip the lockout if it crosses the threshold.

        Every rejection counts, not only unrecognised codes. Counting only unknown
        codes would let an attacker distinguish "wrong code" from "real but expired"
        by watching whether the lockout trips.
        """
        runtime = self.runtime(scope.scope_id)
        runtime.failed_attempts += 1
        threshold = self.lockout_threshold(scope)
        if threshold > 0 and runtime.failed_attempts >= threshold:
            runtime.locked_until = now + timedelta(seconds=self.lockout_duration(scope))
            runtime.failed_attempts = 0
            _LOGGER.warning(
                "Scope %s locked out until %s after %s failed attempts",
                scope.name,
                runtime.locked_until,
                threshold,
            )
        async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope.scope_id))

    def _reset_failures(self, scope_id: str) -> None:
        """Clear the failure counter after a success."""
        runtime = self.runtime(scope_id)
        runtime.failed_attempts = 0
        runtime.locked_until = None
        async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope_id))

    def _record_audit(
        self, scope_id: str, code: str, result: SubmissionResult, now: datetime
    ) -> None:
        """Append the submission to the audit ring buffer."""
        entry = AuditEntry(
            timestamp=now,
            scope_id=scope_id,
            outcome=Outcome.VALID if result.valid else Outcome.INVALID,
            source=result.source,
            credential_id=result.credential_id,
            label=result.label,
            person=result.person,
            reason=str(result.reason) if result.reason else None,
            typed=code if (not result.valid and self.log_failed_plaintext) else None,
        )
        audit.append(self.data.audit, entry, self.audit_log_size)
        self.store.async_schedule_save()

    def _fire_events(self, scope: Scope, result: SubmissionResult) -> None:
        """Notify the event entity and the bus."""
        async_dispatcher_send(
            self.hass, SIGNAL_SUBMISSION.format(scope.scope_id), result
        )
        self.hass.bus.async_fire(
            EVENT_SUBMISSION,
            {
                ATTR_SCOPE_ID: scope.scope_id,
                "scope_name": scope.name,
                "device_id": self.async_device_id(scope.scope_id),
                ATTR_OUTCOME: str(
                    Outcome.VALID if result.valid else Outcome.INVALID
                ),
                "event_type": str(result.event_type),
                ATTR_REASON: str(result.reason) if result.reason else None,
                ATTR_CREDENTIAL_ID: result.credential_id,
                ATTR_LABEL: result.label,
                ATTR_PERSON: result.person,
                ATTR_SOURCE: result.source,
            },
        )

    async def _async_run_actions(
        self, scope: Scope, result: SubmissionResult, context: Context | None
    ) -> None:
        """Run the scope's configured default actions."""
        if not scope.default_actions:
            return

        runtime = self.runtime(scope.scope_id)
        if runtime.script is None or runtime.script_source != scope.default_actions:
            runtime.script = Script(
                self.hass,
                scope.default_actions,
                f"{scope.name} default actions",
                DOMAIN,
            )
            runtime.script_source = list(scope.default_actions)

        credential = self.data.credentials.get(result.credential_id or "")
        try:
            await runtime.script.async_run(
                {
                    ATTR_SCOPE_ID: scope.scope_id,
                    "scope_name": scope.name,
                    ATTR_CREDENTIAL_ID: result.credential_id,
                    ATTR_LABEL: result.label,
                    ATTR_PERSON: result.person,
                    ATTR_SOURCE: result.source,
                    "tags": list(credential.tags) if credential else [],
                },
                context=context,
            )
        except Exception:
            _LOGGER.exception("Default actions for scope %s failed", scope.name)

    @callback
    def async_device_id(self, scope_id: str) -> str | None:
        """Return the device registry id for a scope, if registered."""
        device = self._async_scope_device(scope_id)
        return device.id if device else None

    @callback
    def _async_scope_device(self, scope_id: str) -> dr.DeviceEntry | None:
        """Look up a scope's device."""
        return dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, scope_id), self.entry.entry_id
        )

    # ------------------------------------------------------------------
    # Keystroke buffering
    # ------------------------------------------------------------------

    async def async_submit_key(
        self, scope_id: str, key: str, source: str = Source.KEYPAD
    ) -> SubmissionResult | None:
        """Feed one keystroke into a scope's buffer.

        Physical keypads emit one event per key, so the buffer submits when it sees a
        terminator key, when it reaches the scope's fixed code length, and clears
        itself after the inter-key timeout.
        """
        scope = self.get_scope(scope_id)
        runtime = self.runtime(scope_id)

        self._cancel_buffer_timer(runtime)

        if key in scope.terminator_keys:
            code, runtime.buffer = runtime.buffer, ""
            if not code:
                return None
            return await self.async_submit(scope_id, code, source)

        runtime.buffer += key

        if scope.code_length is not None and len(runtime.buffer) >= scope.code_length:
            code, runtime.buffer = runtime.buffer, ""
            return await self.async_submit(scope_id, code, source)

        runtime.cancel_buffer_timer = async_call_later(
            self.hass,
            scope.inter_key_timeout,
            lambda _now: self.async_clear_buffer(scope_id),
        )
        return None

    @callback
    def async_clear_buffer(self, scope_id: str) -> None:
        """Discard a scope's partially entered code."""
        runtime = self.runtime(scope_id)
        self._cancel_buffer_timer(runtime)
        runtime.buffer = ""

    @callback
    def _cancel_buffer_timer(self, runtime: ScopeRuntime) -> None:
        """Cancel a pending inter-key timeout."""
        if runtime.cancel_buffer_timer is not None:
            runtime.cancel_buffer_timer()
            runtime.cancel_buffer_timer = None

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    def is_code_taken(self, code: str) -> bool:
        """Whether any credential already holds this code."""
        return compute_lookup_index(code, self.data.key) in self._index

    def generate(
        self,
        length: int | None = None,
        *,
        code_type: CodeType = CodeType.PIN,
        **kwargs: Any,
    ) -> str:
        """Generate a code that is unused and, unless disabled, not weak."""
        return generate_code(
            length or self.default_code_length,
            code_type=code_type,
            reject_weak=self.reject_weak_codes,
            blocklist=self.weak_code_blocklist,
            is_taken=self.is_code_taken,
            **kwargs,
        )

    def validate_code(self, code: str) -> None:
        """Raise if ``code`` collides or, when enabled, is weak."""
        if self.is_code_taken(code):
            raise CodeCollisionError(
                "That code is already in use. Two identical codes would make the "
                "audit log unattributable."
            )
        if self.reject_weak_codes:
            weakness = find_weakness(code, self.weak_code_blocklist)
            if weakness is not None:
                raise WeakCodeError(
                    f"That code was rejected as weak ({weakness}). Turn off "
                    "'reject weak codes' in the integration settings to allow it."
                )

    # ------------------------------------------------------------------
    # Credential CRUD
    # ------------------------------------------------------------------

    async def async_create_credential(
        self,
        *,
        label: str,
        code: str | None = None,
        scope_ids: list[str] | None = None,
        code_type: CodeType = CodeType.PIN,
        keep_viewable: bool = False,
        owner: str | None = None,
        tags: list[str] | None = None,
        notes: str = "",
        policy: Policy | None = None,
        length: int | None = None,
    ) -> tuple[Credential, str]:
        """Create a credential, generating a code when one was not supplied.

        Returns the credential and the code in clear, so the caller can show it once.
        """
        for scope_id in scope_ids or []:
            self.get_scope(scope_id)

        if code is None:
            code = self.generate(length, code_type=code_type)
        else:
            self.validate_code(code)

        now = dt_util.utcnow()
        credential = Credential(
            credential_id=uuid4().hex,
            label=label,
            lookup_index=compute_lookup_index(code, self.data.key),
            code_type=code_type,
            plaintext=code if keep_viewable else None,
            keep_viewable=keep_viewable,
            owner=owner,
            tags=list(tags or []),
            notes=notes,
            policy=policy or Policy(),
            grants=[Grant(scope_id=sid) for sid in scope_ids or []],
            created_at=now,
            updated_at=now,
        )

        self.data.credentials[credential.credential_id] = credential
        self._index[credential.lookup_index] = credential.credential_id
        self.store.async_schedule_save()
        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)
        return credential, code

    async def async_create_otp(
        self,
        *,
        scope_id: str,
        label: str = "One-time code",
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        duration: timedelta | None = None,
        max_uses: int = 1,
        length: int | None = None,
        keep_viewable: bool = True,
    ) -> tuple[Credential, str]:
        """Create a single-use, time-limited code.

        Defaults to viewable, because a delivery code you cannot read back is of no
        use to the person handing it over.
        """
        now = dt_util.utcnow()
        start = valid_from or now
        if valid_until is None:
            valid_until = start + (duration or timedelta(hours=2))

        return await self.async_create_credential(
            label=label,
            scope_ids=[scope_id],
            keep_viewable=keep_viewable,
            length=length,
            policy=Policy(
                valid_from=valid_from,
                valid_until=valid_until,
                max_uses=max_uses,
            ),
        )

    async def async_update_credential(
        self, credential_id: str, changes: dict[str, Any]
    ) -> Credential:
        """Apply field changes to a credential.

        Changing the code itself goes through ``code``, which is re-validated for
        collisions and weakness like any new code.
        """
        credential = self.get_credential(credential_id)

        # Applied before the code, so setting a new code and making it viewable in the
        # same call stores the plaintext rather than silently dropping it.
        if (keep_viewable := changes.pop("keep_viewable", None)) is not None:
            credential.keep_viewable = keep_viewable
            if not keep_viewable:
                credential.plaintext = None

        if (code := changes.pop("code", None)) is not None:
            if self._lookup(code) is not credential:
                self.validate_code(code)
            self._index.pop(credential.lookup_index, None)
            credential.lookup_index = compute_lookup_index(code, self.data.key)
            self._index[credential.lookup_index] = credential_id
            if credential.keep_viewable:
                credential.plaintext = code

        if (scope_ids := changes.pop("scope_ids", None)) is not None:
            existing = {g.scope_id: g for g in credential.grants}
            credential.grants = [
                existing.get(sid, Grant(scope_id=sid)) for sid in scope_ids
            ]

        if (raw_policy := changes.pop("policy", None)) is not None:
            credential.policy = Policy.from_dict(raw_policy)

        for key, value in changes.items():
            if hasattr(credential, key):
                setattr(credential, key, value)

        # The credential's device carries its label, so a rename has to follow through
        # or the entity names go stale.
        if changes.get("label") and (
            device := self._async_credential_device(credential_id)
        ):
            dr.async_get(self.hass).async_update_device(
                device.id, name=credential.label
            )

        credential.updated_at = dt_util.utcnow()
        self.store.async_schedule_save()
        async_dispatcher_send(
            self.hass, SIGNAL_CREDENTIAL_UPDATED.format(credential_id)
        )
        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)
        return credential

    async def async_delete_credential(self, credential_id: str) -> None:
        """Remove a credential, its device and therefore its entities."""
        credential = self.get_credential(credential_id)
        self._index.pop(credential.lookup_index, None)
        del self.data.credentials[credential_id]

        if device := self._async_credential_device(credential_id):
            dr.async_get(self.hass).async_remove_device(device.id)

        self.store.async_schedule_save()
        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)

    @callback
    def _async_credential_device(self, credential_id: str) -> dr.DeviceEntry | None:
        """Look up a credential's device."""
        return dr.async_get(self.hass).async_get_device_by_identifier(
            credential_device_identifier(credential_id), self.entry.entry_id
        )

    async def async_revoke(self, credential_id: str) -> None:
        """Mark a credential permanently unusable."""
        await self.async_update_credential(credential_id, {"revoked": True})

    async def async_set_enabled(self, credential_id: str, enabled: bool) -> None:
        """Temporarily enable or disable a credential."""
        await self.async_update_credential(credential_id, {"enabled": enabled})

    async def async_revoke_all(
        self, *, scope_id: str | None = None, tags: list[str] | None = None
    ) -> int:
        """Revoke every matching credential. Returns how many were affected."""
        revoked = 0
        for credential in list(self.data.credentials.values()):
            if credential.revoked:
                continue
            if scope_id is not None and credential.grant_for(scope_id) is None:
                continue
            if tags and not set(tags) & set(credential.tags):
                continue
            credential.revoked = True
            credential.updated_at = dt_util.utcnow()
            revoked += 1
            async_dispatcher_send(
                self.hass, SIGNAL_CREDENTIAL_UPDATED.format(credential.credential_id)
            )

        if revoked:
            self.store.async_schedule_save()
            async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)
        return revoked

    # ------------------------------------------------------------------
    # Scope CRUD
    # ------------------------------------------------------------------

    async def async_create_scope(self, **kwargs: Any) -> Scope:
        """Add a scope and let the platforms create its entities."""
        scope = Scope(scope_id=uuid4().hex, **kwargs)
        self.data.scopes[scope.scope_id] = scope
        self._runtime[scope.scope_id] = ScopeRuntime()
        self.store.async_schedule_save()
        async_dispatcher_send(self.hass, SIGNAL_SCOPES_CHANGED)
        return scope

    async def async_update_scope(self, scope_id: str, changes: dict[str, Any]) -> Scope:
        """Apply field changes to a scope."""
        scope = self.get_scope(scope_id)
        for key, value in changes.items():
            if hasattr(scope, key):
                setattr(scope, key, value)
        # Force the cached action script to be rebuilt on next use.
        self.runtime(scope_id).script = None
        self.store.async_schedule_save()
        async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope_id))
        return scope

    async def async_delete_scope(self, scope_id: str) -> None:
        """Remove a scope, its device and every grant pointing at it."""
        self.get_scope(scope_id)
        del self.data.scopes[scope_id]
        self._runtime.pop(scope_id, None)

        # Removing the device cascades to the scope's entities.
        if device := self._async_scope_device(scope_id):
            dr.async_get(self.hass).async_remove_device(device.id)

        for credential in self.data.credentials.values():
            credential.grants = [g for g in credential.grants if g.scope_id != scope_id]
        self.store.async_schedule_save()
        async_dispatcher_send(self.hass, SIGNAL_SCOPES_CHANGED)
        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)

    @callback
    def async_update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Merge integration-level settings into the config entry's options.

        Updating the entry fires its update listener, which reloads the integration --
        needed because settings like ``per_credential_entities`` change which entities
        should exist.
        """
        options = {**self.entry.options, **changes}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        return options

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def credentials_for_scope(self, scope_id: str) -> list[Credential]:
        """Every credential granted on a scope."""
        return [
            credential
            for credential in self.data.credentials.values()
            if credential.grant_for(scope_id) is not None
        ]

    def is_currently_valid(self, credential_id: str, scope_id: str) -> bool:
        """Whether a credential would be accepted on a scope right now."""
        credential = self.data.credentials.get(credential_id)
        if credential is None:
            return False
        return (
            evaluate(
                self.hass, credential, scope_id, Source.UNKNOWN, dt_util.utcnow()
            )
            is None
        )
