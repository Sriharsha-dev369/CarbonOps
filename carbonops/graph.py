"""LangGraph pipeline in four stages (the organizers' workflow words):

  01 Ingest  ingest
  02 Decide  detect → analyse → price → guard
  03 Act     execute_auto → approval (pauses for a human) → execute
  04 Report  report

AUTO actions run before the pause, so "auto-executed" and "awaiting approval" are both true while
a human decides. Every node adds a trace entry; a failing node is logged to the audit trail and turns
into a friendly `error` message, and the remaining nodes are skipped.
"""

import logging
import operator
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from carbonops import analyst, audit, calc, config, detector, executor, guard, redteam, reporter, seed
from carbonops.fmt import inr
from carbonops.models import (
    Action, AuditEvent, Decision, ExecResult, Finding, Metrics, Resource, Role,
)
from carbonops.moto_runtime import is_sandbox
from carbonops.providers.aws import AwsProvider

log = logging.getLogger(__name__)

Verdict = Literal['approve', 'reject']
Stage = Literal['ingest', 'decide', 'act', 'report']
APPROVER_ROLES: tuple[Role, ...] = executor.APPROVER_ROLES
STAGES: list[tuple[Stage, str]] = [('ingest', '01 Ingest'), ('decide', '02 Decide'),
                                    ('act', '03 Act'), ('report', '04 Report')]


class State(TypedDict, total=False):
    client: str
    role: Role
    user: str
    use_llm: bool
    audit_path: str
    resources: list[Resource]
    metrics: dict[str, Metrics]
    findings: list[Finding]
    actions: list[Action]
    decisions: list[Decision]
    approvals: dict[str, Verdict]        # action id -> verdict, for every APPROVE action
    comments: dict[str, str]             # optional approver comments by action id
    approver: str
    approver_role: Role
    results: Annotated[list[ExecResult], operator.add]   # execute_auto and execute both append
    totals: dict[str, Any]
    report: str
    trace: Annotated[list[dict], operator.add]           # {stage, node, summary, ms}
    error: str                           # friendly message when a node failed


class Outcome(TypedDict):
    thread_id: str
    state: State
    pending: list[dict]                  # actions awaiting approval; empty once the run has finished


def _audit(state: State, kind: str, payload: dict, actor: str = 'system',
           role: Role | Literal['system'] = 'system') -> None:
    audit.append(AuditEvent(ts=datetime.now(timezone.utc).isoformat(timespec='seconds'), actor=actor,
                            role=role, kind=kind, payload=payload), state['audit_path'])


def _by_id(state: State) -> dict[str, Resource]:
    return {r.id: r for r in state['resources']}


# --- nodes: each returns (state update, one-line trace summary) ------------------

def ingest(state: State) -> tuple[State, str]:
    client = state['client']
    provider = AwsProvider(client)
    resources = provider.list_resources(client)
    note = ''
    if not resources and is_sandbox():
        seed.seed(client)
        resources = provider.list_resources(client)
        note = ' (estate seeded first)'
    order = {r.id: i for i, r in enumerate(config.load_estate(client)[0])}
    resources.sort(key=lambda r: (order.get(r.id, len(order)), r.id))
    metrics = {r.id: m for r in resources if r.kind == 'vm' and (m := provider.get_metrics(r.id)) is not None}
    _audit(state, 'run', {'client': client, 'resources': len(resources), 'use_llm': bool(state.get('use_llm'))},
           actor=state['user'], role=state['role'])
    source = 'AWS (emulated by moto)' if is_sandbox() else 'AWS'
    return ({'resources': resources, 'metrics': metrics},
            f'Read {len(resources)} resources and {len(metrics)} hourly CPU series (7 days) from {source}{note}.')


def detect(state: State) -> tuple[State, str]:
    findings = detector.detect(state['resources'], state['metrics'], config.load_policy(state['client']))
    for f in findings:
        _audit(state, 'finding', f.model_dump())
    kinds = Counter(f.kind for f in findings)
    quiet = len(state['resources']) - len(findings)
    return ({'findings': findings},
            f"{len(findings)} findings ({', '.join(f'{n} {k}' for k, n in kinds.items())}); {quiet} need no action.")


def analyse(state: State) -> tuple[State, str]:
    policy, resources = config.load_policy(state['client']), _by_id(state)
    if not state.get('use_llm'):
        prices = config.load_prices()
        actions = [detector.default_action(f, resources[f.resource_id], prices, policy) for f in state['findings']]
        return {'actions': actions}, f'Rules proposed {len(actions)} actions (LLM off).'
    notes: list[str] = []
    actions = analyst.propose(state['findings'], resources, policy, notes=notes)
    _audit(state, 'analysis', {'notes': notes, 'proposed_by': dict(Counter(a.proposed_by for a in actions))})
    return {'actions': actions}, ' '.join(notes)


def price(state: State) -> tuple[State, str]:
    resources, prices, carbon = _by_id(state), config.load_prices(), config.load_carbon()
    priced = [calc.price_action(a, resources[a.resource_id], state['metrics'].get(a.resource_id), prices, carbon)
              for a in state['actions']]
    at_stake = sum(a.inr_month for a in priced)
    kg = sum(a.kgco2e_month for a in priced)
    return {'actions': priced}, f'{inr(at_stake)}/month and {kg:,.1f} kg CO₂e/month at stake.'


def guard_node(state: State) -> tuple[State, str]:
    policy, resources = config.load_policy(state['client']), _by_id(state)
    actions = list(state['actions'])
    decisions = []
    for a in actions:
        r = resources[a.resource_id]
        decisions.append(guard.evaluate(a, r, policy, guard.is_suspicious(r)))
    injected = [pair for r in state['resources'] for pair in guard.tag_decisions(r, policy)]
    for a, d in injected:
        actions.append(a)
        decisions.append(d)
    for d in decisions:
        _audit(state, 'decision', d.model_dump())
    tiers = Counter(d.tier for d in decisions)
    note = f' ({len(injected)} request(s) found in tags, blocked)' if injected else ''
    return ({'actions': actions, 'decisions': decisions},
            f"{tiers['AUTO']} AUTO · {tiers['APPROVE']} APPROVE · {tiers['BLOCK']} BLOCK{note}.")


def _execute(state: State, actions: list[Action], approver: str | None, role: Role | Literal['system']) -> list[ExecResult]:
    provider = AwsProvider(state['client'])
    decisions = {d.action_id: d for d in state['decisions']}
    return [executor.execute(a, provider, state['audit_path'], approver, decision=decisions[a.id], role=role)
            for a in actions]


def _result_summary(results: list[ExecResult], what: str) -> str:
    if not results:
        return f'No {what}.'
    counts = Counter(r.status for r in results)
    return f"{len(results)} {what}: " + ', '.join(f'{n} {s.replace("_", " ").lower()}' for s, n in counts.items()) + '.'


def execute_auto(state: State) -> tuple[State, str]:
    tiers = {d.action_id: d.tier for d in state['decisions']}
    results = _execute(state, [a for a in state['actions'] if tiers[a.id] == 'AUTO'], None, 'system')
    return {'results': results}, _result_summary(results, 'auto actions')


def approval(state: State) -> tuple[State, str]:
    decisions = {d.action_id: d for d in state['decisions']}
    pending = [a for a in state['actions'] if decisions[a.id].tier == 'APPROVE']
    if not pending:
        return {'approvals': {}}, 'Nothing needed a human.'
    answer = interrupt({'pending': [{**a.model_dump(), 'reasons': decisions[a.id].reasons} for a in pending]})
    # Anything not explicitly approved is rejected.
    verdicts: dict[str, Verdict] = {
        a.id: 'approve' if answer['approvals'].get(a.id) == 'approve' else 'reject' for a in pending
    }
    comments = {k: v for k, v in (answer.get('comments') or {}).items() if v}
    for action_id, verdict in verdicts.items():
        _audit(state, 'approval', {'action_id': action_id, 'verdict': verdict, 'comment': comments.get(action_id, '')},
               actor=answer['approver'], role=answer['role'])
    n_ok = sum(v == 'approve' for v in verdicts.values())
    return ({'approvals': verdicts, 'comments': comments, 'approver': answer['approver'],
             'approver_role': answer['role']},
            f"{answer['approver']} ({answer['role']}) approved {n_ok}, rejected {len(verdicts) - n_ok}.")


def execute(state: State) -> tuple[State, str]:
    approved = [a for a in state['actions'] if state.get('approvals', {}).get(a.id) == 'approve']
    results = _execute(state, approved, state.get('approver'), state.get('approver_role', 'system'))
    return {'results': results}, _result_summary(results, 'approved actions')


def report(state: State) -> tuple[State, str]:
    totals = calc.totals(state['resources'], state['findings'], state['actions'], state['decisions'],
                         state['results'], state.get('approvals'))
    notes: list[str] = []
    if state.get('use_llm'):
        text = reporter.summarize(state['client'], state['results'], totals, notes=notes)
    else:
        text = reporter.template_report(state['client'], state['results'], totals)
        notes.append('Report: template (LLM off).')
    _audit(state, 'report', {'totals': totals, 'notes': notes})
    return ({'totals': totals, 'report': text},
            f"{inr(totals['inr_month'])}/month realised, {totals['kgco2e_year']:,.1f} kg CO₂e/year. {' '.join(notes)}")


# --- graph -------------------------------------------------------------------

NODES: list[tuple[str, Stage, Callable[[State], tuple[State, str]]]] = [
    ('ingest', 'ingest', ingest),
    ('detect', 'decide', detect), ('analyse', 'decide', analyse),
    ('price', 'decide', price), ('guard', 'decide', guard_node),
    ('execute_auto', 'act', execute_auto), ('approval', 'act', approval), ('execute', 'act', execute),
    ('report', 'report', report),
]
NODE_STAGE: dict[str, Stage] = {name: stage for name, stage, _ in NODES}


def _step(name: str, stage: Stage, fn: Callable[[State], tuple[State, str]]) -> Callable[[State], State]:
    """Wrap a node: skip after an earlier error, time it, add its trace entry, and catch failures."""

    def run_node(state: State) -> State:
        if state.get('error'):
            return {}
        start = time.perf_counter()
        try:
            update, summary = fn(state)
        except GraphBubbleUp:      # interrupt() for the approval pause: not an error
            raise
        except Exception as e:
            log.error('node %s failed', name, exc_info=True)
            problem = f'{type(e).__name__}: {e}'
            try:
                _audit(state, 'error', {'node': name, 'error': problem})
            except Exception:
                log.error('could not write the error to the audit log', exc_info=True)
            return {'error': f'The "{name}" step failed ({problem}). The rest of the run was skipped; '
                             f'the error is recorded in the audit log.',
                    'trace': [_entry(stage, name, f'Error: {problem}', start)]}
        return {**update, 'trace': [_entry(stage, name, summary, start)]}

    return run_node


def _entry(stage: Stage, node: str, summary: str, start: float) -> dict:
    return {'stage': stage, 'node': node, 'summary': summary, 'ms': round((time.perf_counter() - start) * 1000)}


# Checkpoints hold our pydantic models; allow exactly these types to be deserialised.
_SERDE = JsonPlusSerializer(allowed_msgpack_modules=[
    ('carbonops.models', name) for name in ('Resource', 'Metrics', 'Finding', 'Action', 'Decision', 'ExecResult')
])


def build_graph(checkpointer: InMemorySaver | None = None) -> CompiledStateGraph:
    g = StateGraph(State)
    for name, stage, fn in NODES:
        g.add_node(name, _step(name, stage, fn))
    names = [name for name, _, _ in NODES]
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


def _drive(graph_input: Any, thread_id: str, on_step: Callable[[str], None] | None) -> Outcome:
    for chunk in _app().stream(graph_input, _config(thread_id), stream_mode='updates'):
        if on_step:
            for node in chunk:
                if node in NODE_STAGE:
                    on_step(node)
    snap = _app().get_state(_config(thread_id))
    pending = [p for i in snap.interrupts for p in i.value['pending']]
    return {'thread_id': thread_id, 'state': snap.values, 'pending': pending}


def run(client: str, role: Role, user: str, *, use_llm: bool = False, audit_path: str | None = None,
        on_step: Callable[[str], None] | None = None) -> Outcome:
    """Start a run. It finishes, or pauses at `approval` with `pending` filled. `on_step(node)` fires per node."""
    thread_id = uuid.uuid4().hex
    initial: State = {'client': client, 'role': role, 'user': user, 'use_llm': use_llm,
                      'audit_path': str(audit_path or config.audit_path(client)), 'results': [], 'trace': []}
    return _drive(initial, thread_id, on_step)


def resume(thread_id: str, approvals: dict[str, Verdict], approver: str, role: Role = 'Approver', *,
           comments: dict[str, str] | None = None, on_step: Callable[[str], None] | None = None) -> Outcome:
    """Resume a paused run with a verdict per pending action id. Unlisted actions are rejected."""
    answer = {'approvals': approvals, 'approver': approver, 'role': role, 'comments': comments or {}}
    return _drive(Command(resume=answer), thread_id, on_step)


def reset_demo(client: str) -> None:
    """Rebuild the client's emulated estate (planted red-team tags included) and clear its audit log."""
    seed.reset(client)
    redteam.forget(client)
    config.audit_path(client).unlink(missing_ok=True)
