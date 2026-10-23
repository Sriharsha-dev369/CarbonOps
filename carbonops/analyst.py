"""Analyst agent: one batched LLM call picks an action per finding; rules take over on any failure.

The LLM sees only resource metadata, evidence and tags (marked untrusted), never sim state or
account details, and never computes money or carbon. Everything it returns is validated; anything
invalid (unknown resource, disallowed type such as TERMINATE, bad params) falls back to the rules.
"""

import json
import logging

from pydantic import BaseModel, ValidationError, field_validator

from carbonops import config, detector, llm
from carbonops.config import Policy, Prices
from carbonops.models import Action, Finding, Resource

log = logging.getLogger(__name__)

SCHEMA_HINT = (
    '{"actions": [{"resource_id": str, "type": "STOP"|"SCHEDULE"|"RESIZE"|"SNAPSHOT"|"KEEP", '
    '"params": object, "reason": str, "confidence": number 0-1}]}'
)
NEVER = ('DELETE', 'TERMINATE')
VM_ACTIONS = ('STOP', 'SCHEDULE', 'RESIZE', 'KEEP')
VOLUME_ACTIONS = ('SNAPSHOT', 'KEEP')


class ProposedAction(BaseModel):
    resource_id: str
    type: str
    params: dict = {}
    reason: str = ''
    confidence: float

    @field_validator('confidence')
    @classmethod
    def _clamp(cls, v: float) -> float:
        return min(1.0, max(0.0, v))


def system_prompt() -> str:
    return (config.ROOT / 'prompts' / 'analyst.md').read_text(encoding='utf-8')


def allowed_types(policy: Policy) -> list[str]:
    return [t for t in policy.allowed_actions if t not in policy.never and t not in NEVER]


def build_payload(findings: list[Finding], resources: dict[str, Resource], policy: Policy, prices: Prices) -> dict:
    """The only data the LLM sees. Tags go in `untrusted_tags`; no sim state, account ids or IPs."""
    items = []
    for f in findings:
        r = resources[f.resource_id]
        item = {
            'id': r.id, 'kind': r.kind, 'env': r.env, 'owner': r.owner,
            'type': r.instance_type if r.kind == 'vm' else f'{r.size_gb} GB volume',
            'finding': f.kind, 'evidence': f.evidence, 'untrusted_tags': r.tags,
        }
        if r.kind == 'vm':
            item['next_size_down'] = prices.downsize.get(r.instance_type)
        items.append(item)
    return {'client': policy.name, 'allowed_actions': allowed_types(policy),
            'office_hours_per_week': policy.office_hours.hours_per_week, 'findings': items}


def _params(p: ProposedAction, r: Resource, prices: Prices, policy: Policy) -> dict | None:
    """Validated params for the proposal, or None if it cannot be executed safely."""
    if p.type in ('STOP', 'SNAPSHOT', 'KEEP'):
        return {}
    if p.type == 'SCHEDULE':
        hours = p.params.get('hours_per_week', policy.office_hours.hours_per_week)
        ok = isinstance(hours, (int, float)) and not isinstance(hours, bool) and 1 <= hours < 168
        return {'hours_per_week': hours if ok else policy.office_hours.hours_per_week}
    if p.type == 'RESIZE':
        current = prices.instances.get(r.instance_type)
        target = p.params.get('to')
        if current and target in prices.instances and prices.instances[target].vcpu < current.vcpu:
            return {'to': target}
        fallback = prices.downsize.get(r.instance_type)
        return {'to': fallback} if fallback else None
    return None


def propose(findings: list[Finding], resources: dict[str, Resource], policy: Policy, *,
            notes: list[str] | None = None) -> list[Action]:
    """Return one Action per finding: the LLM's when valid, otherwise detector.default_action.

    `notes` (optional) receives one-line, human-readable notes for the agent trace.
    """
    notes = notes if notes is not None else []
    if not findings:
        return []
    prices = config.load_prices()
    rules = {f.resource_id: detector.default_action(f, resources[f.resource_id], prices, policy) for f in findings}
    payload = build_payload(findings, resources, policy, prices)
    try:
        reply = llm.chat_json(system_prompt(), json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                              schema_hint=SCHEMA_HINT, replay_key=f'analyst_{policy.client}', timeout=20)
    except Exception as e:
        log.warning('analyst LLM call failed: %s', e)
        notes.append(f'LLM unavailable — rules used ({type(e).__name__}: {e})')
        return list(rules.values())

    raw = reply.get('actions')
    if not isinstance(raw, list):
        notes.append('LLM reply had no "actions" list — rules used')
        return list(rules.values())

    allowed = allowed_types(policy)
    chosen: dict[str, Action] = {}
    rejected: list[str] = []
    for item in raw:
        try:
            p = ProposedAction.model_validate(item)
        except ValidationError:
            rejected.append(f'malformed entry {str(item)[:60]}')
            continue
        r = resources.get(p.resource_id)
        if p.resource_id not in rules or p.resource_id in chosen or r is None:
            rejected.append(f'{p.resource_id}: not one of the findings')
            continue
        if p.type not in allowed or p.type not in (VM_ACTIONS if r.kind == 'vm' else VOLUME_ACTIONS):
            rejected.append(f'{p.resource_id}: {p.type} is not allowed here')
            continue
        params = _params(p, r, prices, policy)
        if params is None:
            rejected.append(f'{p.resource_id}: {p.type} params unusable')
            continue
        chosen[p.resource_id] = Action(
            id=f'{p.resource_id}:{p.type}', resource_id=p.resource_id, type=p.type, params=params,
            reason=p.reason.strip()[:400] or 'No reason given.', confidence=p.confidence, proposed_by='llm',
        )

    missing = [rid for rid in rules if rid not in chosen]
    note = f'LLM proposed {len(chosen)} of {len(rules)} actions'
    if missing:
        note += f'; rules used for {", ".join(missing)}'
    if rejected:
        note += f'; rejected: {"; ".join(rejected)}'
    notes.append(note + '.')
    return [chosen.get(f.resource_id, rules[f.resource_id]) for f in findings]
