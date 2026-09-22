"""Constants for the HyperPasscode integration."""

from enum import StrEnum
from typing import Final

DOMAIN: Final = "hyper_passcode"

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1

#: Scopes, credentials and keypad buffers are config subentries, which is what gives
#: them "Add" buttons on the integration page and configure dialogs of their own.
SUBENTRY_TYPE_SCOPE: Final = "scope"
SUBENTRY_TYPE_CREDENTIAL: Final = "credential"
SUBENTRY_TYPE_KEYPAD: Final = "keypad"

#: Credential and keypad device identifiers are prefixed so they can never be
#: confused with a scope's, whatever ids happen to be generated.
CREDENTIAL_DEVICE_PREFIX: Final = "credential"
KEYPAD_DEVICE_PREFIX: Final = "keypad"

# How the three device kinds present themselves in the device registry.
DEVICE_MANUFACTURER: Final = "HyperPasscode"
DEVICE_MODEL_SCOPE: Final = "Passcode scope"
DEVICE_MODEL_CREDENTIAL: Final = "Credential"
DEVICE_MODEL_KEYPAD: Final = "Keypad buffer"


def credential_device_identifier(credential_id: str) -> tuple[str, str]:
    """Return the device registry identifier for a credential."""
    return (DOMAIN, f"{CREDENTIAL_DEVICE_PREFIX}_{credential_id}")


def keypad_device_identifier(keypad_id: str) -> tuple[str, str]:
    """Return the device registry identifier for a keypad buffer."""
    return (DOMAIN, f"{KEYPAD_DEVICE_PREFIX}_{keypad_id}")


def credential_code_unique_id(credential_id: str) -> str:
    """Return the unique id of the sensor that shows a credential's code.

    Shared with the coordinator rather than left inline in the sensor, because
    discarding a stored code also has to find that entity's recorded history and
    delete it. A drift between the two spellings would silently leave the code in
    the recorder database.
    """
    return f"{credential_id}_code"


# Integration-level settings and their defaults.
CONF_REJECT_WEAK_CODES: Final = "reject_weak_codes"
CONF_WEAK_CODE_BLOCKLIST: Final = "weak_code_blocklist"
CONF_PER_CREDENTIAL_ENTITIES: Final = "per_credential_entities"
CONF_AUDIT_LOG_SIZE: Final = "audit_log_size"
CONF_LOG_FAILED_PLAINTEXT: Final = "log_failed_plaintext"
CONF_DEFAULT_CODE_LENGTH: Final = "default_code_length"

DEFAULT_REJECT_WEAK_CODES: Final = True
DEFAULT_WEAK_CODE_BLOCKLIST: Final[list[str]] = []
DEFAULT_PER_CREDENTIAL_ENTITIES: Final = True
DEFAULT_AUDIT_LOG_SIZE: Final = 1000
DEFAULT_LOG_FAILED_PLAINTEXT: Final = False
DEFAULT_CODE_LENGTH: Final = 6

# Per-scope defaults. Lockout belongs to the door rather than the integration -- a
# keypad on the street and a panel in the hallway want different answers -- so these
# are only ever starting points for a scope's own settings.
DEFAULT_INTER_KEY_TIMEOUT: Final = 10.0  # seconds
DEFAULT_TERMINATOR_KEYS: Final[list[str]] = ["#"]
DEFAULT_LOCKOUT_THRESHOLD: Final = 5
DEFAULT_LOCKOUT_DURATION: Final = 300  # seconds

# How many recent use timestamps to retain per credential for rate limiting.
MAX_RECENT_USES: Final = 100


class CodeType(StrEnum):
    """The kind of secret a credential holds."""

    PIN = "pin"
    ALPHANUMERIC = "alphanumeric"


class StoreMethod(StrEnum):
    """How a credential's secret is held.

    Only ever a readback of ``keep_viewable``: the lookup index is always there, and
    what varies is whether a copy in clear sits beside it.
    """

    PLAINTEXT = "plaintext"
    HASHED = "hashed"


class GraceMode(StrEnum):
    """How a credential's re-entry grace window is anchored.

    The grace period lets somebody back through a door without using another of the
    code's uses up. What differs between the two modes is only *which use the window
    is measured from*, and with it whether the window can be walked forward.
    """

    #: Measured from the use that was counted against ``max_uses``, and never
    #: extended. Five minutes' grace on a one-time code means five minutes.
    FIXED = "fixed"
    #: Measured from the last use of any kind, counted or not, so the window restarts
    #: on every re-entry and the code stays usable while the gaps stay short.
    SLIDING = "sliding"


#: A window that cannot be walked forward is the safer default: a sliding one puts no
#: upper bound on how long a single-use code stays alive.
DEFAULT_GRACE_MODE: Final = GraceMode.FIXED


class Outcome(StrEnum):
    """The result of a submission."""

    VALID = "valid"
    INVALID = "invalid"


class RejectionReason(StrEnum):
    """Why a submission was refused.

    Surfaced on the event entity so automations can react differently to, say, an
    expired guest code versus somebody guessing at the keypad.
    """

    UNKNOWN_CODE = "unknown_code"
    NO_GRANT = "no_grant"
    DISABLED = "disabled"
    REVOKED = "revoked"
    WRONG_SOURCE = "wrong_source"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"
    OUT_OF_SCHEDULE = "out_of_schedule"
    CONDITION_FAILED = "condition_failed"
    MAX_USES_REACHED = "max_uses_reached"
    RATE_LIMITED = "rate_limited"
    LOCKED_OUT = "locked_out"


class EventType(StrEnum):
    """Event types fired by a scope's event entity."""

    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"
    LOCKOUT = "lockout"


#: Rejection reasons that get their own event type rather than the generic ``invalid``.
#: Keeps the common "somebody typed the wrong code" case distinct from "a real code
#: has run out", which usually wants a different automation.
REASON_TO_EVENT_TYPE: Final[dict[RejectionReason, EventType]] = {
    RejectionReason.EXPIRED: EventType.EXPIRED,
    RejectionReason.RATE_LIMITED: EventType.RATE_LIMITED,
    RejectionReason.LOCKED_OUT: EventType.LOCKOUT,
}


class Source(StrEnum):
    """Where a submission came from. Scopes may restrict credentials to a subset."""

    UI = "ui"
    KEYPAD = "keypad"
    SERVICE = "service"
    WEBHOOK = "webhook"
    UNKNOWN = "unknown"


# Bus event fired for every submission, so recorder and logbook pick it up.
EVENT_SUBMISSION: Final = f"{DOMAIN}_submission"

# Service names.
SERVICE_SUBMIT: Final = "submit"
SERVICE_SUBMIT_KEY: Final = "submit_key"
SERVICE_CLEAR_BUFFER: Final = "clear_buffer"
SERVICE_CREATE_CODE: Final = "create_code"
SERVICE_CREATE_OTP: Final = "create_otp"
SERVICE_REVOKE: Final = "revoke"
SERVICE_SET_ENABLED: Final = "set_enabled"
SERVICE_TEST_CODE: Final = "test_code"
SERVICE_EXPORT_AUDIT: Final = "export_audit"

# Common attribute / field names.
ATTR_SCOPE_ID: Final = "scope_id"
ATTR_KEYPAD_ID: Final = "keypad_id"
ATTR_CREDENTIAL_ID: Final = "credential_id"
ATTR_CODE: Final = "code"
ATTR_KEY: Final = "key"
ATTR_LABEL: Final = "label"
ATTR_PERSON: Final = "person"
ATTR_SOURCE: Final = "source"
ATTR_REASON: Final = "reason"
ATTR_OUTCOME: Final = "outcome"
#: Reported on accepted uses only: a refused code was excused from nothing.
ATTR_IN_GRACE_PERIOD: Final = "in_grace_period"
