"""Tests for secret handling and code generation."""

import pytest

from custom_components.hyper_passcode.const import CodeType
from custom_components.hyper_passcode.crypto import (
    CodeGenerationError,
    compute_lookup_index,
    find_weakness,
    generate_code,
    generate_integration_key,
    verify,
)


def test_lookup_index_is_stable_and_key_dependent():
    key_a = generate_integration_key()
    key_b = generate_integration_key()

    assert compute_lookup_index("1379", key_a) == compute_lookup_index("1379", key_a)
    assert compute_lookup_index("1379", key_a) != compute_lookup_index("1379", key_b)
    assert compute_lookup_index("1379", key_a) != compute_lookup_index("1380", key_a)


def test_verify_matches_only_the_right_code():
    key = generate_integration_key()
    index = compute_lookup_index("8317", key)

    assert verify("8317", index, key) is True
    assert verify("8318", index, key) is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("1234", "sequential"),
        ("4321", "sequential"),
        ("123456", "sequential"),
        ("0000", "repeated_characters"),
        ("7777", "repeated_characters"),
        ("1212", "common_code"),
        ("2580", "common_code"),
        ("8317", None),
        ("495162", None),
    ],
)
def test_find_weakness(code, expected):
    assert find_weakness(code) == expected


def test_blocklist_is_honoured():
    assert find_weakness("8317", ["8317"]) == "blocklisted"
    assert find_weakness("8317", ["9999"]) is None


def test_single_character_code_reports_nothing():
    # Every rule would trivially match; callers enforce minimum length themselves.
    assert find_weakness("7") is None


def test_generated_pin_respects_length_and_alphabet():
    code = generate_code(6, code_type=CodeType.PIN)

    assert len(code) == 6
    assert code.isdigit()
    assert find_weakness(code) is None


def test_generation_avoids_excluded_characters_and_prefixes():
    for _ in range(50):
        code = generate_code(6, exclude_characters="0", forbidden_prefixes=["12", "99"])
        assert "0" not in code
        assert not code.startswith(("12", "99"))


def test_generation_skips_taken_codes():
    taken = {"111111"}
    code = generate_code(6, is_taken=lambda candidate: candidate in taken)
    assert code not in taken


def test_generation_gives_up_rather_than_looping_forever():
    with pytest.raises(CodeGenerationError):
        generate_code(4, is_taken=lambda _candidate: True, max_attempts=20)


def test_generation_refuses_an_impossible_alphabet():
    with pytest.raises(CodeGenerationError):
        generate_code(4, exclude_characters="0123456789")


def test_weak_codes_can_be_generated_when_the_rule_is_off():
    # With one character available every code repeats, so this only succeeds
    # because weak-code rejection is disabled.
    code = generate_code(4, exclude_characters="012345678", reject_weak=False)
    assert code == "9999"
