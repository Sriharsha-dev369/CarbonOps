"""LangGraph pipeline: ingest → detect → analyse → price → guard → approval → execute → report.

Each node calls its module. While those modules are stubs, run with fake=True (or env
CARBONOPS_FAKE=1): the stubbed steps then return hard-coded demo data of the right types,
so the graph, the approval pause and the app all work end to end.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from carbonops import analyst, audit, calc, config, detector, executor, guard, reporter, seed
from carbonops.models import (
    Action, AuditEvent, Decision, ExecResult, Finding, Metrics, Resource, Role, Tier,
)
from carbonops.providers.aws import AwsProvider

Verdict = Literal['approve', 'reject']
APPROVER_ROLES: tuple[Role, ...] = ('Approver', 'Admin')


class State(TypedDict, total=False):
    client: str
    role: Role
    user: str
    fake: bool
    resources: list[Resource]
    metrics: dict[str, Metrics]
    sims: dict[str, dict]
    aws_ids: dict[str, str]          # resource id -> AWS id, from seed (real mode only)
    findings: list[Finding]
    actions: list[Action]
    decisions: list[Decision]
    approvals: dict[str, Verdict]    # action id -> verdict, for every APPROVE action
    approver: str
    approver_role: Role
    results: list[ExecResult]
    totals: dict[str, Any]
    report: str
    audit_path: str


class Outcome(TypedDict):
    thread_id: str
    state: State
    pending: list[dict]              # actions awaiting approval; empty once the run has finished


def _is_fake(state: State) -> bool:
    return bool(state.get('fake')) or os.environ.get('CARBONOPS_FAKE') == '1'


def _audit(state: State, kind: str, payload: dict, actor: str = 'system',
           role: Role | Literal['system'] = 'system') -> None:
    if _is_fake(state):
        return
    event = AuditEvent(ts=datetime.now(timezone.utc).isoformat(), actor=actor, role=role,
                       kind=kind, payload=payload)
    audit.append(event, state['audit_path'])


# --- nodes -------------------------------------------------------------------

def ingest(state: State) -> State:
    resources, metrics, sims = config.load_estate(state['client'])
    out: State = {
        'resources': resources, 'metrics': metrics, 'sims': sims,
        'audit_path': state.get('audit_path') or str(config.ROOT / 'audit' / f"{state['client']}.jsonl"),
    }
    if not _is_fake(state):
        out['aws_ids'] = seed.seed(resources)
    return out


def detect(state: State) -> State:
    if _is_fake(state):
        findings = _fake_findings(state)
    else:
        findings = detector.detect(state['resources'], state['metrics'], config.load_policy(state['client']))
    for f in findings:
        _audit(state, 'finding', f.model_dump())
    return {'findings': findings}


def analyse(state: State) -> State:
    if _is_fake(state):
        return {'actions': _fake_actions(state)}
    resources = {r.id: r for r in state['resources']}
    actions = analyst.propose(state['findings'], resources, config.load_policy(state['client']),
                              config.load_prices())
    return {'actions': actions}


def price(state: State) -> State:
    if _is_fake(state):
        return {'actions': [_fake_price(a) for a in state['actions']]}
    resources = {r.id: r for r in state['resources']}
    prices, carbon = config.load_prices(), config.load_carbon()
    priced = [calc.price_action(a, resources[a.resource_id], state['metrics'].get(a.resource_id), prices, carbon)
              for a in state['actions']]
    return {'actions': priced}


def guard_node(state: State) -> State:
    actions, decisions = list(state['actions']), []
    if _is_fake(state):
        decisions = [_fake_decision(state, a) for a in actions]
        tag_pairs = _fake_tag_decisions(state)
    else:
        policy = config.load_policy(state['client'])
        resources = {r.id: r for r in state['resources']}
        for a in actions:
            r = resources[a.resource_id]
            decisions.append(guard.evaluate(a, r, policy, guard.is_suspicious(r)))
        tag_pairs = [pair for r in state['resources'] for pair in guard.tag_decisions(r, policy)]
    for a, d in tag_pairs:
        actions.append(a)
        decisions.append(d)
    for d in decisions:
        _audit(state, 'decision', d.model_dump())
    return {'actions': actions, 'decisions': decisions}


def approval(state: State) -> State:
    decisions = {d.action_id: d for d in state['decisions']}
    pending = [a for a in state['actions'] if decisions[a.id].tier == 'APPROVE']
    if not pending:
        return {'approvals': {}}
    answer = interrupt({'pending': [{**a.model_dump(), 'reasons': decisions[a.id].reasons} for a in pending]})
    # Anything not explicitly approved is rejected.
    verdicts: dict[str, Verdict] = {
        a.id: 'approve' if answer['approvals'].get(a.id) == 'approve' else 'reject' for a in pending
    }
    for action_id, verdict in verdicts.items():
        _audit(state, 'approval', {'action_id': action_id, 'verdict': verdict},
               actor=answer['approver'], role=answer['role'])
    return {'approvals': verdicts, 'approver': answer['approver'], 'approver_role': answer['role']}


def execute(state: State) -> State:
    decisions = {d.action_id: d for d in state['decisions']}
    approvals = state.get('approvals', {})
    runnable = [a for a in state['actions']
                if decisions[a.id].tier == 'AUTO'
                or (decisions[a.id].tier == 'APPROVE' and approvals.get(a.id) == 'approve')]
    if _is_fake(state):
        return {'results': [_fake_result(state, a) for a in runnable]}
    provider = AwsProvider(state['aws_ids'], state['sims'])
    results = []
    for a in runnable:
        auto = decisions[a.id].tier == 'AUTO'
        results.append(executor.execute(
            a, provider, state['audit_path'],
            None if auto else state['approver'],
            decision=decisions[a.id],
            role='system' if auto else state['approver_role'],
        ))
    return {'results': results}


def report(state: State) -> State:
    policy = config.load_policy(state['client'])
    if _is_fake(state):
        totals = _fake_totals(state)
        text = reporter.TEMPLATE.format(**{
            **totals, 'client': policy.name,
            'blocked': ', '.join(b['action_id'] for b in totals['blocked']) or 'none',
            'needs_human': ', '.join(totals['needs_human']) or 'none',
        })
    else:
        totals = calc.totals(state['resources'], state['findings'], state['actions'],
                             state['decisions'], state['results'])
        text = reporter.summarise(policy, totals, state['results'])
    _audit(state, 'report', {'totals': totals, 'report': text})
    return {'totals': totals, 'report': text}


# --- graph -------------------------------------------------------------------

NODES = [
    ('ingest', ingest), ('detect', detect), ('analyse', analyse), ('price', price),
    ('guard', guard_node), ('approval', approval), ('execute', execute), ('report', report),
]

# Checkpoints hold our pydantic models; allow exactly these types to be deserialised.
_SERDE = JsonPlusSerializer(allowed_msgpack_modules=[
    ('carbonops.models', name) for name in ('Resource', 'Metrics', 'Finding', 'Action', 'Decision', 'ExecResult')
])


def build_graph(checkpointer: InMemorySaver | None = None) -> CompiledStateGraph:
    g = StateGraph(State)
    for name, fn in NODES:
        g.add_node(name, fn)
    names = [name for name, _ in NODES]
    g.add_edge(START, names[0])
    for a, b in zip(names, names[1:]):
        g.add_edge(a, b)
    g.add_edge(names[-1], END)
    return g.compile(checkpointer=checkpointer or InMemorySaver(serde=_SERDE))


_graph: CompiledStateGraph | None = None


def _app() -> CompiledStateGraph:
    """One graph per process, so paused runs survive Streamlit reruns."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': thread_id}}


def _outcome(thread_id: str) -> Outcome:
    snap = _app().get_state(_config(thread_id))
    pending = [p for i in snap.interrupts for p in i.value['pending']]
    return {'thread_id': thread_id, 'state': snap.values, 'pending': pending}


def run(client: str, role: Role, user: str, fake: bool = False) -> Outcome:
    """Start a run. It finishes, or pauses at `approval` with `pending` filled."""
    thread_id = uuid.uuid4().hex
    _app().invoke({'client': client, 'role': role, 'user': user, 'fake': fake}, _config(thread_id))
    return _outcome(thread_id)


def resume(thread_id: str, approvals: dict[str, Verdict], approver: str, role: Role = 'Approver') -> Outcome:
    """Resume a paused run with a verdict per pending action id. Unlisted actions are rejected."""
    _app().invoke(Command(resume={'approvals': approvals, 'approver': approver, 'role': role}),
                  _config(thread_id))
    return _outcome(thread_id)


# --- fake data (used until the stubs are implemented) --------------------------
# Numbers match what calc.py should produce for these resources; tiers follow guard.py's rules.

_FAKE_PLAN: dict[str, dict[str, Any]] = {
    'dev-api-01':    dict(kind='ALWAYS_ON', evidence=('hours_per_week',), type='SCHEDULE',
                          params={'hours_per_week': 50}, confidence=0.9, inr=9787.7, kg=3.71,
                          tiers=('AUTO', 'APPROVE')),
    'dev-ci-runner': dict(kind='IDLE', evidence=('cpu_avg', 'cpu_max'), type='STOP',
                          params={}, confidence=0.9, inr=27870.1, kg=10.44, tiers=('AUTO', 'AUTO')),
    'test-db-02':    dict(kind='ALWAYS_ON', evidence=('hours_per_week',), type='SCHEDULE',
                          params={'hours_per_week': 50}, confidence=0.9, inr=4341.4, kg=1.88,
                          tiers=('AUTO', 'APPROVE')),
    'staging-web':   dict(kind='OVERSIZED', evidence=('cpu_p95',), type='RESIZE',
                          params={'to': 'm5.xlarge'}, confidence=0.9, inr=13935.0, kg=5.10,
                          tiers=('APPROVE', 'APPROVE')),
    'staging-api':   dict(kind='OVERSIZED', evidence=('cpu_p95',), type='RESIZE',
                          params={'to': 'm5.large'}, confidence=0.9, inr=6967.5, kg=2.55,
                          tiers=('APPROVE', 'APPROVE')),
    'reporting-vm':  dict(kind='IDLE', evidence=('cpu_avg', 'cpu_max'), type='SCHEDULE',
                          params={'hours_per_week': 50}, confidence=0.5, inr=8682.8, kg=3.67,
                          tiers=('APPROVE', 'APPROVE'), why='confidence 0.50 is below 0.70'),
    'sandbox-ml':    dict(kind='IDLE', evidence=('cpu_avg', 'cpu_max'), type='STOP',
                          params={}, confidence=0.9, inr=27870.1, kg=10.32,
                          tiers=('APPROVE', 'APPROVE'), why='instructions found in tags'),
    'prod-web-01':   dict(kind='OVERSIZED', evidence=('cpu_p95',), type='RESIZE',
                          params={'to': 'm5.large'}, confidence=0.9, inr=6967.5, kg=2.55,
                          tiers=('BLOCK', 'BLOCK'), why='prod is a protected environment'),
    'vol-orphan-01': dict(kind='UNATTACHED', evidence=(), type='SNAPSHOT',
                          params={}, confidence=0.9, inr=0.0, kg=0.0, tiers=('APPROVE', 'APPROVE')),
}
_FAKE_WHY: dict[Tier, str] = {'AUTO': 'within auto policy', 'APPROVE': 'needs a human approver',
                              'BLOCK': 'not allowed by policy'}


def _base_id(resource_id: str) -> str:
    return resource_id.removeprefix('nb-')


def _fake_findings(state: State) -> list[Finding]:
    findings = []
    for r in state['resources']:
        plan = _FAKE_PLAN.get(_base_id(r.id))
        if plan is None:
            continue
        m = state['metrics'].get(r.id)
        evidence = ({k: getattr(m, k) for k in plan['evidence']} if m
                    else {'attached': r.attached, 'size_gb': r.size_gb})
        findings.append(Finding(resource_id=r.id, kind=plan['kind'], evidence=evidence))
    return findings


def _fake_actions(state: State) -> list[Action]:
    actions = []
    for f in state['findings']:
        plan = _FAKE_PLAN[_base_id(f.resource_id)]
        actions.append(Action(
            id=f"{f.resource_id}:{plan['type']}", resource_id=f.resource_id, type=plan['type'],
            params=plan['params'], confidence=plan['confidence'],
            reason=f"[fake] {f.kind.lower().replace('_', ' ')}: {f.evidence}",
        ))
    return actions


def _fake_price(action: Action) -> Action:
    plan = _FAKE_PLAN[_base_id(action.resource_id)]
    return action.model_copy(update={'inr_month': plan['inr'], 'kgco2e_month': plan['kg']})


def _fake_decision(state: State, action: Action) -> Decision:
    plan = _FAKE_PLAN[_base_id(action.resource_id)]
    tier: Tier = plan['tiers'][0 if state['client'] == 'client_a' else 1]
    return Decision(action_id=action.id, tier=tier, reasons=[f"[fake] {plan.get('why', _FAKE_WHY[tier])}"])


def _fake_tag_decisions(state: State) -> list[tuple[Action, Decision]]:
    pairs = []
    for r in state['resources']:
        if _base_id(r.id) == 'sandbox-ml':
            a = Action(id=f'{r.id}:TERMINATE:tag', resource_id=r.id, type='TERMINATE', confidence=0,
                       proposed_by='tag', reason=f"tag 'note' asks: {r.tags.get('note', '')}")
            pairs.append((a, Decision(action_id=a.id, tier='BLOCK',
                                      reasons=['[fake] TERMINATE is never allowed', 'instructions found in tags'])))
    return pairs


def _fake_result(state: State, action: Action) -> ExecResult:
    if state['sims'].get(action.resource_id, {}).get('health_fail_after') == action.type:
        return ExecResult(action_id=action.id, status='ROLLED_BACK',
                          detail=f'[fake] health check failed after {action.type}; restored')
    return ExecResult(action_id=action.id, status='DONE', detail='[fake] applied')


def _fake_totals(state: State) -> dict[str, Any]:
    actions = {a.id: a for a in state['actions']}
    tiers = [d.tier for d in state['decisions']]
    done = [actions[r.action_id] for r in state['results'] if r.status == 'DONE']
    inr = sum(a.inr_month for a in done)
    kg = sum(a.kgco2e_month for a in done)
    with_finding = {f.resource_id for f in state['findings']}
    return {
        'auto': tiers.count('AUTO'), 'approve': tiers.count('APPROVE'), 'block': tiers.count('BLOCK'),
        'no_finding': sum(r.id not in with_finding for r in state['resources']),
        'done': len(done),
        'rolled_back': sum(r.status == 'ROLLED_BACK' for r in state['results']),
        'failed': sum(r.status == 'FAILED' for r in state['results']),
        'inr_month': round(inr, 2), 'inr_year': round(inr * 12, 2),
        'kgco2e_month': round(kg, 2), 'kgco2e_year': round(kg * 12, 2),
        'blocked': [{'action_id': d.action_id, 'reasons': d.reasons} for d in state['decisions'] if d.tier == 'BLOCK'],
        'needs_human': [d.action_id for d in state['decisions'] if d.tier == 'APPROVE'],
    }
