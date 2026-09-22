"""Packaging consistency.

A community integration lives or dies on its metadata being right, and these are the
mistakes that are easy to make and invisible until a user hits them: a service with no
description in the UI, a settings field nobody translated, a manifest key HACS rejects.
"""

import json
from pathlib import Path

import pytest
import yaml
from homeassistant.core import HomeAssistant

from custom_components.hyper_passcode.const import DOMAIN

COMPONENT = Path("custom_components/hyper_passcode")
REQUIRED_MANIFEST_KEYS = {
    "domain",
    "name",
    "codeowners",
    "documentation",
    "iot_class",
    "version",
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((COMPONENT / "manifest.json").read_text())


@pytest.fixture(scope="module")
def services_yaml() -> dict:
    return yaml.safe_load((COMPONENT / "services.yaml").read_text())


@pytest.fixture(scope="module")
def translations() -> dict:
    return json.loads((COMPONENT / "translations" / "en.json").read_text())


def test_manifest_has_the_keys_hacs_requires(manifest):
    assert manifest.keys() >= REQUIRED_MANIFEST_KEYS
    assert manifest["domain"] == DOMAIN
    assert manifest["version"]


def test_hacs_manifest_is_valid():
    hacs = json.loads(Path("hacs.json").read_text())
    assert hacs["name"]


async def test_every_service_is_described(
    hass: HomeAssistant, entry, services_yaml, translations
):
    registered = set(hass.services.async_services_for_domain(DOMAIN))

    assert registered == set(services_yaml), (
        "services.yaml and the registered services have drifted apart"
    )
    assert registered == set(translations["services"]), (
        "every service needs a name and description or the UI shows a bare key"
    )


def test_every_service_field_is_translated(services_yaml, translations):
    for service, spec in services_yaml.items():
        documented = set(translations["services"][service].get("fields", {}))
        declared = set((spec or {}).get("fields", {}))
        assert declared == documented, f"field translations drifted for {service}"


def test_every_settings_field_is_translated(translations):
    # The options schema is built inside the step, so assert against the constants
    # that define it instead.
    from custom_components.hyper_passcode import const

    setting_keys = {
        value
        for name, value in vars(const).items()
        if name.startswith("CONF_") and isinstance(value, str)
    }
    documented = set(translations["options"]["step"]["settings"]["data"])
    assert setting_keys == documented, "an integration setting is missing a label"


def test_every_entity_translation_key_exists(translations):
    entity = translations["entity"]
    assert set(entity["sensor"]) == {
        "last_used",
        "last_result",
        "failed_attempts",
        "uses",
        "uncounted_uses",
        "last_uncounted_use",
        "code",
        "store_method",
    }
    assert set(entity["binary_sensor"]) == {"lockout"}
    assert set(entity["switch"]) == {"enabled", "keep_viewable"}
    assert set(entity["button"]) == {
        "generate_delivery_code",
        "clear_validity_window",
    }
    assert set(entity["select"]) == {"grace_mode"}
    assert set(entity["datetime"]) == {"valid_from", "valid_until"}
    assert set(entity["text"]) == {"terminator_keys", "notes"}
    assert set(entity["event"]) == {"code"}


def test_every_grace_mode_has_a_label(translations):
    # The dropdown's option labels are the only place the difference between the two
    # modes is explained at the point it is chosen, so an untranslated one leaves the
    # user picking between a bare "fixed" and a bare "sliding".
    from custom_components.hyper_passcode.const import GraceMode

    assert set(translations["entity"]["select"]["grace_mode"]["state"]) == {
        str(mode) for mode in GraceMode
    }


def test_every_repair_issue_is_described(translations):
    # A repair with no translation shows as a bare key in Settings > Repairs, which
    # is worse than useless for something raised to warn about a leaked code.
    from custom_components.hyper_passcode import coordinator

    assert set(translations["issues"]) == {coordinator.ISSUE_TRANSLATION_KEY}


def test_every_editable_setting_entity_is_named(translations):
    # These carry the settings that used to be dialog fields, so an untranslated one
    # shows up on a device page as a bare key where a label used to be.
    from custom_components.hyper_passcode import number

    declared = (
        {description.key for description in number.SCOPE_NUMBERS}
        | {description.key for description in number.KEYPAD_NUMBERS}
        | {description.key for description in number.POLICY_NUMBERS}
    )
    assert declared == set(translations["entity"]["number"])


def test_device_automation_types_are_translated(translations):
    from custom_components.hyper_passcode import device_condition, device_trigger

    automation = translations["device_automation"]
    assert set(device_trigger.TRIGGER_TYPES) == set(automation["trigger_type"])
    assert set(automation["condition_type"]) == device_condition.CONDITION_TYPES


def test_every_rejection_reason_can_be_reported(translations):
    from custom_components.hyper_passcode.const import (
        EventType,
        Outcome,
        RejectionReason,
    )

    # On the event entity reasons are raw values, so only the event types they map
    # to have to be declared.
    declared = set(
        translations["entity"]["event"]["code"]["state_attributes"]["event_type"][
            "state"
        ]
    )
    assert {str(event_type) for event_type in EventType} == declared

    # On the last-result sensor they are enum states, and an enum sensor raises on a
    # state outside its options -- so a new reason with no entry here would take the
    # entity down rather than merely show a bare key.
    assert set(translations["entity"]["sensor"]["last_result"]["state"]) == {
        str(Outcome.VALID)
    } | {str(reason) for reason in RejectionReason}
