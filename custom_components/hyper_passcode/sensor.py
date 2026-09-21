"""Sensors: per-scope activity and per-credential use counts."""

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HyperPasscodeConfigEntry
from .const import (
    ATTR_CREDENTIAL_ID,
    ATTR_LABEL,
    ATTR_PERSON,
    ATTR_REASON,
    ATTR_SOURCE,
    Outcome,
    RejectionReason,
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
        hass, entry, coordinator, async_add_entities, [CredentialUsesSensor]
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
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Which credential it was."""
        return {ATTR_LABEL: self.coordinator.runtime(self.scope_id).last_label}


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
            max(policy.max_uses - credential.use_count, 0)
            if policy.max_uses is not None
            else None
        )
        return {
            "remaining_uses": remaining,
            "max_uses": policy.max_uses,
            "valid_from": policy.valid_from,
            "valid_until": policy.valid_until,
            "enabled": credential.enabled,
            "revoked": credential.revoked,
            "tags": credential.tags,
            "last_used": credential.last_used,
        }
