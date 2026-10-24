"""Ask CarbonOps: answer a question about one run from its audit log and totals only.

Retrieval first (resource ids and keywords over this run's audit events, at most 20), then one small
LLM call that must cite events and may only quote given numbers. Without the LLM, or if its answer
fails the checks, the caller shows the retrieved events instead.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from carbonops import config, llm, reporter
from carbonops.models import AuditEvent, ExecResult

log = logging.getLogger(__name__)

CHIPS = ['Why was sandbox-ml blocked?', 'What did we save?', 'What was rolled back and why?',
         'Which actions need approval?']
NOT_FOUND = "That isn't in the audit log for this run."
MAX_EVENTS = 20
MAX_WORDS = 80
SCHEMA_HINT = '{"answer": str (at most 80 words, cites events as [e14]), "cited": [str]}'

# Question words -> the events they are about.
TOPICS: list[tuple[tuple[str, ...], Callable[[AuditEvent], bool]]] = [
    (('block',), lambda e: (e.kind == 'decision' and e.payload.get('tier') == 'BLOCK') or e.kind == 'redteam_attack'),
    (('save', 'saving', 'saved', '₹', 'money', 'cost', 'carbon', 'co2'), lambda e: e.kind in ('done', 'report', 'undo')),
    (('roll', 'revert', 'fail', 'health', 'latency'), lambda e: e.kind in ('rollback', 'health_check', 'failed')),
    (('approv', 'human', 'pending', 'waiting', 'review'),
     lambda e: (e.kind == 'decision' and e.payload.get('tier') == 'APPROVE') or e.kind == 'approval'),
    (('undo', 'undone', 'revers'), lambda e: e.kind == 'undo'),
    (('red team', 'attack', 'inject', 'tag', 'instruction'), lambda e: e.kind.startswith('redteam')),
    (('find', 'idle', 'waste', 'detect'), lambda e: e.kind == 'finding'),
]


class AskOutput(BaseModel):
    answer: str
    cited: list[str] = []


def run_events(events: list[AuditEvent]) -> list[AuditEvent]:
    """Events of the latest run (from its `run` event on), without earlier questions."""
    start = max((i for i, e in enumerate(events) if e.kind == 'run'), default=0)
    return [e for e in events[start:] if e.kind != 'ask']


def _mentions(e: AuditEvent, resource_id: str) -> bool:
    p = e.payload
    ids = [p.get('resource_id'), str(p.get('action_id', '')).split(':')[0], *(p.get('resource_ids') or [])]
    return resource_id in ids


def retrieve(question: str, events: list[AuditEvent], resource_ids: list[str]) -> list[tuple[str, AuditEvent]]:
    """At most MAX_EVENTS (id, event) pairs, in log order. Ids are e<index in `events`>."""
    q = question.lower()
    named = [rid for rid in resource_ids if rid.lower() in q]
    topics = [match for words, match in TOPICS if any(w in q for w in words)]
    scored = []
    for i, e in enumerate(events):
        score = 3 * sum(_mentions(e, rid) for rid in named) + 2 * sum(match(e) for match in topics)
        if named and topics and not any(_mentions(e, rid) for rid in named):
            score -= 1                        # about another resource: keep only if nothing better
        if score > 0:
            scored.append((score, i, e))
    top = sorted(scored, key=lambda t: (-t[0], t[1]))[:MAX_EVENTS]
    return [(f'e{i}', e) for _, i, e in sorted(top, key=lambda t: t[1])]


def _compact(event_id: str, e: AuditEvent) -> dict:
    """Event for the prompt: no hashes, timestamps or restore-point ids (they change every run)."""
    p = {k: v for k, v in e.payload.items() if k not in ('snapshot_id', 'totals')}
    for key in ('reason', 'detail', 'error'):
        if isinstance(p.get(key), str):
            p[key] = re.sub(r'\s*Restored from \S+?\.?$', '', p[key])
    out = {'id': event_id, 'kind': e.kind}
    if e.actor != 'system':
        out['by'] = f'{e.actor} ({e.role})'
    return {**out, **p}


def build_payload(question: str, client: str, picked: list[tuple[str, AuditEvent]],
                  results: list[ExecResult], totals: dict[str, Any]) -> dict:
    return {'question': question.strip(), 'summary': reporter.build_payload(client, results, totals),
            'events': [_compact(i, e) for i, e in picked]}


def answer(question: str, client: str, events: list[AuditEvent], resource_ids: list[str],
           results: list[ExecResult], totals: dict[str, Any], *, use_llm: bool) -> dict[str, Any]:
    """{'answer': str | None, 'cited': [ids], 'events': [(id, event)], 'source': 'llm' | 'events', 'note': str}.

    `answer` is None when the LLM is off or failed; show `events` then.
    """
    picked = retrieve(question, run_events(events), resource_ids)
    out: dict[str, Any] = {'answer': None, 'cited': [], 'events': picked, 'source': 'events', 'note': ''}
    if not use_llm:
        out['note'] = 'LLM off: matching audit events shown.'
        return out
    payload = build_payload(question, client, picked, results, totals)
    key = f"ask_{client}_{hashlib.sha256(question.strip().lower().encode()).hexdigest()[:10]}"
    try:
        system = (config.ROOT / 'prompts' / 'ask.md').read_text(encoding='utf-8')
        reply = llm.chat_json(system, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                              schema_hint=SCHEMA_HINT, replay_key=key, timeout=20)
        result = AskOutput.model_validate(reply)
        text = result.answer.strip()
        if not text or len(text.split()) > MAX_WORDS + 20:
            raise ValueError(f'answer has {len(text.split())} words')
        if text != NOT_FOUND and not reporter.numbers_are_given(text, payload):
            raise ValueError('answer contains a number that is not in the data')
        known = {i for i, _ in picked}
        cited = dict.fromkeys([*result.cited, *re.findall(r'\be\d+\b', text)])
        out.update(answer=text, cited=[c for c in cited if c in known], source='llm')
    except Exception as e:
        log.warning('ask LLM failed: %s', e)
        reason = 'no recording for this question and run' if isinstance(e, llm.ReplayMissing) else type(e).__name__
        out['note'] = f'LLM answer unavailable ({reason}): matching audit events shown.'
    return out
