"""Shared test fixtures."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyper_passcode.const import DOMAIN
from custom_components.hyper_passcode.coordinator import HyperPasscodeCoordinator

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components during tests."""
    return


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A loaded HyperPasscode config entry."""
    config_entry = MockConfigEntry(domain=DOMAIN, title="HyperPasscode", data={})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture
def coordinator(entry: MockConfigEntry) -> HyperPasscodeCoordinator:
    """The coordinator behind the loaded entry."""
    return entry.runtime_data


@pytest.fixture
async def scope(coordinator: HyperPasscodeCoordinator):
    """A scope with no default actions."""
    return await coordinator.async_create_scope(name="Front Door")
