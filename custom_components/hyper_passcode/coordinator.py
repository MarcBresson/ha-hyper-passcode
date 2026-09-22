"""The central manager: submission handling, lockout, buffering and CRUD.

Everything that mutates HyperPasscode state goes through here, so there is exactly one
place where a code is checked, a use is counted and an action is fired.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.script import Script
from homeassistant.util import dt as dt_util

from . import audit
from .const import (
    ATTR_CREDENTIAL_ID,
    ATTR_IN_GRACE_PERIOD,
    ATTR_LABEL,
    ATTR_OUTCOME,
    ATTR_PERSON,
    ATTR_REASON,
    ATTR_SCOPE_ID,
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
    DEFAULT_WEAK_CODE_BLOCKLIST,
    DEVICE_MANUFACTURER,
    DEVICE_MODEL_KEYPAD,
    DEVICE_MODEL_SCOPE,
    DOMAIN,
    EVENT_SUBMISSION,
    REASON_TO_EVENT_TYPE,
    SUBENTRY_TYPE_CREDENTIAL,
    SUBENTRY_TYPE_KEYPAD,
    SUBENTRY_TYPE_SCOPE,
    CodeType,
    EventType,
    Outcome,
    RejectionReason,
    credential_code_unique_id,
    credential_device_identifier,
    keypad_device_identifier,
)
from .crypto import compute_lookup_index, find_weakness, generate_code, verify
from .exceptions import (
    CodeCollisionError,
    UnknownCredentialError,
    UnknownKeypadError,
    UnknownScopeError,
    WeakCodeError,
)
from .models import AuditEntry, Credential, Grant, Keypad, Policy, Scope
from .policy import evaluate, is_within_grace
from .store import HyperPasscodeStore, StoredData

_LOGGER = logging.getLogger(__name__)

#: Dispatcher signals. Entities subscribe rather than polling.
SIGNAL_SUBMISSION = f"{DOMAIN}_submission_{{}}"
SIGNAL_SCOPES_CHANGED = f"{DOMAIN}_scopes_changed"
SIGNAL_CREDENTIALS_CHANGED = f"{DOMAIN}_credentials_changed"
SIGNAL_KEYPADS_CHANGED = f"{DOMAIN}_keypads_changed"
SIGNAL_CREDENTIAL_UPDATED = f"{DOMAIN}_credential_updated_{{}}"
SIGNAL_SCOPE_UPDATED = f"{DOMAIN}_scope_updated_{{}}"
SIGNAL_KEYPAD_UPDATED = f"{DOMAIN}_keypad_updated_{{}}"

#: The recorder, and the service on it that deletes one entity's recorded history.
RECORDER_DOMAIN = "recorder"
SERVICE_PURGE_ENTITIES = "purge_entities"

#: Repair issue raised when a code's history could not be deleted after all. One per
#: entity, so two codes that both fail are two separate warnings.
ISSUE_TRANSLATION_KEY = "code_history_not_purged"
ISSUE_CODE_HISTORY = f"{ISSUE_TRANSLATION_KEY}_{{}}"


@dataclass
class SubmissionResult:
    """The outcome of one code submission."""

    valid: bool
    scope_id: str
    reason: RejectionReason | None = None
    credential_id: str | None = None
    label: str | None = None
    person: str | None = None
    #: Whether this use fell inside the credential's re-entry grace period, and so is
    #: exempt from ``max_uses``. Decided while evaluating and carried here rather than
    #: worked out again when the use is recorded, so the two cannot drift apart: one
    #: instant, one effective policy, one answer.
    in_grace: bool = False

    @property
    def accepted_in_grace(self) -> bool:
        """Whether an *accepted* use fell inside the re-entry grace period.

        ``in_grace`` on its own only says the window was still open, which is asked of
        every code that was recognised, refused ones included. The activity surfaces
        want the narrower question: a use that never happened was excused from nothing,
        and reporting it as graced would read as though the code had been let in.
        """
        return self.valid and self.in_grace

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
            "in_grace_period": self.in_grace,
        }


@dataclass
class ScopeRuntime:
    """Per-scope state that lives only in memory.

    Lockout deliberately does not survive a restart: a reboot is a plausible recovery
    path for a locked-out household, and persisting it would mostly serve to lock
    people out for longer than intended. The escalation streak follows the same rule:
    it lives here rather than in the store, and resets on any successful submission
    just like ``failed_attempts`` does.

    The last-test fields are what the scope's "Last test" sensor reads. They are
    here rather than in the store for the same reason: a verdict is a live reading,
    and the audit log already keeps the history of everything that was not a dry run.
    """

    failed_attempts: int = 0
    locked_until: datetime | None = None
    #: Consecutive lockouts tripped since the last successful submission.
    lockout_streak: int = 0
    last_used: datetime | None = None
    last_label: str | None = None
    last_credential_id: str | None = None
    last_in_grace: bool = False
    last_test: SubmissionResult | None = None
    last_test_at: datetime | None = None
    last_test_dry_run: bool = False
    script: Script | None = None
    script_source: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class KeypadRuntime:
    """Per-keypad state that lives only in memory: the in-progress keystroke buffer."""

    buffer: str = ""
    cancel_buffer_timer: CALLBACK_TYPE | None = None


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
        self._keypad_runtime: dict[str, KeypadRuntime] = {}
        self._scopes: dict[str, Scope] = {}
        self._keypads: dict[str, Keypad] = {}
        self._credentials: dict[str, Credential] = {}
        self._known_options: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load persisted data and rebuild in-memory state."""
        await self.store.async_load()
        self._known_options = dict(self.entry.options)
        self._rebuild_scopes()
        self._rebuild_keypads()
        self._rebuild_credentials()
        self._rebuild_index()
        self._rebuild_runtime()

    def _rebuild_credentials(self) -> None:
        """Join each credential's subentry to its stored secret.

        Secrets left behind by a half-finished add are dropped here rather than
        lingering: without a subentry there is nothing that could use them.
        """
        seen: set[str] = set()
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_CREDENTIAL:
                continue
            config = dict(subentry.data)
            credential_id = config["credential_id"]
            seen.add(credential_id)
            fresh = Credential.assemble(
                subentry.title, config, self.data.secrets.get(credential_id, {})
            )
            if (existing := self._credentials.get(credential_id)) is None:
                self._credentials[credential_id] = fresh
            else:
                existing.update_from(fresh)

        for credential_id in set(self._credentials) - seen:
            del self._credentials[credential_id]

        orphaned = set(self.data.secrets) - set(self._credentials)
        for credential_id in orphaned:
            del self.data.secrets[credential_id]
        if orphaned:
            self.store.async_schedule_save()

    @callback
    def async_options_changed(self) -> bool:
        """Whether the entry's options differ from the ones currently in effect."""
        return dict(self.entry.options) != self._known_options

    @callback
    def async_sync_subentries(self) -> None:
        """Pick up scope and credential changes made through the UI, without a reload.

        Editing either from the integration page updates a subentry, which fires the
        entry's update listener. Reloading there would be simpler, but it would also
        throw away lockout counters and half-typed keypad buffers every time an
        unrelated item was touched, so the change is absorbed in place instead.
        """
        self._sync_credentials()
        self._sync_scopes()
        self._sync_keypads()

    def _sync_credentials(self) -> None:
        """Absorb credential subentry changes, keeping live counters intact."""
        before = set(self._credentials)
        self._rebuild_credentials()
        self._rebuild_index()
        if before != set(self._credentials):
            async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)
        for credential_id in before & set(self._credentials):
            async_dispatcher_send(
                self.hass, SIGNAL_CREDENTIAL_UPDATED.format(credential_id)
            )

    def _sync_scopes(self) -> None:
        """Absorb scope subentry changes, keeping lockout state and buffers intact."""
        before = set(self._scopes)
        self._rebuild_scopes()
        after = set(self._scopes)

        for scope_id in after - before:
            self._runtime.setdefault(scope_id, ScopeRuntime())
        for scope_id in before - after:
            self._runtime.pop(scope_id, None)

        if after - before:
            # Before the signal below adds the new scope's entities, so a code added
            # to it straight afterwards already has a device to hang off.
            self.async_register_scope_devices()

        for scope_id in after & before:
            # The cached action script may no longer match the scope's config.
            self.runtime(scope_id).script = None
            async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope_id))

        if before != after:
            async_dispatcher_send(self.hass, SIGNAL_SCOPES_CHANGED)

    def _sync_keypads(self) -> None:
        """Absorb keypad subentry changes, keeping in-progress buffers intact."""
        before = set(self._keypads)
        self._rebuild_keypads()
        after = set(self._keypads)

        for keypad_id in after - before:
            self._keypad_runtime.setdefault(keypad_id, KeypadRuntime())
        for keypad_id in before - after:
            self._keypad_runtime.pop(keypad_id, None)

        if after - before:
            # Before the signal below adds the new keypad's entities, so it already
            # has a device to hang off.
            self.async_register_keypad_devices()

        for keypad_id in after & before:
            async_dispatcher_send(self.hass, SIGNAL_KEYPAD_UPDATED.format(keypad_id))

        if before != after:
            async_dispatcher_send(self.hass, SIGNAL_KEYPADS_CHANGED)

    def _rebuild_scopes(self) -> None:
        """Read the scopes back out of the config entry's subentries.

        Scopes are configuration, so they live as subentries rather than in the
        store: that is what gives them an "Add scope" button on the integration
        page and a per-scope configure dialog. Any change to them reloads the entry,
        which lands back here.
        """
        self._scopes = {}
        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_SCOPE:
                continue
            scope = Scope.from_dict(
                {**subentry.data, "scope_id": subentry_id, "name": subentry.title}
            )
            stats = self.data.scope_stats.get(subentry_id, {})
            scope.valid_submissions = stats.get("valid_submissions", 0)
            scope.invalid_submissions = stats.get("invalid_submissions", 0)
            self._scopes[subentry_id] = scope

        orphaned = set(self.data.scope_stats) - set(self._scopes)
        for scope_id in orphaned:
            del self.data.scope_stats[scope_id]
        if orphaned:
            self.store.async_schedule_save()

    def _rebuild_keypads(self) -> None:
        """Read the keypad buffers back out of the config entry's subentries."""
        self._keypads = {
            subentry_id: Keypad.from_dict(
                {**subentry.data, "keypad_id": subentry_id, "name": subentry.title}
            )
            for subentry_id, subentry in self.entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_KEYPAD
        }

    def _rebuild_index(self) -> None:
        """Rebuild the lookup-index to credential-id map."""
        self._index = {
            credential.lookup_index: credential_id
            for credential_id, credential in self._credentials.items()
        }

    def _rebuild_runtime(self) -> None:
        """Recreate per-scope runtime, recovering last-used from the audit log.

        Deriving last-used from the audit log rather than persisting it separately
        keeps a single source of truth.
        """
        self._runtime = {scope_id: ScopeRuntime() for scope_id in self._scopes}
        for entry in self.data.audit:
            if entry.outcome is not Outcome.VALID:
                continue
            runtime = self._runtime.get(entry.scope_id)
            if runtime is None:
                continue
            if runtime.last_used is None or entry.timestamp > runtime.last_used:
                runtime.last_used = entry.timestamp
                runtime.last_label = entry.label
                runtime.last_credential_id = entry.credential_id
                runtime.last_in_grace = entry.in_grace

    @callback
    def async_shutdown(self) -> None:
        """Cancel any pending keystroke timers."""
        for keypad_runtime in self._keypad_runtime.values():
            if keypad_runtime.cancel_buffer_timer is not None:
                keypad_runtime.cancel_buffer_timer()
                keypad_runtime.cancel_buffer_timer = None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def data(self) -> StoredData:
        """The loaded dataset."""
        return self.store.data

    @property
    def scopes(self) -> dict[str, Scope]:
        """Configured scopes by id, which is also their subentry id."""
        return self._scopes

    @property
    def credentials(self) -> dict[str, Credential]:
        """Configured credentials by id, assembled from subentry and store."""
        return self._credentials

    @property
    def keypads(self) -> dict[str, Keypad]:
        """Configured keypad buffers by id, which is also their subentry id."""
        return self._keypads

    def get_scope(self, scope_id: str) -> Scope:
        """Return a scope or raise."""
        try:
            return self._scopes[scope_id]
        except KeyError:
            raise UnknownScopeError(f"No such scope: {scope_id}") from None

    def get_credential(self, credential_id: str) -> Credential:
        """Return a credential or raise."""
        try:
            return self._credentials[credential_id]
        except KeyError:
            raise UnknownCredentialError(
                f"No such credential: {credential_id}"
            ) from None

    def get_keypad(self, keypad_id: str) -> Keypad:
        """Return a keypad buffer or raise."""
        try:
            return self._keypads[keypad_id]
        except KeyError:
            raise UnknownKeypadError(f"No such keypad: {keypad_id}") from None

    def runtime(self, scope_id: str) -> ScopeRuntime:
        """Return (creating if needed) the runtime state for a scope."""
        return self._runtime.setdefault(scope_id, ScopeRuntime())

    def keypad_runtime(self, keypad_id: str) -> KeypadRuntime:
        """Return (creating if needed) the runtime state for a keypad buffer."""
        return self._keypad_runtime.setdefault(keypad_id, KeypadRuntime())

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
        *,
        dry_run: bool = False,
        context: Context | None = None,
    ) -> SubmissionResult:
        """Validate ``code`` against ``scope_id`` and act on the outcome.

        With ``dry_run`` the code is evaluated but no use is counted, no failure is
        registered, no audit row is written and no action runs -- this backs the
        ``test_code`` service and the "Test a code" page.

        The one thing a dry run does leave behind is the verdict, on the scope's
        "Last test" sensor. Without it a test would leave no trace whatsoever, and
        a surface that evaluates codes without counting failures or tripping the
        lockout is an unrate-limited guessing oracle. That sensor's history is what
        makes somebody working through the code space visible.
        """
        scope = self.get_scope(scope_id)
        now = dt_util.utcnow()

        result = self._evaluate_submission(scope, code, now)

        # Before the branches below: both _register_failure and _reset_failures
        # dispatch the scope update that the sensor reads synchronously, so the
        # verdict has to already be in place by the time they run.
        runtime = self.runtime(scope_id)
        runtime.last_test = result
        runtime.last_test_at = now
        runtime.last_test_dry_run = dry_run

        if dry_run:
            # Nothing else dispatches on this path.
            async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope_id))
            return result

        if result.valid:
            credential = self.get_credential(result.credential_id)  # type: ignore[arg-type]
            credential.record_use(now, counted=not result.in_grace)
            credential.updated_at = now
            # Set before _reset_failures, which dispatches the scope update the
            # last-used sensor reads synchronously.
            runtime.last_used = now
            runtime.last_label = credential.label
            runtime.last_credential_id = credential.credential_id
            runtime.last_in_grace = result.in_grace
            self._reset_failures(scope_id)
            self.async_save_credential(credential)
            async_dispatcher_send(
                self.hass, SIGNAL_CREDENTIAL_UPDATED.format(credential.credential_id)
            )
        else:
            self._register_failure(scope, now)

        scope.record_submission(result.valid)
        self._save_scope_stats(scope)

        self._record_audit(scope_id, code, result, now)
        self._fire_events(scope, result)

        # Actions run last, after the use is counted, so a failing action cannot be
        # retried to burn through a one-time code.
        if result.valid:
            await self._async_run_actions(scope, result, context)

        return result

    def _evaluate_submission(
        self, scope: Scope, code: str, now: datetime
    ) -> SubmissionResult:
        """Resolve a code to a verdict without mutating anything."""
        if self.is_locked_out(scope.scope_id, now):
            return SubmissionResult(
                valid=False,
                scope_id=scope.scope_id,
                reason=RejectionReason.LOCKED_OUT,
            )

        credential = self._lookup(code)
        if credential is None:
            return SubmissionResult(
                valid=False,
                scope_id=scope.scope_id,
                reason=RejectionReason.UNKNOWN_CODE,
            )

        reason = evaluate(self.hass, credential, scope.scope_id, now)
        return SubmissionResult(
            valid=reason is None,
            scope_id=scope.scope_id,
            reason=reason,
            credential_id=credential.credential_id,
            label=credential.label,
            person=credential.owner,
            # policy_for is the same resolution evaluate does internally, so a
            # per-grant override is honoured on both sides by construction. Asked on
            # the dry-run path too: it reads without mutating, and it lets the "Test a
            # code" page say the use would have been free.
            in_grace=is_within_grace(
                credential, credential.policy_for(scope.scope_id), now
            ),
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

        credential = self._credentials.get(credential_id)
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
        threshold = scope.lockout_threshold
        if threshold > 0 and runtime.failed_attempts >= threshold:
            runtime.lockout_streak += 1
            duration = scope.lockout_duration * (
                scope.lockout_backoff_factor ** (runtime.lockout_streak - 1)
            )
            if scope.lockout_max_duration > 0:
                duration = min(duration, scope.lockout_max_duration)
            runtime.locked_until = now + timedelta(seconds=duration)
            runtime.failed_attempts = 0
            _LOGGER.warning(
                "Scope %s locked out until %s after %s failed attempts "
                "(streak %s, duration %ss)",
                scope.name,
                runtime.locked_until,
                threshold,
                runtime.lockout_streak,
                duration,
            )
        async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope.scope_id))

    def _reset_failures(self, scope_id: str) -> None:
        """Clear the failure counter and escalation streak after a success."""
        runtime = self.runtime(scope_id)
        runtime.failed_attempts = 0
        runtime.locked_until = None
        runtime.lockout_streak = 0
        async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope_id))

    def _save_scope_stats(self, scope: Scope) -> None:
        """Persist a scope's lifetime submission counters."""
        self.data.scope_stats[scope.scope_id] = scope.stats_dict()
        self.store.async_schedule_save()

    def _record_audit(
        self, scope_id: str, code: str, result: SubmissionResult, now: datetime
    ) -> None:
        """Append the submission to the audit ring buffer."""
        entry = AuditEntry(
            timestamp=now,
            scope_id=scope_id,
            outcome=Outcome.VALID if result.valid else Outcome.INVALID,
            credential_id=result.credential_id,
            label=result.label,
            person=result.person,
            reason=str(result.reason) if result.reason else None,
            typed=code if (not result.valid and self.log_failed_plaintext) else None,
            in_grace=result.accepted_in_grace,
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
                ATTR_OUTCOME: str(Outcome.VALID if result.valid else Outcome.INVALID),
                "event_type": str(result.event_type),
                ATTR_REASON: str(result.reason) if result.reason else None,
                ATTR_CREDENTIAL_ID: result.credential_id,
                ATTR_LABEL: result.label,
                ATTR_PERSON: result.person,
                ATTR_IN_GRACE_PERIOD: result.accepted_in_grace,
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

        try:
            await runtime.script.async_run(
                {
                    ATTR_SCOPE_ID: scope.scope_id,
                    "scope_name": scope.name,
                    ATTR_CREDENTIAL_ID: result.credential_id,
                    ATTR_LABEL: result.label,
                    ATTR_PERSON: result.person,
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

    @callback
    def async_register_scope_devices(self) -> None:
        """Register every scope's device before any entity is added.

        A code's device hangs off its scope's through ``via_device_id``, and Home
        Assistant refuses a link to a device that is not registered yet. Entity
        platforms are set up concurrently, so letting a scope's own entities create
        its device first would be a race; creating them here removes it.
        """
        registry = dr.async_get(self.hass)
        for scope_id, scope in self._scopes.items():
            registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                config_subentry_id=scope_id,
                identifiers={(DOMAIN, scope_id)},
                name=scope.name,
                manufacturer=DEVICE_MANUFACTURER,
                model=DEVICE_MODEL_SCOPE,
            )

    @callback
    def _async_keypad_device(self, keypad_id: str) -> dr.DeviceEntry | None:
        """Look up a keypad buffer's device."""
        return dr.async_get(self.hass).async_get_device_by_identifier(
            keypad_device_identifier(keypad_id), self.entry.entry_id
        )

    @callback
    def async_register_keypad_devices(self) -> None:
        """Register every keypad buffer's device.

        Nested under its target scope's device via ``via_device_id``, which must
        already be registered -- called after ``async_register_scope_devices``.
        """
        registry = dr.async_get(self.hass)
        for keypad_id, keypad in self._keypads.items():
            registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                config_subentry_id=keypad_id,
                identifiers={keypad_device_identifier(keypad_id)},
                name=keypad.name,
                manufacturer=DEVICE_MANUFACTURER,
                model=DEVICE_MODEL_KEYPAD,
                via_device_id=self.async_device_id(keypad.scope_id),
            )

    @callback
    def async_sync_keypad_devices(self) -> None:
        """Re-parent keypad devices whose target scope has changed.

        ``device_info`` is read once, when an entity is added, so a keypad
        reconfigured to target a different scope would otherwise keep the place in
        the tree it had when it was created.
        """
        registry = dr.async_get(self.hass)
        for keypad_id, keypad in self._keypads.items():
            device = registry.async_get_device_by_identifier(
                keypad_device_identifier(keypad_id), self.entry.entry_id
            )
            if device is None:
                continue
            via_device_id = self.async_device_id(keypad.scope_id)
            if device.via_device_id != via_device_id:
                registry.async_update_device(device.id, via_device_id=via_device_id)

    @callback
    def async_credential_via_device_id(self, credential: Credential) -> str | None:
        """Return the scope device a credential's device should sit under.

        Only a code granted on exactly one scope is nested. With two there is no
        single parent -- grants are many-to-many and a device tree cannot say so --
        and picking one of them would hide the rest, so such a code stays top level.
        """
        scope_ids = [
            grant.scope_id
            for grant in credential.grants
            if grant.scope_id in self._scopes
        ]
        if len(scope_ids) != 1:
            return None
        return self.async_device_id(scope_ids[0])

    @callback
    def async_sync_credential_devices(self) -> None:
        """Re-parent the credential devices whose grants have changed.

        ``device_info`` is read once, when an entity is added, so a code granted a
        second scope -- or moved to another one -- would otherwise keep the place in
        the tree it had when it was created.
        """
        registry = dr.async_get(self.hass)
        for credential in self._credentials.values():
            device = registry.async_get_device_by_identifier(
                credential_device_identifier(credential.credential_id),
                self.entry.entry_id,
            )
            if device is None:
                continue
            via_device_id = self.async_credential_via_device_id(credential)
            if device.via_device_id != via_device_id:
                registry.async_update_device(device.id, via_device_id=via_device_id)

    # ------------------------------------------------------------------
    # Keystroke buffering
    # ------------------------------------------------------------------

    async def async_submit_key(
        self, keypad_id: str, key: str
    ) -> SubmissionResult | None:
        """Feed one keystroke into a keypad buffer.

        Physical keypads emit one event per key, so the buffer submits -- against the
        keypad's target scope -- when it sees a terminator key, when it reaches the
        keypad's fixed code length, and clears itself after the inter-key timeout.
        """
        keypad = self.get_keypad(keypad_id)
        runtime = self.keypad_runtime(keypad_id)

        self._cancel_buffer_timer(runtime)

        if key in keypad.terminator_keys:
            code, runtime.buffer = runtime.buffer, ""
            if not code:
                return None
            return await self.async_submit(keypad.scope_id, code)

        runtime.buffer += key

        if keypad.code_length is not None and len(runtime.buffer) >= keypad.code_length:
            code, runtime.buffer = runtime.buffer, ""
            return await self.async_submit(keypad.scope_id, code)

        runtime.cancel_buffer_timer = async_call_later(
            self.hass,
            keypad.inter_key_timeout,
            lambda _now: self.async_clear_buffer(keypad_id),
        )
        return None

    @callback
    def async_clear_buffer(self, keypad_id: str) -> None:
        """Discard a keypad's partially entered code."""
        runtime = self.keypad_runtime(keypad_id)
        self._cancel_buffer_timer(runtime)
        runtime.buffer = ""

    @callback
    def _cancel_buffer_timer(self, runtime: KeypadRuntime) -> None:
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
        notes: str = "",
        policy: Policy | None = None,
        length: int | None = None,
        persist: bool = True,
    ) -> tuple[Credential, str]:
        """Create a credential, generating a code when one was not supplied.

        Returns the credential and the code in clear, so the caller can show it once.

        ``persist=False`` builds the credential without writing it anywhere, which is
        what lets the add dialog show the generated code on a confirmation step and
        only commit once the user has seen it.
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
            notes=notes,
            policy=policy or Policy(),
            grants=[Grant(scope_id=sid) for sid in scope_ids or []],
            created_at=now,
            updated_at=now,
        )

        if persist:
            self.async_persist_credential(credential)
        return credential, code

    @callback
    def async_stash_secret(self, credential: Credential) -> None:
        """Write a credential's secret half to the private store, and nothing else.

        The add dialog calls this and then lets Home Assistant create the subentry as
        the flow finishes. The credential deliberately does not appear in
        ``credentials`` yet: it is the subentry appearing that makes it real, and
        that is what triggers its entities being created.
        """
        self.data.secrets[credential.credential_id] = credential.secret_dict()
        self.store.async_schedule_save()

    @callback
    def async_persist_credential(self, credential: Credential) -> None:
        """Write a new credential to both halves of its storage.

        The secret goes to the private store first, so a credential is never visible
        as a subentry without one.
        """
        # Registered before the rebuild so that rebuild updates this very object
        # rather than replacing it -- the caller is holding it.
        self._credentials[credential.credential_id] = credential
        self.async_stash_secret(credential)
        self.hass.config_entries.async_add_subentry(
            self.entry,
            ConfigSubentry(
                data=MappingProxyType(credential.config_dict()),
                subentry_id=credential.credential_id,
                subentry_type=SUBENTRY_TYPE_CREDENTIAL,
                title=credential.label,
                unique_id=None,
            ),
        )
        # Reflect it at once rather than waiting for the entry's update listener, so
        # a caller can use the credential on the very next line.
        self._rebuild_credentials()
        self._rebuild_index()
        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)

    @callback
    def async_credential_subentry(self, credential_id: str) -> ConfigSubentry | None:
        """Find a credential's subentry.

        Matched on the ``credential_id`` inside the subentry's data rather than on the
        subentry id, because a credential added through the dialog gets an id Home
        Assistant chose, while one added through an action gets ours.
        """
        for subentry in self.entry.subentries.values():
            if (
                subentry.subentry_type == SUBENTRY_TYPE_CREDENTIAL
                and subentry.data.get("credential_id") == credential_id
            ):
                return subentry
        return None

    @callback
    def async_credential_subentry_id(self, credential_id: str) -> str | None:
        """Return the subentry id a credential's entities attach to."""
        subentry = self.async_credential_subentry(credential_id)
        return subentry.subentry_id if subentry else None

    async def async_create_otp(
        self,
        *,
        scope_id: str,
        label: str = "One-time code",
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        duration: timedelta | None = None,
        max_uses: int = 1,
        grace_period_seconds: int | None = None,
        length: int | None = None,
        keep_viewable: bool = True,
    ) -> tuple[Credential, str]:
        """Create a single-use, time-limited code.

        Defaults to viewable, because a delivery code you cannot read back is of no
        use to the person handing it over.

        ``grace_period_seconds`` is the one-time code's answer to the driver who has
        to come straight back out through the door. The window it opens is always a
        fixed one: a sliding window puts no upper bound on how long a single-use code
        stays alive, which is not something this helper should be able to hand out.
        Anyone wanting that can switch the code's own mode afterwards.
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
                grace_period_seconds=grace_period_seconds,
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
        discarded = False
        if (keep_viewable := changes.pop("keep_viewable", None)) is not None:
            discarded = credential.keep_viewable and not keep_viewable
            credential.keep_viewable = keep_viewable
            if not keep_viewable:
                credential.plaintext = None
        # Read while the entity is certain to be registered, and before the purge,
        # which only happens once the sensor has been told to drop the code.
        code_entity_id = (
            self._async_code_entity_id(credential_id) if discarded else None
        )

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
        self.async_save_credential(credential)
        async_dispatcher_send(
            self.hass, SIGNAL_CREDENTIAL_UPDATED.format(credential_id)
        )
        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)

        # After the dispatch, so the sensor has already written its code away and the
        # purge does not leave the last clear state behind as the newest row.
        if discarded:
            await self.async_purge_code_history(code_entity_id, credential.label)
        return credential

    @callback
    def async_save_credential(self, credential: Credential) -> None:
        """Write an existing credential back to both halves of its storage."""
        self.data.secrets[credential.credential_id] = credential.secret_dict()
        self.store.async_schedule_save()

        subentry = self.async_credential_subentry(credential.credential_id)
        if subentry is None:
            return

        config = credential.config_dict()
        # Only touch the config entry when the configuration actually changed; a use
        # being recorded must not rewrite it.
        if dict(subentry.data) != config or subentry.title != credential.label:
            self.hass.config_entries.async_update_subentry(
                self.entry, subentry, data=config, title=credential.label
            )

    async def async_delete_credential(self, credential_id: str) -> None:
        """Remove a credential, its secret, its entities and its recorded code."""
        credential = self.get_credential(credential_id)
        # Both read before the subentry goes, which takes the registry entry with it.
        label = credential.label
        code_entity_id = self._async_code_entity_id(credential_id)
        self._index.pop(credential.lookup_index, None)
        self._credentials.pop(credential_id, None)
        self.data.secrets.pop(credential_id, None)
        self.store.async_schedule_save()

        if (subentry := self.async_credential_subentry(credential_id)) is not None:
            # Removing the subentry takes its device and entities with it.
            self.hass.config_entries.async_remove_subentry(
                self.entry, subentry.subentry_id
            )
        elif device := self._async_credential_device(credential_id):
            dr.async_get(self.hass).async_remove_device(device.id)

        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)
        # Unconditionally: a code that is hashed now may well have been viewable
        # earlier, and that history is still the code in clear.
        await self.async_purge_code_history(code_entity_id, label)

    @callback
    def _async_code_entity_id(self, credential_id: str) -> str | None:
        """Return a credential's code sensor id, while it still has one.

        Resolved separately from the purge because deleting a credential takes its
        subentry, and with it the registry entry, before there is anything to purge.
        The id has to be read while it is still there.
        """
        return er.async_get(self.hass).async_get_entity_id(
            Platform.SENSOR, DOMAIN, credential_code_unique_id(credential_id)
        )

    async def async_purge_code_history(self, entity_id: str | None, label: str) -> None:
        """Delete the recorded history of a credential's code sensor.

        Discarding the stored copy of a code empties ``plaintext``, but the recorder
        has been keeping every state that sensor ever had, so without this the code
        would sit in the database for up to ``purge_keep_days`` after the user asked
        for it to be gone. Deleting that entity's history whole is the right scope:
        every row in it is a copy of the code.

        Nothing to do when the sensor was never registered -- with
        ``per_credential_entities`` off there is no entity, and so nothing recorded --
        or when there is no recorder to have recorded it. Anything else that stops the
        purge is reported, because the alternative is telling the user the code is
        gone when it is not.
        """
        if entity_id is None or RECORDER_DOMAIN not in self.hass.config.components:
            return

        issue_id = ISSUE_CODE_HISTORY.format(entity_id)
        try:
            await self.hass.services.async_call(
                RECORDER_DOMAIN,
                SERVICE_PURGE_ENTITIES,
                {"entity_id": [entity_id], "keep_days": 0},
                blocking=True,
            )
        except Exception:
            _LOGGER.exception(
                "Could not purge the recorded history of %s; the code for %s may "
                "remain in the recorder database",
                entity_id,
                label,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_TRANSLATION_KEY,
                translation_placeholders={"label": label, "entity_id": entity_id},
            )
        else:
            # A later attempt that works clears a warning from an earlier one.
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

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

    async def async_revoke_all(self, *, scope_id: str | None = None) -> int:
        """Revoke every matching credential. Returns how many were affected."""
        revoked = 0
        for credential in list(self._credentials.values()):
            if credential.revoked:
                continue
            if scope_id is not None and credential.grant_for(scope_id) is None:
                continue
            credential.revoked = True
            credential.updated_at = dt_util.utcnow()
            self.async_save_credential(credential)
            revoked += 1
            async_dispatcher_send(
                self.hass, SIGNAL_CREDENTIAL_UPDATED.format(credential.credential_id)
            )

        if revoked:
            async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)
        return revoked

    # ------------------------------------------------------------------
    # Scope CRUD
    # ------------------------------------------------------------------

    # Each of these mutates the config entry's subentries, which fires the entry's
    # update listener and reloads the integration. The reload is what rebuilds the
    # scope list and recreates entities, so none of them touch entities directly.

    async def async_create_scope(self, **kwargs: Any) -> Scope:
        """Add a scope as a config subentry."""
        scope = Scope(scope_id=uuid4().hex, **kwargs)
        data = scope.to_dict()
        name = data.pop("name")
        data.pop("scope_id")

        self.hass.config_entries.async_add_subentry(
            self.entry,
            ConfigSubentry(
                data=MappingProxyType(data),
                subentry_id=scope.scope_id,
                subentry_type=SUBENTRY_TYPE_SCOPE,
                title=name,
                unique_id=None,
            ),
        )
        # Reflect it at once, so a caller can use the scope immediately.
        self._scopes[scope.scope_id] = scope
        self._runtime.setdefault(scope.scope_id, ScopeRuntime())
        self.async_register_scope_devices()
        async_dispatcher_send(self.hass, SIGNAL_SCOPES_CHANGED)
        return scope

    async def async_update_scope(self, scope_id: str, changes: dict[str, Any]) -> Scope:
        """Apply field changes to a scope's subentry."""
        scope = self.get_scope(scope_id)
        for key, value in changes.items():
            if hasattr(scope, key):
                setattr(scope, key, value)

        data = scope.to_dict()
        name = data.pop("name")
        data.pop("scope_id")

        self.hass.config_entries.async_update_subentry(
            self.entry,
            self.entry.subentries[scope_id],
            data=data,
            title=name,
        )
        # Force the cached action script to be rebuilt on next use.
        self.runtime(scope_id).script = None
        async_dispatcher_send(self.hass, SIGNAL_SCOPE_UPDATED.format(scope_id))
        return scope

    async def async_delete_scope(self, scope_id: str) -> None:
        """Remove a scope and every grant pointing at it.

        Home Assistant removes the subentry's devices and entities for us.
        """
        self.get_scope(scope_id)
        self.hass.config_entries.async_remove_subentry(self.entry, scope_id)
        self._scopes.pop(scope_id, None)
        self._runtime.pop(scope_id, None)
        async_dispatcher_send(self.hass, SIGNAL_SCOPES_CHANGED)

        for credential in self._credentials.values():
            if credential.grant_for(scope_id) is None:
                continue
            credential.grants = [g for g in credential.grants if g.scope_id != scope_id]
            self.async_save_credential(credential)
        async_dispatcher_send(self.hass, SIGNAL_CREDENTIALS_CHANGED)

    # ------------------------------------------------------------------
    # Keypad buffer CRUD
    # ------------------------------------------------------------------

    async def async_create_keypad(self, **kwargs: Any) -> Keypad:
        """Add a keypad buffer as a config subentry."""
        self.get_scope(kwargs["scope_id"])
        keypad = Keypad(keypad_id=uuid4().hex, **kwargs)
        data = keypad.to_dict()
        name = data.pop("name")
        data.pop("keypad_id")

        self.hass.config_entries.async_add_subentry(
            self.entry,
            ConfigSubentry(
                data=MappingProxyType(data),
                subentry_id=keypad.keypad_id,
                subentry_type=SUBENTRY_TYPE_KEYPAD,
                title=name,
                unique_id=None,
            ),
        )
        # Reflect it at once, so a caller can use the keypad immediately.
        self._keypads[keypad.keypad_id] = keypad
        self._keypad_runtime.setdefault(keypad.keypad_id, KeypadRuntime())
        self.async_register_keypad_devices()
        async_dispatcher_send(self.hass, SIGNAL_KEYPADS_CHANGED)
        return keypad

    async def async_update_keypad(
        self, keypad_id: str, changes: dict[str, Any]
    ) -> Keypad:
        """Apply field changes to a keypad buffer's subentry."""
        keypad = self.get_keypad(keypad_id)
        if (scope_id := changes.get("scope_id")) is not None:
            self.get_scope(scope_id)
        for key, value in changes.items():
            if hasattr(keypad, key):
                setattr(keypad, key, value)

        data = keypad.to_dict()
        name = data.pop("name")
        data.pop("keypad_id")

        self.hass.config_entries.async_update_subentry(
            self.entry,
            self.entry.subentries[keypad_id],
            data=data,
            title=name,
        )
        async_dispatcher_send(self.hass, SIGNAL_KEYPAD_UPDATED.format(keypad_id))
        self.async_sync_keypad_devices()
        return keypad

    async def async_delete_keypad(self, keypad_id: str) -> None:
        """Remove a keypad buffer.

        Home Assistant removes the subentry's device and entities for us.
        """
        self.get_keypad(keypad_id)
        self.hass.config_entries.async_remove_subentry(self.entry, keypad_id)
        self._keypads.pop(keypad_id, None)
        self._keypad_runtime.pop(keypad_id, None)
        async_dispatcher_send(self.hass, SIGNAL_KEYPADS_CHANGED)

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
            for credential in self._credentials.values()
            if credential.grant_for(scope_id) is not None
        ]

    def is_currently_valid(self, credential_id: str, scope_id: str) -> bool:
        """Whether a credential would be accepted on a scope right now."""
        credential = self._credentials.get(credential_id)
        if credential is None:
            return False
        return evaluate(self.hass, credential, scope_id, dt_util.utcnow()) is None
