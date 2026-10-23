"""Append-only, hash-chained audit log (one JSON object per line). STUB: written on the day."""

from pathlib import Path

from carbonops.models import AuditEvent


def append(event: AuditEvent, path: str | Path) -> str:
    """Append `event` to the JSONL file at `path` and return its hash.

    prev_hash = hash of the last line in the file ('' if the file is empty or missing).
    hash      = sha256 hex of json.dumps(event without 'hash', sort_keys=True).
    Writes exactly one JSON line. Creates the file and parent folder if needed.
    """
    raise NotImplementedError


def verify(path: str | Path) -> tuple[bool, int | None]:
    """Re-check every line's hash and prev_hash link.

    Returns (True, None) if the chain is intact,
    or (False, i) where i is the 0-based index of the first broken line.
    """
    raise NotImplementedError
