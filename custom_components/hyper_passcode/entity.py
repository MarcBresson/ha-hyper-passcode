"""Shared entity bases.

Scopes, credentials and keypad buffers each get a device of their own. That is what
makes entity ids read properly: a "Uses" entity under a "Cleaner" device becomes
``sensor.cleaner_uses`` rather than something prefixed with a shared device name. It
also means deleting any one of them can simply remove its device and let the removal
cascade to its entities.

A code granted on exactly one scope is linked to that scope's device, and a keypad
buffer always is (it targets exactly one), so the scope's device page shows both the
codes that open it and the keypads that feed it, instead of leaving everything in one
flat list.
"""

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DEVICE_MANUFACTURER,
    DEVICE_MODEL_CREDENTIAL,
    DEVICE_MODEL_KEYPAD,
    DEVICE_MODEL_SCOPE,
    DOMAIN,
    credential_device_identifier,
    keypad_device_identifier,
)
from .coordinator import (
    SIGNAL_CREDENTIAL_UPDATED,
    SIGNAL_CREDENTIALS_CHANGED,
    SIGNAL_KEYPAD_UPDATED,
    SIGNAL_KEYPADS_CHANGED,
    SIGNAL_SCOPE_UPDATED,
    SIGNAL_SCOPES_CHANGED,
    HyperPasscodeCoordinator,
)
from .models import Credential, Keypad, Scope

type ScopeEntityFactory = Callable[[HyperPasscodeCoordinator, Scope], Entity]
type CredentialEntityFactory = Callable[[HyperPasscodeCoordinator, Credential], Entity]
type KeypadEntityFactory = Callable[[HyperPasscodeCoordinator, Keypad], Entity]


@callback
def async_add_scope_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyperPasscodeCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factories: Iterable[ScopeEntityFactory],
) -> None:
    """Create each scope's entities, and those of scopes added later.

    ``config_subentry_id`` is what ties the entities to their scope, so Home Assistant
    removes them itself when the scope is deleted. That is also why this only ever
    needs to add.
    """
    known: set[str] = set()
    builders = list(factories)

    @callback
    def _refresh() -> None:
        for scope_id, scope in coordinator.scopes.items():
            if scope_id in known:
                continue
            known.add(scope_id)
            async_add_entities(
                [build(coordinator, scope) for build in builders],
                config_subentry_id=scope_id,
            )

    _refresh()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_SCOPES_CHANGED, _refresh)
    )


@callback
def async_add_credential_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyperPasscodeCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
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
        for credential_id, credential in coordinator.credentials.items():
            if credential_id in known:
                continue
            # A credential added through the dialog has a subentry id Home Assistant
            # chose, which is not its credential id, so it has to be looked up.
            subentry_id = coordinator.async_credential_subentry_id(credential_id)
            if subentry_id is None:
                continue
            known.add(credential_id)
            async_add_entities(
                [build(coordinator, credential) for build in builders],
                config_subentry_id=subentry_id,
            )

    _refresh()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_CREDENTIALS_CHANGED, _refresh)
    )


@callback
def async_add_keypad_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyperPasscodeCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factories: Iterable[KeypadEntityFactory],
) -> None:
    """Create each keypad's entities, and those of keypads added later."""
    known: set[str] = set()
    builders = list(factories)

    @callback
    def _refresh() -> None:
        for keypad_id, keypad in coordinator.keypads.items():
            if keypad_id in known:
                continue
            known.add(keypad_id)
            async_add_entities(
                [build(coordinator, keypad) for build in builders],
                config_subentry_id=keypad_id,
            )

    _refresh()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_KEYPADS_CHANGED, _refresh)
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
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL_SCOPE,
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
        device_info = DeviceInfo(
            identifiers={credential_device_identifier(credential.credential_id)},
            name=credential.label,
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL_CREDENTIAL,
        )
        # Omitted rather than set to None when the code has no single scope: passing
        # the key at all would clear a link the registry is already holding, and
        # ``async_sync_credential_devices`` is what deliberately clears one.
        if (via := coordinator.async_credential_via_device_id(credential)) is not None:
            device_info["via_device_id"] = via
        self._attr_device_info = device_info

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


class HyperPasscodeKeypadEntity(Entity):
    """Base for entities belonging to one keypad buffer."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: HyperPasscodeCoordinator, keypad: Keypad) -> None:
        """Bind the entity to its keypad."""
        self.coordinator = coordinator
        self.keypad_id = keypad.keypad_id
        device_info = DeviceInfo(
            identifiers={keypad_device_identifier(keypad.keypad_id)},
            name=keypad.name,
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL_KEYPAD,
        )
        # A keypad's scope device is always registered first, but the key is still
        # omitted rather than set to None if it somehow is not: passing it would
        # clear a link the registry may already be holding.
        if (via := coordinator.async_device_id(keypad.scope_id)) is not None:
            device_info["via_device_id"] = via
        self._attr_device_info = device_info

    @property
    def keypad(self) -> Keypad | None:
        """The keypad this entity tracks, or None once it has been deleted."""
        return self.coordinator.keypads.get(self.keypad_id)

    @property
    def available(self) -> bool:
        """Unavailable once the underlying keypad is gone."""
        return self.keypad is not None

    async def async_added_to_hass(self) -> None:
        """Subscribe to this keypad's updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_KEYPAD_UPDATED.format(self.keypad_id),
                self.async_write_ha_state,
            )
        )
