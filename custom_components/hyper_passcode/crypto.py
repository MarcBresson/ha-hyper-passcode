"""Secret handling and code generation.

Standard library only, and free of Home Assistant imports, so the security-critical
parts can be reasoned about and tested in isolation.

A note on what the hashing here does and does not buy you. Credentials are matched via
``HMAC-SHA256(code, integration_key)``, stored per credential as a lookup index. That
gives O(1) matching -- with per-credential salted hashes you would have to hash the
submission once per stored credential, which at a few hundred credentials and a proper
slow KDF takes seconds per keypad entry.

The trade-off is deliberate and worth stating plainly: PINs are low-entropy, and the
key lives in ``.storage`` right next to the data it protects. This defends against
casually reading a backup. It does not defend against an attacker with filesystem
access, and the documentation must not imply otherwise.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable, Sequence
from hashlib import sha256
from itertools import pairwise

from .const import CodeType

#: Digits, minus nothing -- keypads universally have 0-9.
PIN_ALPHABET = "0123456789"

#: Upper-case letters and digits with the usual confusable pairs removed
#: (0/O, 1/I/L, 2/Z, 5/S, 8/B) so a code can be read aloud or off a screen.
UNAMBIGUOUS_ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"

#: Full alphanumeric set, used when ambiguity avoidance is turned off.
FULL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

#: Codes that are weak regardless of the sequential/repeated rules. Small and
#: deliberately uncontroversial -- the point is to catch the handful of codes that
#: appear at the top of every leaked-PIN analysis, not to nag.
COMMON_CODES = frozenset(
    {
        "1004",
        "1010",
        "1122",
        "1212",
        "1313",
        "2000",
        "2001",
        "2580",  # straight down the middle column of a keypad
        "6969",
        "112233",
        "121212",
        "123123",
    }
)


class CodeGenerationError(RuntimeError):
    """Raised when no code satisfying every constraint could be generated."""


def generate_integration_key() -> str:
    """Create the random key backing every lookup index.

    Generated once at setup and stored alongside the data.
    """
    return secrets.token_hex(32)


def compute_lookup_index(code: str, key: str) -> str:
    """Return the HMAC lookup index for ``code``."""
    return hmac.new(key.encode(), code.encode(), sha256).hexdigest()


def verify(code: str, lookup_index: str, key: str) -> bool:
    """Check ``code`` against a stored lookup index in constant time."""
    return hmac.compare_digest(compute_lookup_index(code, key), lookup_index)


def find_weakness(code: str, blocklist: Sequence[str] = ()) -> str | None:
    """Return a short reason if ``code`` is weak, otherwise None.

    Covers the rules named in the integration settings: repeated digits, sequential
    runs, blocklist entries, and a small set of well-known codes. Callers enforce any
    minimum length themselves; a one-character code is not reported here because every
    rule below would trivially match it.
    """
    if code in blocklist:
        return "blocklisted"

    if len(code) < 2:
        return None

    if len(set(code)) == 1:
        return "repeated_characters"

    if _is_sequential(code):
        return "sequential"

    if code in COMMON_CODES:
        return "common_code"

    return None


def _is_sequential(code: str) -> bool:
    """Return True when every character steps by +1 or -1 from the previous one."""
    if not code.isdigit():
        return False
    deltas = {int(b) - int(a) for a, b in pairwise(code)}
    return deltas in ({1}, {-1})


def generate_code(
    length: int,
    *,
    code_type: CodeType = CodeType.PIN,
    avoid_ambiguous: bool = True,
    exclude_characters: str = "",
    forbidden_prefixes: Sequence[str] = (),
    reject_weak: bool = True,
    blocklist: Sequence[str] = (),
    is_taken: Callable[[str], bool] | None = None,
    max_attempts: int = 1000,
) -> str:
    """Generate a random code satisfying every configured constraint.

    ``exclude_characters`` and ``forbidden_prefixes`` exist because real keypad
    hardware imposes rules of its own -- some models reject a leading zero, others
    reserve a prefix. Making them options rather than assumptions keeps the
    integration vendor-neutral.

    ``is_taken`` is how collision refusal is enforced: the caller passes a predicate
    that reports whether a code already exists, and generation retries until it finds
    a free one.

    Raises:
        CodeGenerationError: if the constraints leave nothing to pick from, or no
            valid code turned up within ``max_attempts``.
    """
    if length < 1:
        raise CodeGenerationError("Code length must be at least 1")

    alphabet = _build_alphabet(code_type, avoid_ambiguous, exclude_characters)
    if not alphabet:
        raise CodeGenerationError("No characters left to build a code from")
    if len(alphabet) == 1 and length > 1 and reject_weak:
        # Every possible code would be a run of the same character.
        raise CodeGenerationError(
            "Only one character available, so every code would be rejected as weak"
        )

    for _ in range(max_attempts):
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))

        if any(candidate.startswith(prefix) for prefix in forbidden_prefixes if prefix):
            continue
        if reject_weak and find_weakness(candidate, blocklist) is not None:
            continue
        if is_taken is not None and is_taken(candidate):
            continue

        return candidate

    raise CodeGenerationError(
        f"Could not generate a code meeting every constraint in {max_attempts} attempts"
    )


def _build_alphabet(
    code_type: CodeType, avoid_ambiguous: bool, exclude_characters: str
) -> str:
    """Resolve the character set a generated code may draw from."""
    if code_type is CodeType.PIN:
        base = PIN_ALPHABET
    else:
        base = UNAMBIGUOUS_ALPHABET if avoid_ambiguous else FULL_ALPHABET

    excluded = set(exclude_characters.upper()) | set(exclude_characters.lower())
    return "".join(char for char in base if char not in excluded)
