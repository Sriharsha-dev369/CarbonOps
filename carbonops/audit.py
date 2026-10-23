"""Append-only, hash-chained audit log (one JSON object per line).

Each event stores the previous event's hash, so editing any line breaks the chain from there on.
"""

import hashlib
import json
from pathlib import Path

from carbonops.models import AuditEvent


def _hash(event_without_hash: dict) -> str:
    return hashlib.sha256(json.dumps(event_without_hash, sort_keys=True).encode()).hexdigest()


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def append(event: AuditEvent, path: str | Path) -> str:
    """Append `event` to the JSONL file at `path` and return its hash.

    prev_hash = hash of the last line ('' if none); hash = sha256(json of event without 'hash', sort_keys=True).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = _lines(path)
    body = event.model_dump(mode='json', exclude={'hash'})
    body['prev_hash'] = json.loads(lines[-1])['hash'] if lines else ''
    digest = _hash(body)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps({**body, 'hash': digest}, sort_keys=True) + '\n')
    return digest


def verify(path: str | Path) -> tuple[bool, int | None]:
    """(True, None) if every hash and prev_hash link checks out, else (False, index of the first broken line)."""
    prev = ''
    for i, line in enumerate(_lines(Path(path))):
        try:
            event = json.loads(line)
            body = {k: v for k, v in event.items() if k != 'hash'}
            if event.get('prev_hash') != prev or _hash(body) != event.get('hash'):
                return False, i
        except (json.JSONDecodeError, AttributeError):
            return False, i
        prev = event['hash']
    return True, None


def read_all(path: str | Path) -> list[AuditEvent]:
    return [AuditEvent.model_validate_json(line) for line in _lines(Path(path))]
