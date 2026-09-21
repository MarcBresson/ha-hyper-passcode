"""Small helpers shared by the test modules."""

from homeassistant.core import HomeAssistant, State


def state_of(hass: HomeAssistant, entity_id: str | None) -> State:
    """Return an entity's state, failing loudly if it is missing.

    ``hass.states.get`` and the entity registry's lookups are both optional-returning,
    so a typo in an entity id would otherwise surface as an ``AttributeError`` on
    ``None`` several lines later.
    """
    assert entity_id is not None, "no entity id -- the entity was never registered"
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    return state


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    """Set a number entity the way the UI does, and wait for the write to land."""
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": value},
        blocking=True,
    )
    await hass.async_block_till_done()


async def set_select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    """Pick an option on a select entity the way the UI does."""
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": option},
        blocking=True,
    )
    await hass.async_block_till_done()


async def set_text(hass: HomeAssistant, entity_id: str, value: str) -> None:
    """Set a text entity the way the UI does."""
    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": entity_id, "value": value},
        blocking=True,
    )
    await hass.async_block_till_done()
