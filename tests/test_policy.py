"""The validity engine: one case per rejection reason, plus time handling."""

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hyper_passcode.const import GraceMode, RejectionReason, Source
from custom_components.hyper_passcode.models import Credential, Grant, Policy
from custom_components.hyper_passcode.policy import (
    evaluate,
    is_within_grace,
    next_boundary,
)

SCOPE = "scope-1"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def make_credential(policy: Policy | None = None, **kwargs) -> Credential:
    """Build a credential granted on SCOPE."""
    return Credential(
        credential_id="cred-1",
        label="Test",
        lookup_index="deadbeef",
        policy=policy or Policy(),
        grants=[Grant(scope_id=SCOPE)],
        **kwargs,
    )


def check(hass: HomeAssistant, credential: Credential, **kwargs):
    """Evaluate against SCOPE at NOW."""
    return evaluate(
        hass,
        credential,
        kwargs.pop("scope_id", SCOPE),
        kwargs.pop("source", str(Source.KEYPAD)),
        kwargs.pop("now", NOW),
    )


async def test_an_unconstrained_credential_is_accepted(hass: HomeAssistant):
    assert check(hass, make_credential()) is None


async def test_no_grant(hass: HomeAssistant):
    assert check(hass, make_credential(), scope_id="other") is RejectionReason.NO_GRANT


async def test_revoked_beats_disabled(hass: HomeAssistant):
    credential = make_credential(revoked=True, enabled=False)
    # Revocation is permanent, so it is the more useful thing to report.
    assert check(hass, credential) is RejectionReason.REVOKED


async def test_disabled(hass: HomeAssistant):
    assert check(hass, make_credential(enabled=False)) is RejectionReason.DISABLED


async def test_wrong_source(hass: HomeAssistant):
    credential = make_credential(Policy(allowed_sources=[str(Source.UI)]))
    assert check(hass, credential) is RejectionReason.WRONG_SOURCE
    assert check(hass, credential, source=str(Source.UI)) is None


async def test_not_yet_valid(hass: HomeAssistant):
    credential = make_credential(Policy(valid_from=NOW + timedelta(hours=1)))
    assert check(hass, credential) is RejectionReason.NOT_YET_VALID


async def test_expired(hass: HomeAssistant):
    credential = make_credential(Policy(valid_until=NOW - timedelta(seconds=1)))
    assert check(hass, credential) is RejectionReason.EXPIRED


async def test_validity_boundaries_are_inclusive_start_exclusive_end(
    hass: HomeAssistant,
):
    credential = make_credential(
        Policy(valid_from=NOW, valid_until=NOW + timedelta(hours=1))
    )
    assert check(hass, credential, now=NOW) is None
    assert check(hass, credential, now=NOW - timedelta(microseconds=1)) is (
        RejectionReason.NOT_YET_VALID
    )
    assert check(hass, credential, now=NOW + timedelta(hours=1)) is (
        RejectionReason.EXPIRED
    )
    assert check(hass, credential, now=NOW + timedelta(minutes=59)) is None


async def test_out_of_schedule(hass: HomeAssistant):
    credential = make_credential(Policy(schedule_entities=["schedule.cleaner"]))

    hass.states.async_set("schedule.cleaner", "off")
    assert check(hass, credential) is RejectionReason.OUT_OF_SCHEDULE

    hass.states.async_set("schedule.cleaner", "on")
    assert check(hass, credential) is None


async def test_condition_failed(hass: HomeAssistant):
    credential = make_credential(Policy(condition_entities=["calendar.booking"]))

    hass.states.async_set("calendar.booking", "off")
    assert check(hass, credential) is RejectionReason.CONDITION_FAILED

    hass.states.async_set("calendar.booking", "on")
    assert check(hass, credential) is None


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_unreadable_conditions_fail_closed(hass: HomeAssistant, state):
    # An unreadable condition must never be treated as permission to enter.
    credential = make_credential(Policy(condition_entities=["binary_sensor.gate"]))
    hass.states.async_set("binary_sensor.gate", state)
    assert check(hass, credential) is RejectionReason.CONDITION_FAILED


async def test_missing_condition_entity_fails_closed(hass: HomeAssistant):
    credential = make_credential(Policy(condition_entities=["binary_sensor.absent"]))
    assert check(hass, credential) is RejectionReason.CONDITION_FAILED


async def test_max_uses_reached(hass: HomeAssistant):
    credential = make_credential(Policy(max_uses=2), use_count=2)
    assert check(hass, credential) is RejectionReason.MAX_USES_REACHED

    credential.use_count = 1
    assert check(hass, credential) is None


async def test_cooldown(hass: HomeAssistant):
    credential = make_credential(Policy(cooldown_seconds=60))
    credential.last_used = NOW - timedelta(seconds=30)
    assert check(hass, credential) is RejectionReason.RATE_LIMITED

    credential.last_used = NOW - timedelta(seconds=61)
    assert check(hass, credential) is None


async def test_uses_per_hour_is_a_rolling_window(hass: HomeAssistant):
    credential = make_credential(Policy(uses_per_hour=2))
    credential.recent_uses = [NOW - timedelta(minutes=10), NOW - timedelta(minutes=20)]
    assert check(hass, credential) is RejectionReason.RATE_LIMITED

    # One of them drops out of the window.
    credential.recent_uses = [NOW - timedelta(minutes=10), NOW - timedelta(minutes=61)]
    assert check(hass, credential) is None


async def test_uses_per_day_cannot_be_doubled_over_midnight(hass: HomeAssistant):
    # A rolling 24 hours, not a calendar day, so using a code either side of
    # midnight does not reset the allowance.
    credential = make_credential(Policy(uses_per_day=1))
    credential.recent_uses = [NOW - timedelta(hours=20)]
    assert check(hass, credential) is RejectionReason.RATE_LIMITED

    credential.recent_uses = [NOW - timedelta(hours=25)]
    assert check(hass, credential) is None


# ----------------------------------------------------------------------
# Re-entry grace period
# ----------------------------------------------------------------------


def exhausted(mode: GraceMode = GraceMode.FIXED, **policy_kwargs) -> Credential:
    """A one-use code that has had its one use, at NOW, and a 5 minute grace."""
    credential = make_credential(
        Policy(max_uses=1, grace_period_seconds=300, grace_mode=mode, **policy_kwargs),
        use_count=1,
    )
    credential.last_used = credential.last_counted_use = NOW
    return credential


async def test_a_use_inside_the_grace_period_does_not_count_towards_max_uses(
    hass: HomeAssistant,
):
    # The whole point: the driver who has to come straight back out through the door
    # they were just let through is still on the same visit.
    assert check(hass, exhausted(), now=NOW + timedelta(minutes=3)) is None


async def test_the_first_use_is_never_inside_a_grace_period(hass: HomeAssistant):
    # Nothing has opened a window yet, so there is nothing to be inside of.
    credential = make_credential(Policy(max_uses=1, grace_period_seconds=300))
    assert is_within_grace(credential, credential.policy, NOW) is False


async def test_a_fixed_grace_window_does_not_extend(hass: HomeAssistant):
    credential = exhausted()

    assert check(hass, credential, now=NOW + timedelta(minutes=4, seconds=30)) is None
    # Exclusive at the boundary, like every other window in the engine.
    for lapsed in (timedelta(minutes=5), timedelta(minutes=6)):
        assert (
            check(hass, credential, now=NOW + lapsed)
            is RejectionReason.MAX_USES_REACHED
        )


async def test_a_sliding_grace_window_restarts_on_every_use(hass: HomeAssistant):
    credential = exhausted(GraceMode.SLIDING)
    # A free use four minutes in moved last_used but not last_counted_use, which is
    # the whole of the difference between the two modes.
    credential.last_used = NOW + timedelta(minutes=4)

    assert check(hass, credential, now=NOW + timedelta(minutes=6)) is None
    assert (
        check(hass, credential, now=NOW + timedelta(minutes=10))
        is RejectionReason.MAX_USES_REACHED
    )


async def test_the_same_state_is_refused_under_fixed_and_allowed_under_sliding(
    hass: HomeAssistant,
):
    # Switching a code to fixed can take it from usable to refused on the spot. That
    # is the honest meaning of "does not extend", not a bug to special-case away.
    at = NOW + timedelta(minutes=6)
    for mode, expected in (
        (GraceMode.FIXED, RejectionReason.MAX_USES_REACHED),
        (GraceMode.SLIDING, None),
    ):
        credential = exhausted(mode)
        credential.last_used = NOW + timedelta(minutes=4)
        assert check(hass, credential, now=at) is expected


async def test_a_grace_period_with_no_use_limit_exempts_nothing(hass: HomeAssistant):
    # Without a limit there is nothing to exempt, and banking exemptions anyway would
    # hand a fresh allowance to a max_uses set later.
    credential = make_credential(Policy(grace_period_seconds=300), use_count=9)
    credential.last_used = credential.last_counted_use = NOW

    assert is_within_grace(credential, credential.policy, NOW) is False

    # So none of those nine uses banked an allowance: adding a limit later finds the
    # code fully spent, rather than crediting it for the ones it "got free".
    credential.policy.max_uses = 1
    assert credential.counted_uses == 9
    assert (
        check(hass, credential, now=NOW + timedelta(hours=1))
        is RejectionReason.MAX_USES_REACHED
    )


async def test_a_grace_period_does_not_exempt_a_code_from_its_cooldown(
    hass: HomeAssistant,
):
    # The two settings pull against each other on purpose: grace says "come straight
    # back and it is free", a cooldown says "not yet". The stricter one answers.
    credential = exhausted(cooldown_seconds=60)
    assert (
        check(hass, credential, now=NOW + timedelta(seconds=30))
        is RejectionReason.RATE_LIMITED
    )
    assert check(hass, credential, now=NOW + timedelta(seconds=61)) is None


async def test_a_grace_period_does_not_exempt_a_code_from_its_daily_limit(
    hass: HomeAssistant,
):
    credential = exhausted(uses_per_day=1)
    credential.recent_uses = [NOW]
    assert (
        check(hass, credential, now=NOW + timedelta(minutes=3))
        is RejectionReason.RATE_LIMITED
    )


async def test_a_lapsed_grace_period_still_reports_max_uses_reached(
    hass: HomeAssistant,
):
    # No new rejection reason was invented, so the enum sensor and the event types
    # need nothing added to them.
    assert (
        check(hass, exhausted(), now=NOW + timedelta(hours=1))
        is RejectionReason.MAX_USES_REACHED
    )


async def test_switching_the_grace_period_off_bites_immediately(hass: HomeAssistant):
    credential = exhausted()
    credential.policy.grace_period_seconds = 0
    assert (
        check(hass, credential, now=NOW + timedelta(minutes=1))
        is RejectionReason.MAX_USES_REACHED
    )


async def test_first_failing_rule_wins(hass: HomeAssistant):
    credential = make_credential(
        Policy(valid_until=NOW - timedelta(hours=1), max_uses=1),
        use_count=5,
        enabled=False,
    )
    assert check(hass, credential) is RejectionReason.DISABLED


async def test_next_boundary_ignores_the_past(hass: HomeAssistant):
    credential = make_credential(
        Policy(valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(hours=3))
    )
    assert next_boundary(credential, NOW) == NOW + timedelta(hours=3)


async def test_next_boundary_is_none_without_a_window(hass: HomeAssistant):
    assert next_boundary(make_credential(), NOW) is None
