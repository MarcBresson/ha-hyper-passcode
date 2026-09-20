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
