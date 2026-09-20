"""Persistence for HyperPasscode.

A thin wrapper over Home Assistant's Store helper. All business logic lives in the
coordinator; this module only turns the model into JSON and back, and owns schema
migration.

The store file is written ``private`` (mode 0600) and atomically, because it holds
door codes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .crypto import generate_integration_key
from .models import AuditEntry, Credential

_LOGGER = logging.getLogger(__name__)

#: Writes are debounced; a burst of keypad activity should not mean a burst of disk IO.
SAVE_DELAY = 10


@dataclass
class StoredData:
    """Everything HyperPasscode persists.

    Two things deliberately live elsewhere, both on the config entry, so that the
    standard Home Assistant UI edits one source of truth rather than two:

    - integration-level *settings* are the entry's options
    - *scopes* are config subentries, which is what gives them an "Add scope" button
      and a per-scope configure dialog

    What is left here is data rather than configuration: the lookup key, the
    credentials, and the audit log.
    """

    #: Random key backing every credential's lookup index. Generated once.
    key: str
    credentials: dict[str, Credential] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)

    @classmethod
    def empty(cls) -> StoredData:
        """Build a fresh dataset with a newly generated key."""
        return cls(key=generate_integration_key())

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the store."""
        return {
            "key": self.key,
            "credentials": {k: v.to_dict() for k, v in self.credentials.items()},
            "audit": [entry.to_dict() for entry in self.audit],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoredData:
        """Rebuild from the store."""
        return cls(
            key=data.get("key") or generate_integration_key(),
            credentials={
                k: Credential.from_dict(v)
                for k, v in (data.get("credentials") or {}).items()
            },
            audit=[AuditEntry.from_dict(e) for e in data.get("audit") or []],
        )


class HyperPasscodeStore:
    """Loads and saves the integration's data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Set up the underlying Home Assistant store."""
        self._store = _MigratingStore(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            private=True,
            atomic_writes=True,
        )
        self.data: StoredData = StoredData.empty()

    async def async_load(self) -> StoredData:
        """Load from disk, falling back to a fresh dataset on first run."""
        raw = await self._store.async_load()
        if raw is None:
            _LOGGER.debug("No existing store, starting fresh")
            self.data = StoredData.empty()
        else:
            self.data = StoredData.from_dict(raw)
        return self.data

    @callback
    def async_schedule_save(self) -> None:
        """Queue a debounced write."""
        self._store.async_delay_save(self.data.to_dict, SAVE_DELAY)

    async def async_save(self) -> None:
        """Write immediately, bypassing the debounce."""
        await self._store.async_save(self.data.to_dict())

    async def async_remove(self) -> None:
        """Delete the store entirely, used when the config entry is removed."""
        await self._store.async_remove()


class _MigratingStore(Store[dict[str, Any]]):
    """Store subclass owning schema migration."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Upgrade an older payload to the current schema.

        Only version 1 exists so far, so there is nothing to do yet. Future versions
        mutate ``old_data`` step by step from ``old_major_version`` upwards.
        """
        if old_major_version > STORAGE_VERSION:
            raise ValueError(
                f"Cannot downgrade {STORAGE_KEY} store from version "
                f"{old_major_version} to {STORAGE_VERSION}"
            )
        return old_data
