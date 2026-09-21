"""The validity engine.

Evaluates a credential's rules in a fixed order and reports the *first* failure, so
the reason a user sees is the most fundamental one. A revoked code that has also
expired reports ``revoked``, which is what somebody debugging it needs to know.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant

from .const import GraceMode, RejectionReason
from .models import Credential

if TYPE_CHECKING:
    from .models import Policy


def evaluate(
    hass: HomeAssistant,
    credential: Credential,
    scope_id: str,
    source: str,
    now: datetime,
) -> RejectionReason | None:
    """Return why ``credential`` may not be used right now, or None if it may.

    ``now`` is passed in rather than read here so a single submission evaluates every
    rule against one consistent instant.
    """
    if credential.grant_for(scope_id) is None:
        return RejectionReason.NO_GRANT

    if credential.revoked:
        return RejectionReason.REVOKED

    if not credential.enabled:
        return RejectionReason.DISABLED

    policy = credential.policy_for(scope_id)

    if policy.allowed_sources and source not in policy.allowed_sources:
        return RejectionReason.WRONG_SOURCE

    if policy.valid_from is not None and now < policy.valid_from:
        return RejectionReason.NOT_YET_VALID

    if policy.valid_until is not None and now >= policy.valid_until:
        return RejectionReason.EXPIRED

    if not _all_entities_on(hass, policy.schedule_entities):
        return RejectionReason.OUT_OF_SCHEDULE

    if not _all_entities_on(hass, policy.condition_entities):
        return RejectionReason.CONDITION_FAILED

    # is_within_grace is checked last only because it can never reject -- a grace
    # period only ever permits -- so there is nothing to ask when the limit is not
    # reached anyway. Whether a use is *counted* is a separate question, decided for
    # every use in _evaluate_submission.
    if (
        policy.max_uses is not None
        and credential.counted_uses >= policy.max_uses
        and not is_within_grace(credential, policy, now)
    ):
        return RejectionReason.MAX_USES_REACHED

    # Below the grace period, deliberately: a cooldown still bites during one. The
    # two settings pull against each other -- grace says "come straight back and it
    # is free", a cooldown says "not yet" -- and a code carrying both gets the
    # stricter answer.
    if _is_rate_limited(credential, policy, now):
        return RejectionReason.RATE_LIMITED

    return None


def _all_entities_on(hass: HomeAssistant, entity_ids: list[str]) -> bool:
    """Return True when every listed entity is currently on.

    Fails closed: an entity that is missing, unavailable or unknown counts as off. For
    a component that authorises door access, an unreadable condition must never be
    treated as permission.
    """
    return all(hass.states.is_state(entity_id, STATE_ON) for entity_id in entity_ids)


def is_within_grace(credential: Credential, policy: Policy, now: datetime) -> bool:
    """Whether a use right now is exempt from ``max_uses``.

    The re-entry grace period is for the delivery driver who has to come back out
    through the door they were just let through, or the cleaner who fetches something
    from the car: inside the window it is all one visit, so it is one use.

    The window is anchored on the last *counted* use in ``fixed`` mode and on the last
    use of any kind in ``sliding`` mode. That is the whole difference between them,
    and why a sliding window stays open for as long as the gaps stay short while a
    fixed one cannot be walked forward.

    Deliberately false when there is no use limit. Nothing needs exempting without
    one, and exempting uses anyway would quietly bank an allowance against a limit set
    later. Both that guard and the "is it even switched on" one live here rather than
    at the call sites, so this function means exactly "this use is exempt" and the
    verdict cannot disagree with the counting.
    """
    if policy.max_uses is None or not policy.grace_period_seconds:
        return False

    anchor = (
        credential.last_used
        if policy.grace_mode is GraceMode.SLIDING
        else credential.last_counted_use
    )
    if anchor is None:
        return False
    return now - anchor < timedelta(seconds=policy.grace_period_seconds)


def _is_rate_limited(credential: Credential, policy: Policy, now: datetime) -> bool:
    """Check the cooldown and the rolling per-hour / per-day limits.

    Both windows are rolling rather than calendar-aligned, so "three times a day"
    cannot be doubled up by using a code either side of midnight.
    """
    if (
        policy.cooldown_seconds
        and credential.last_used is not None
        and now - credential.last_used < timedelta(seconds=policy.cooldown_seconds)
    ):
        return True

    if (
        policy.uses_per_hour is not None
        and _uses_since(credential, now - timedelta(hours=1)) >= policy.uses_per_hour
    ):
        return True

    return (
        policy.uses_per_day is not None
        and _uses_since(credential, now - timedelta(days=1)) >= policy.uses_per_day
    )


def _uses_since(credential: Credential, cutoff: datetime) -> int:
    """Count recorded uses at or after ``cutoff``."""
    return sum(1 for used_at in credential.recent_uses if used_at >= cutoff)


def next_boundary(credential: Credential, now: datetime) -> datetime | None:
    """Return the soonest future moment this credential's validity could change.

    Used to schedule a timer so per-credential validity entities flip the instant a
    window opens or closes, rather than lying until the next submission.
    """
    grant_policies = (g.policy for g in credential.grants if g.policy)
    candidates = [
        boundary
        for policy in (credential.policy, *grant_policies)
        for boundary in (policy.valid_from, policy.valid_until)
        if boundary is not None and boundary > now
    ]
    return min(candidates) if candidates else None
