"""Numbers: the thresholds and limits that used to be dialog fields.

A number entity is a better home for a threshold than a form field. It can be read in
a template, changed from a dashboard or an automation, and the recorder keeps its
history. Each one writes straight back to the scope's subentry or the credential's
policy, so a value set here survives a restart exactly as a dialog field did.

A number entity cannot hold ``None``, so every field that used to mean "leave it
blank" encodes that as zero: no fixed code length, unlimited uses, no cooldown. The
two lockout numbers are the exception, because zero already means "never lock out"
there. They report the integration-wide setting until something is written, and the
first write pins an override for that scope alone.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HyperPasscodeConfigEntry
from .coordinator import HyperPasscodeCoordinator
from .entity import (
    HyperPasscodeCredentialEntity,
    HyperPasscodeScopeEntity,
    async_add_credential_entities,
    async_add_scope_entities,
)
from .models import Credential, Policy, Scope


@dataclass(frozen=True, kw_only=True)
class ScopeNumberDescription(NumberEntityDescription):
    """One editable field on a scope."""

    #: Reads the value to show, which for the lockout pair is the effective one.
    value_fn: Callable[[HyperPasscodeCoordinator, Scope], float]
    #: Turns what the user typed into what the scope stores.
    to_stored: Callable[[float], Any]


@dataclass(frozen=True, kw_only=True)
class PolicyNumberDescription(NumberEntityDescription):
    """One editable limit on a credential's policy.

    Every one of these is ``int | None`` in the model, where None means no limit, and
    zero is the entity's way of saying the same thing.
    """

    value_fn: Callable[[Policy], int | None]


def _optional_int(value: float) -> int | None:
    """Map the zero a number entity uses for "unset" back onto None."""
    return int(value) or None


SCOPE_NUMBERS: tuple[ScopeNumberDescription, ...] = (
    ScopeNumberDescription(
        key="code_length",
        translation_key="code_length",
        icon="mdi:numeric",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0,
        native_max_value=64,
        native_step=1,
        value_fn=lambda _coordinator, scope: scope.code_length or 0,
        to_stored=_optional_int,
    ),
    ScopeNumberDescription(
        key="inter_key_timeout",
        translation_key="inter_key_timeout",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=1,
        native_max_value=300,
        native_step=0.5,
        value_fn=lambda _coordinator, scope: scope.inter_key_timeout,
        to_stored=float,
    ),
    ScopeNumberDescription(
        key="lockout_threshold",
        translation_key="lockout_threshold",
        icon="mdi:lock-alert-outline",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        value_fn=lambda coordinator, scope: coordinator.lockout_threshold(scope),
        to_stored=int,
    ),
    ScopeNumberDescription(
        key="lockout_duration",
        translation_key="lockout_duration",
        icon="mdi:timer-lock-outline",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0,
        native_max_value=86400,
        native_step=1,
        value_fn=lambda coordinator, scope: coordinator.lockout_duration(scope),
        to_stored=int,
    ),
)

POLICY_NUMBERS: tuple[PolicyNumberDescription, ...] = (
    PolicyNumberDescription(
        key="max_uses",
        translation_key="max_uses",
        icon="mdi:counter",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0,
        native_max_value=100000,
        native_step=1,
        value_fn=lambda policy: policy.max_uses,
    ),
    PolicyNumberDescription(
        key="uses_per_hour",
        translation_key="uses_per_hour",
        icon="mdi:speedometer",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0,
        native_max_value=1000,
        native_step=1,
        value_fn=lambda policy: policy.uses_per_hour,
    ),
    PolicyNumberDescription(
        key="uses_per_day",
        translation_key="uses_per_day",
        icon="mdi:speedometer-medium",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0,
        native_max_value=1000,
        native_step=1,
        value_fn=lambda policy: policy.uses_per_day,
    ),
    PolicyNumberDescription(
        key="cooldown_seconds",
        translation_key="cooldown_seconds",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0,
        native_max_value=86400,
        native_step=1,
        value_fn=lambda policy: policy.cooldown_seconds,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyperPasscodeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the editable numbers on every scope and every credential."""
    coordinator = entry.runtime_data
    async_add_scope_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        [partial(ScopeNumber, description=d) for d in SCOPE_NUMBERS],
    )
    async_add_credential_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        [partial(PolicyNumber, description=d) for d in POLICY_NUMBERS],
    )


class ScopeNumber(NumberEntity, HyperPasscodeScopeEntity):
    """One editable setting on a scope."""

    entity_description: ScopeNumberDescription

    def __init__(
        self,
        coordinator: HyperPasscodeCoordinator,
        scope: Scope,
        description: ScopeNumberDescription,
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, scope)
        self.entity_description = description
        self._attr_unique_id = f"{scope.scope_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """The value currently in effect, or None once the scope is gone."""
        scope = self.scope
        if scope is None:
            return None
        return self.entity_description.value_fn(self.coordinator, scope)

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value back to the scope's subentry."""
        await self.coordinator.async_update_scope(
            self.scope_id,
            {self.entity_description.key: self.entity_description.to_stored(value)},
        )


class PolicyNumber(NumberEntity, HyperPasscodeCredentialEntity):
    """One editable limit on a credential's policy."""

    entity_description: PolicyNumberDescription

    def __init__(
        self,
        coordinator: HyperPasscodeCoordinator,
        credential: Credential,
        description: PolicyNumberDescription,
    ) -> None:
        """Set the entity's identity."""
        super().__init__(coordinator, credential)
        self.entity_description = description
        self._attr_unique_id = f"{credential.credential_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """The limit, with an absent one reported as zero."""
        credential = self.credential
        if credential is None:
            return None
        return self.entity_description.value_fn(credential.policy) or 0

    async def async_set_native_value(self, value: float) -> None:
        """Write the new limit into the credential's policy."""
        credential = self.credential
        if credential is None:
            return
        policy = credential.policy.to_dict()
        policy[self.entity_description.key] = _optional_int(value)
        await self.coordinator.async_update_credential(
            self.credential_id, {"policy": policy}
        )
