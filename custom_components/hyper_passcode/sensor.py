"""Sensors: per-scope activity, and per-credential use counts and code readback."""

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HyperPasscodeConfigEntry
from .const import (
    ATTR_CREDENTIAL_ID,
    ATTR_IN_GRACE_PERIOD,
    ATTR_LABEL,
    ATTR_PERSON,
    ATTR_REASON,
    ATTR_SOURCE,
    Outcome,
    RejectionReason,
    StoreMethod,
    credential_code_unique_id,
)
from .coordinator import HyperPasscodeCoordinator
from .entity import (
    HyperPasscodeCredentialEntity,
    HyperPasscodeScopeEntity,
    async_add_credential_entities,
    async_add_scope_entities,
)
from .models import Credential, Scope

#: Every state the last-result sensor can report. An enum sensor raises on anything
#: outside its options, so this has to stay in step with ``RejectionReason``.
RESULT_STATES: list[str] = [
    str(Outcome.VALID),
    *(str(reason) for reason in RejectionReason),
]

#: The two forms a credential's secret can be held in, for the store-method sensor.
STORE_METHOD_STATES: list[str] = [str(method) for method in StoreMethod]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up scope and credential sensors."""
    coordinator = entry.runtime_data
    async_add_scope_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        [ScopeLastUsedSensor, ScopeLastResultSensor, ScopeFailedAttemptsSensor],
    )
    async_add_credential_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        [
            CredentialUsesSensor,
            CredentialUncountedUsesSensor,
            CredentialLastUsedSensor,
            CredentialLastUncountedUseSensor,
            CredentialCodeSensor,
            CredentialStoreMethodSensor,
        ],
    )


class ScopeLastUsedSensor(SensorEntity, HyperPasscodeScopeEntity):
    """When this scope last accepted a code."""

    _attr_translation_key = "last_used"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_last_used"

    @property
    def native_value(self) -> datetime | None:
        """The timestamp of the last accepted code."""
        return self.coordinator.runtime(self.scope_id).last_used

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Which code it was, and whether the use was free.

        The label is what a dashboard shows, but it is only as stable as the name
        somebody gave the code, so the id travels with it for automations to match on.
        """
        runtime = self.coordinator.runtime(self.scope_id)
        return {
            ATTR_LABEL: runtime.last_label,
            ATTR_CREDENTIAL_ID: runtime.last_credential_id,
            ATTR_IN_GRACE_PERIOD: runtime.last_in_grace,
        }


class ScopeLastResultSensor(SensorEntity, HyperPasscodeScopeEntity):
    """The verdict on the last code submitted against this scope.

    Where ``last_used`` says when a code was last *accepted*, this says what happened
    on the last *attempt*, whatever it came from -- the keypad, an action, a webhook
    or the "Test a code" page.

    It deliberately covers dry runs too, and for them it is the only trace there is:
    a test counts no failure, trips no lockout and writes no audit row, so this
    sensor's history is what a run of ``unknown_code`` verdicts shows up in.
    """

    _attr_translation_key = "last_result"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:clipboard-check-outline"
    # No state class and no unit: both are rejected on an enum sensor.

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_last_result"
        self._attr_options = RESULT_STATES

    @property
    def native_value(self) -> str | None:
        """The verdict, or None until a code has been submitted.

        An enum sensor raises on a state outside its options, so a refusal that
        somehow carries no reason degrades to unknown rather than taking the entity
        down with it.
        """
        result = self.coordinator.runtime(self.scope_id).last_result
        if result is None:
            return None
        if result.valid:
            return str(Outcome.VALID)
        return str(result.reason) if result.reason else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Which code it was, why it was refused, and whether it was for real."""
        runtime = self.coordinator.runtime(self.scope_id)
        result = runtime.last_result
        if result is None:
            return {}
        return {
            ATTR_REASON: str(result.reason) if result.reason else None,
            ATTR_LABEL: result.label,
            ATTR_CREDENTIAL_ID: result.credential_id,
            ATTR_PERSON: result.person,
            ATTR_SOURCE: result.source,
            "dry_run": runtime.last_result_dry_run,
            "tested_at": runtime.last_result_at,
        }


class ScopeFailedAttemptsSensor(SensorEntity, HyperPasscodeScopeEntity):
    """Consecutive failed attempts since the last success.

    Resets on a success and when a lockout trips, since the lockout is what the
    counter was building towards.
    """

    _attr_translation_key = "failed_attempts"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self._attr_unique_id = f"{scope.scope_id}_failed_attempts"

    @property
    def native_value(self) -> int:
        """The current failure count."""
        return self.coordinator.runtime(self.scope_id).failed_attempts


class CredentialUsesSensor(SensorEntity, HyperPasscodeCredentialEntity):
    """How many times a credential has been accepted."""

    _attr_translation_key = "uses"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_uses"

    @property
    def native_value(self) -> int | None:
        """The lifetime use count."""
        credential = self.credential
        return credential.use_count if credential else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Remaining uses and validity window, for at-a-glance debugging."""
        credential = self.credential
        if credential is None:
            return {}
        policy = credential.policy
        remaining = (
            max(policy.max_uses - credential.counted_uses, 0)
            if policy.max_uses is not None
            else None
        )
        return {
            "remaining_uses": remaining,
            "max_uses": policy.max_uses,
            # Why the state and the remaining count need not add up: these are the
            # uses that fell inside the re-entry grace period. Both grace settings
            # are repeated here so the block still explains itself where per-credential
            # entities are turned off and neither of them has an entity.
            "uncounted_uses": credential.uncounted_uses,
            "grace_period_seconds": policy.grace_period_seconds,
            "grace_mode": str(policy.grace_mode),
            "valid_from": policy.valid_from,
            "valid_until": policy.valid_until,
            "enabled": credential.enabled,
            "revoked": credential.revoked,
            "tags": credential.tags,
            "last_used": credential.last_used,
        }


class CredentialUncountedUsesSensor(SensorEntity, HyperPasscodeCredentialEntity):
    """How many of a credential's uses a re-entry grace period excused.

    The difference between this and the Uses sensor is the number that was actually
    charged against ``max_uses``. Diagnostic rather than primary: it explains the
    other two numbers rather than being one somebody watches.
    """

    _attr_translation_key = "uncounted_uses"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter-off"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_uncounted_uses"

    @property
    def native_value(self) -> int | None:
        """How many uses were exempt from the limit."""
        credential = self.credential
        return credential.uncounted_uses if credential else None


class CredentialLastUsedSensor(SensorEntity, HyperPasscodeCredentialEntity):
    """When this credential was last accepted, counted or not.

    A timestamp sensor rather than a datetime entity: a datetime entity is settable,
    and when a code was last used is a record of what happened, not a setting. The
    scope's equivalent works the same way.
    """

    _attr_translation_key = "last_used"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_last_used"

    @property
    def native_value(self) -> datetime | None:
        """The last accepted use, or None if it has never been used."""
        credential = self.credential
        return credential.last_used if credential else None


class CredentialLastUncountedUseSensor(SensorEntity, HyperPasscodeCredentialEntity):
    """When a re-entry grace period last excused a use.

    Unknown until one has been, which is the quickest way to tell whether a grace
    period is earning its keep or whether nobody has ever come back inside it.
    """

    _attr_translation_key = "last_uncounted_use"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-remove-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_last_uncounted_use"

    @property
    def native_value(self) -> datetime | None:
        """The last use the grace period excused, if there has been one."""
        credential = self.credential
        return credential.last_uncounted_use if credential else None


class CredentialCodeSensor(SensorEntity, HyperPasscodeCredentialEntity):
    """The code itself, for the credentials that were kept viewable.

    This is the only way to read a code back after the add dialog has closed, which
    is the whole point of "Keep code viewable". For every other credential there is
    nothing to show -- only the lookup index was ever stored -- so the state is
    unknown rather than a placeholder that could be mistaken for the code.

    The state is a secret, so it lands in the recorder's history like any other. A
    code that must not be written to the database is one to leave un-viewable.
    """

    _attr_translation_key = "code"
    _attr_icon = "mdi:form-textbox-password"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = credential_code_unique_id(credential.credential_id)

    @property
    def native_value(self) -> str | None:
        """The code in clear, or None when no readable copy is kept."""
        credential = self.credential
        if credential is None or not credential.keep_viewable:
            return None
        return credential.plaintext


class CredentialStoreMethodSensor(SensorEntity, HyperPasscodeCredentialEntity):
    """Whether this code is held in clear or only as a lookup index.

    Reads ``plaintext`` while the code can be shown and ``hashed`` once it cannot,
    so a dashboard can tell at a glance which codes are recoverable without having
    to reason about the "Keep viewable" switch and the code sensor together.
    """

    _attr_translation_key = "store_method"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:database-lock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self._attr_unique_id = f"{credential.credential_id}_store_method"
        self._attr_options = STORE_METHOD_STATES

    @property
    def native_value(self) -> str | None:
        """Which of the two storage forms this credential is in."""
        credential = self.credential
        if credential is None:
            return None
        if credential.keep_viewable and credential.plaintext is not None:
            return str(StoreMethod.PLAINTEXT)
        return str(StoreMethod.HASHED)
