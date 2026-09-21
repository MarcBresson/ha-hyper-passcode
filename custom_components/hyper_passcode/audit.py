"""The append-only submission log.

Kept as a bounded ring buffer in the store. Every submission also fires a bus event,
so recorder and logbook get a full history without this buffer having to grow without
limit.
"""

import csv
import io
import json
from collections.abc import Iterable
from typing import Any

from .models import AuditEntry

CSV_COLUMNS = (
    "timestamp",
    "scope_id",
    "outcome",
    "reason",
    "credential_id",
    "label",
    "person",
    "source",
    "typed",
)


def append(entries: list[AuditEntry], entry: AuditEntry, max_size: int) -> None:
    """Add ``entry``, trimming the oldest records past ``max_size``."""
    entries.append(entry)
    if max_size > 0 and len(entries) > max_size:
        del entries[: len(entries) - max_size]


def recent(
    entries: Iterable[AuditEntry],
    *,
    limit: int | None = None,
    scope_id: str | None = None,
    credential_id: str | None = None,
) -> list[AuditEntry]:
    """Return matching entries, newest first."""
    selected = [
        entry
        for entry in entries
        if (scope_id is None or entry.scope_id == scope_id)
        and (credential_id is None or entry.credential_id == credential_id)
    ]
    selected.reverse()
    return selected[:limit] if limit is not None else selected


#: The renderers below deliberately preserve the order they are given rather than
#: sorting again. Ordering and filtering belong to ``recent``; re-applying it here
#: would silently reverse an already-ordered list.


def to_csv(entries: Iterable[AuditEntry]) -> str:
    """Render entries as CSV, in the order given."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for entry in entries:
        writer.writerow(entry.to_dict())
    return buffer.getvalue()


def to_json(entries: Iterable[AuditEntry]) -> str:
    """Render entries as JSON, in the order given."""
    return json.dumps([entry.to_dict() for entry in entries], indent=2)


def as_dicts(entries: Iterable[AuditEntry]) -> list[dict[str, Any]]:
    """Render entries as plain dicts, in the order given."""
    return [entry.to_dict() for entry in entries]
