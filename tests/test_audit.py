import hashlib
import json

from carbonops import audit
from carbonops.models import AuditEvent


def _event(i: int) -> AuditEvent:
    return AuditEvent(ts=f'2026-10-24T10:00:0{i}+00:00', actor='system', role='system',
                      kind='finding', payload={'n': i})


def _append_three(path) -> list[str]:
    return [audit.append(_event(i), path) for i in range(3)]


def test_chain_links_and_verifies(tmp_path):
    path = tmp_path / 'audit.jsonl'
    hashes = _append_three(path)

    lines = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert len(lines) == 3
    assert [e['hash'] for e in lines] == hashes
    assert [e['prev_hash'] for e in lines] == ['', hashes[0], hashes[1]]
    assert audit.verify(path) == (True, None)


def test_hash_is_sha256_of_event_without_hash(tmp_path):
    path = tmp_path / 'audit.jsonl'
    _append_three(path)
    first = json.loads(path.read_text(encoding='utf-8').splitlines()[0])
    body = {k: v for k, v in first.items() if k != 'hash'}
    assert first['hash'] == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def test_tampered_payload_is_found(tmp_path):
    path = tmp_path / 'audit.jsonl'
    _append_three(path)

    lines = path.read_text(encoding='utf-8').splitlines()
    event = json.loads(lines[1])
    event['payload']['n'] = 99
    lines[1] = json.dumps(event)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    assert audit.verify(path) == (False, 1)
