"""The audit ring buffer and its renderers."""

import json
from datetime import UTC, datetime, timedelta

from custom_components.hyper_passcode import audit
from custom_components.hyper_passcode.const import Outcome
from custom_components.hyper_passcode.models import AuditEntry

START = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def entry(minute: int, scope_id: str = "s1", outcome=Outcome.VALID) -> AuditEntry:
    return AuditEntry(
        timestamp=START + timedelta(minutes=minute),
        scope_id=scope_id,
        outcome=outcome,
        label=f"entry-{minute}",
    )


def test_append_trims_the_oldest_past_the_cap():
    entries: list[AuditEntry] = []
    for minute in range(10):
        audit.append(entries, entry(minute), max_size=4)

    assert len(entries) == 4
    assert [e.label for e in entries] == [
        "entry-6",
        "entry-7",
        "entry-8",
        "entry-9",
    ]


def test_a_zero_cap_means_unbounded():
    entries: list[AuditEntry] = []
    for minute in range(5):
        audit.append(entries, entry(minute), max_size=0)
    assert len(entries) == 5


def test_recent_returns_newest_first():
    entries = [entry(minute) for minute in range(5)]
    assert [e.label for e in audit.recent(entries)] == [
        "entry-4",
        "entry-3",
        "entry-2",
        "entry-1",
        "entry-0",
    ]


def test_recent_filters_and_limits():
    entries = [entry(0, "s1"), entry(1, "s2"), entry(2, "s1")]

    by_scope = audit.recent(entries, scope_id="s1")
    assert [e.label for e in by_scope] == ["entry-2", "entry-0"]

    assert len(audit.recent(entries, limit=2)) == 2


def test_renderers_preserve_the_order_they_are_given():
    # They must not re-sort: callers pass an already-ordered list from recent(),
    # and sorting again would silently reverse it back to oldest-first.
    ordered = audit.recent([entry(0), entry(1), entry(2)])
    labels = [e.label for e in ordered]

    assert [row["label"] for row in audit.as_dicts(ordered)] == labels
    assert [row["label"] for row in json.loads(audit.to_json(ordered))] == labels

    csv_lines = audit.to_csv(ordered).strip().splitlines()
    assert csv_lines[0].startswith("timestamp,scope_id,outcome")
    assert [line.split(",")[5] for line in csv_lines[1:]] == labels


def test_an_export_says_which_uses_a_grace_period_excused():
    graced = entry(0)
    graced.in_grace = True
    rendered = [graced, entry(1)]

    assert [row["in_grace"] for row in json.loads(audit.to_json(rendered))] == [
        True,
        False,
    ]

    csv_lines = audit.to_csv(rendered).strip().splitlines()
    assert csv_lines[0].endswith(",in_grace")
    assert [line.split(",")[-1] for line in csv_lines[1:]] == ["True", "False"]
