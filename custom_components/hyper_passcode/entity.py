"""Shared entity bases.

Both scopes and credentials get a device of their own. That is what makes entity ids
read properly: a "Uses" entity under a "Cleaner" device becomes ``sensor.cleaner_uses``
rather than something prefixed with a shared device name. It also means deleting
either one can simply remove its device and let the removal cascade to its entities.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, credential_device_identifier
from .coordinator import (
    SIGNAL_CREDENTIAL_UPDATED,
    SIGNAL_CREDENTIALS_CHANGED,
    SIGNAL_SCOPE_UPDATED,
    SIGNAL_SCOPES_CHANGED,
    HyperPasscodeCoordinator,
)
from .models import Credential, Scope

MANUFACTURER = "HyperPasscode"

type ScopeEntityFactory = Callable[[HyperPasscodeCoordinator, Scope], Entity]
type CredentialEntityFactory = Callable[[HyperPasscodeCoordinator, Credential], Entity]


@callback
def async_add_scope_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyperPasscodeCoordinator,
    async_add_entities: AddEntitiesCallback,
    factories: Iterable[ScopeEntityFactory],
) -> None:
    """Create entities for every scope, and for scopes added later.

    Entities for a deleted scope are cleaned up by removing the scope's device, which
    cascades, so this only ever needs to add.
    """
    known: set[str] = set()
    builders = list(factories)

    @callback
    def _refresh() -> None:
        new: list[Entity] = []
        for scope_id, scope in coordinator.scopes.items():
            if scope_id in known:
                continue
            known.add(scope_id)
            new.extend(build(coordinator, scope) for build in builders)
        if new:
            async_add_entities(new)

    _refresh()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_SCOPES_CHANGED, _refresh)
    )


@callback
def async_add_credential_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyperPasscodeCoordinator,
    async_add_entities: AddEntitiesCallback,
    factories: Iterable[CredentialEntityFactory],
) -> None:
    """Create entities for every credential, and for credentials added later.

    Does nothing when ``per_credential_entities`` is off: at a hundred credentials the
    entity list becomes unusable, so it has to be possible to turn off.
    """
    if not coordinator.per_credential_entities:
        return

    known: set[str] = set()
    builders = list(factories)

    @callback
    def _refresh() -> None:
        new: list[Entity] = []
        for credential_id, credential in coordinator.credentials.items():
            if credential_id in known:
                continue
            known.add(credential_id)
            new.extend(build(coordinator, credential) for build in builders)
        if new:
            async_add_entities(new)

    _refresh()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_CREDENTIALS_CHANGED, _refresh)
    )


class HyperPasscodeScopeEntity(Entity):
    """Base for entities belonging to one scope."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: HyperPasscodeCoordinator, scope: Scope) -> None:
        """Bind the entity to its scope."""
        self.coordinator = coordinator
        self.scope_id = scope.scope_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, scope.scope_id)},
            name=scope.name,
            manufacturer=MANUFACTURER,
            model="Passcode scope",
        )

    @property
    def scope(self) -> Scope | None:
        """The scope this entity tracks, or None once it has been deleted."""
        return self.coordinator.scopes.get(self.scope_id)

    @property
    def available(self) -> bool:
        """Unavailable once the underlying scope is gone."""
        return self.scope is not None

    async def async_added_to_hass(self) -> None:
        """Subscribe to this scope's updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SCOPE_UPDATED.format(self.scope_id),
                self.async_write_ha_state,
            )
        )


class HyperPasscodeCredentialEntity(Entity):
    """Base for entities belonging to one credential."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, coordinator: HyperPasscodeCoordinator, credential: Credential
    ) -> None:
        """Bind the entity to its credential."""
        self.coordinator = coordinator
        self.credential_id = credential.credential_id
        self._attr_device_info = DeviceInfo(
            identifiers={credential_device_identifier(credential.credential_id)},
            name=credential.label,
            manufacturer=MANUFACTURER,
            model="Credential",
        )

    @property
    def credential(self) -> Credential | None:
        """The credential this entity tracks, or None once it has been deleted."""
        return self.coordinator.credentials.get(self.credential_id)

    @property
    def available(self) -> bool:
        """Unavailable once the underlying credential is gone."""
        return self.credential is not None

    async def async_added_to_hass(self) -> None:
        """Subscribe to this credential's updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CREDENTIAL_UPDATED.format(self.credential_id),
                self.async_write_ha_state,
            )
        )
