"""Constants for the HyperPasscode integration."""

from enum import StrEnum
from typing import Final

DOMAIN: Final = "hyper_passcode"

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1

#: Scopes and credentials are config subentries, which is what gives them "Add"
#: buttons on the integration page and configure dialogs of their own.
SUBENTRY_TYPE_SCOPE: Final = "scope"
SUBENTRY_TYPE_CREDENTIAL: Final = "credential"

#: Credential device identifiers are prefixed so they can never be confused with a
#: scope's, whatever ids happen to be generated.
CREDENTIAL_DEVICE_PREFIX: Final = "credential"

# How the two device kinds present themselves in the device registry.
DEVICE_MANUFACTURER: Final = "HyperPasscode"
DEVICE_MODEL_SCOPE: Final = "Passcode scope"
DEVICE_MODEL_CREDENTIAL: Final = "Credential"


def credential_device_identifier(credential_id: str) -> tuple[str, str]:
    """Return the device registry identifier for a credential."""
    return (DOMAIN, f"{CREDENTIAL_DEVICE_PREFIX}_{credential_id}")


# Integration-level settings and their defaults.
CONF_REJECT_WEAK_CODES: Final = "reject_weak_codes"
CONF_WEAK_CODE_BLOCKLIST: Final = "weak_code_blocklist"
CONF_PER_CREDENTIAL_ENTITIES: Final = "per_credential_entities"
CONF_AUDIT_LOG_SIZE: Final = "audit_log_size"
CONF_LOG_FAILED_PLAINTEXT: Final = "log_failed_plaintext"
CONF_DEFAULT_CODE_LENGTH: Final = "default_code_length"
CONF_LOCKOUT_THRESHOLD: Final = "lockout_threshold"
CONF_LOCKOUT_DURATION: Final = "lockout_duration"

DEFAULT_REJECT_WEAK_CODES: Final = True
DEFAULT_WEAK_CODE_BLOCKLIST: Final[list[str]] = []
DEFAULT_PER_CREDENTIAL_ENTITIES: Final = True
DEFAULT_AUDIT_LOG_SIZE: Final = 1000
DEFAULT_LOG_FAILED_PLAINTEXT: Final = False
DEFAULT_CODE_LENGTH: Final = 6
DEFAULT_LOCKOUT_THRESHOLD: Final = 5
DEFAULT_LOCKOUT_DURATION: Final = 300  # seconds

# Per-scope entry defaults.
DEFAULT_INTER_KEY_TIMEOUT: Final = 10.0  # seconds
DEFAULT_TERMINATOR_KEYS: Final[list[str]] = ["#"]

# How many recent use timestamps to retain per credential for rate limiting.
MAX_RECENT_USES: Final = 100


class CodeType(StrEnum):
    """The kind of secret a credential holds."""

    PIN = "pin"
    ALPHANUMERIC = "alphanumeric"


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
SERVICE_REVOKE_ALL_GUESTS: Final = "revoke_all_guests"
SERVICE_EXPORT_AUDIT: Final = "export_audit"

# Common attribute / field names.
ATTR_SCOPE_ID: Final = "scope_id"
ATTR_CREDENTIAL_ID: Final = "credential_id"
ATTR_CODE: Final = "code"
ATTR_KEY: Final = "key"
ATTR_LABEL: Final = "label"
ATTR_PERSON: Final = "person"
ATTR_SOURCE: Final = "source"
ATTR_REASON: Final = "reason"
ATTR_OUTCOME: Final = "outcome"
ATTR_TAGS: Final = "tags"
