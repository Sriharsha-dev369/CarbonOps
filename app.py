"""CarbonOps Streamlit shell: `uv run streamlit run app.py`."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import dotenv_values, load_dotenv
from pydantic import ValidationError

from carbonops import audit, config, graph
from carbonops.models import Action, AuditEvent, Decision, ExecResult, Finding, Resource, Role

load_dotenv(config.ROOT / '.env')

RUN_FAKE = True   # the business-logic modules are stubs until the hackathon; flip to False then
ROLES: list[Role] = ['Viewer', 'Engineer', 'Approver', 'Admin']
CLIENTS = {config.load_policy(c).name: c for c in ('client_a', 'client_b')}
TIER_COLOR = {'AUTO': 'green', 'APPROVE': 'orange', 'BLOCK': 'red'}
TIER_LABEL = {'AUTO': 'Auto', 'APPROVE': 'Needs approval', 'BLOCK': 'Blocked'}
TIER_ORDER = {'BLOCK': 0, 'APPROVE': 1, 'AUTO': 2}


def render_action_card(action: Action, decision: Decision, resource: Resource, finding: Finding | None,
                       result: ExecResult | None = None) -> None:
    with st.container(border=True):
        title, badge = st.columns([5, 1])
        size = resource.instance_type or f'{resource.size_gb} GB volume'
        title.markdown(f'**{resource.id}** · `{resource.env}` · {size}')
        with badge:
            st.badge(TIER_LABEL[decision.tier], color=TIER_COLOR[decision.tier])

        params = f" `{json.dumps(action.params)}`" if action.params else ''
        st.markdown(f'**{action.type}**{params} · {action.reason or "no reason given"}')
        inr = '₹ — (volume price unconfirmed)' if resource.kind == 'volume' else f'₹{action.inr_month:,.0f}/month'
        st.markdown(f'{inr} · {action.kgco2e_month:,.2f} kg CO₂e/month · confidence {action.confidence:.0%}')

        if finding is not None:
            st.caption(f'Finding: {finding.kind} · evidence {json.dumps(finding.evidence)}')
        elif action.proposed_by == 'tag':
            st.caption('Source: instructions found in resource tags (untrusted)')
        st.caption('Why this tier: ' + '; '.join(decision.reasons))
        if result is not None:
            st.caption(f'Result: **{result.status}** {result.detail}')


def _replay_default() -> str:
    return dotenv_values(config.ROOT / '.env').get('REPLAY_MODE') or 'off'


def _read_audit(path: Path) -> list[AuditEvent]:
    if not path.exists():
        return []
    return [AuditEvent.model_validate_json(line)
            for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _sample_audit(state: graph.State) -> list[AuditEvent]:
    """Fake runs write no audit log; show what one would look like."""
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    events = [AuditEvent(ts=now, actor='system', role='system', kind='decision', payload=d.model_dump())
              for d in state.get('decisions', [])]
    for action_id, verdict in state.get('approvals', {}).items():
        events.append(AuditEvent(ts=now, actor=state.get('approver', ''), role=state.get('approver_role', 'Approver'),
                                 kind='approval', payload={'action_id': action_id, 'verdict': verdict}))
    events += [AuditEvent(ts=now, actor='system', role='system',
                          kind='rollback' if r.status == 'ROLLED_BACK' else 'execution', payload=r.model_dump())
               for r in state.get('results', [])]
    return events


# --- sidebar -------------------------------------------------------------------

st.set_page_config(page_title='CarbonOps', page_icon='🌱', layout='wide')

with st.sidebar:
    st.title('CarbonOps')
    user = st.text_input('Your name', value='demo-user')
    role: Role = st.selectbox('Role', ROLES, index=1)
    client_name = st.selectbox('Client', list(CLIENTS))
    client = CLIENTS[client_name]
    demo = st.toggle('Demo mode', help='Replay recorded LLM answers (REPLAY_MODE=play): no API calls.')
    os.environ['REPLAY_MODE'] = 'play' if demo else _replay_default()

    if st.button('Run', type='primary', disabled=role == 'Viewer'):
        try:
            st.session_state.outcome = graph.run(client, role, user, fake=RUN_FAKE)
            st.session_state.choices = {}
        except Exception as e:  # show any failure in the page instead of a blank app
            st.session_state.outcome = None
            st.exception(e)
    if role == 'Viewer':
        st.caption('Viewers are read-only.')
    if RUN_FAKE:
        st.caption('Using fake data until the business logic is implemented.')

outcome: graph.Outcome | None = st.session_state.get('outcome')
if outcome is not None and outcome['state'].get('client') != client:
    outcome = None   # last run was for the other client
state: graph.State = outcome['state'] if outcome else {}
pending: list[dict] = outcome['pending'] if outcome else []

resources = {r.id: r for r in state.get('resources', [])}
findings = {f.resource_id: f for f in state.get('findings', [])}
actions = {a.id: a for a in state.get('actions', [])}
decisions = {d.action_id: d for d in state.get('decisions', [])}
results = {r.action_id: r for r in state.get('results', [])}

# --- main ----------------------------------------------------------------------

st.header(client_name)
run_tab, approvals_tab, savings_tab, audit_tab, policy_tab = st.tabs(['Run', 'Approvals', 'Savings', 'Audit', 'Policy'])

with run_tab:
    if outcome is None:
        st.info('Press **Run** in the sidebar to scan this estate.')
    else:
        tiers = [d.tier for d in decisions.values()]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Auto', tiers.count('AUTO'))
        c2.metric('Needs approval', tiers.count('APPROVE'))
        c3.metric('Blocked', tiers.count('BLOCK'))
        c4.metric('No finding', len(resources) - len(findings))
        if pending:
            st.warning(f'{len(pending)} action(s) are waiting in **Approvals**.')
        elif state.get('report'):
            st.success(state['report'])
        for a in sorted(actions.values(), key=lambda a: TIER_ORDER[decisions[a.id].tier]):
            render_action_card(a, decisions[a.id], resources[a.resource_id], findings.get(a.resource_id),
                               results.get(a.id))

with approvals_tab:
    can_approve = role in graph.APPROVER_ROLES
    if outcome is None:
        st.info('Nothing to approve yet.')
    elif not pending:
        st.success('No pending approvals.')
        if state.get('approvals'):
            st.dataframe([{'action': k, 'verdict': v, 'by': state.get('approver', '')}
                          for k, v in state['approvals'].items()], hide_index=True)
    else:
        if not can_approve:
            st.info('Only an Approver or Admin can approve or reject.')
        choices: dict[str, str] = st.session_state.setdefault('choices', {})
        for p in pending:
            a = actions[p['id']]
            render_action_card(a, decisions[a.id], resources[a.resource_id], findings.get(a.resource_id))
            b1, b2, chosen = st.columns([1, 1, 4])
            if b1.button('Approve', key=f'approve-{a.id}', disabled=not can_approve):
                choices[a.id] = 'approve'
            if b2.button('Reject', key=f'reject-{a.id}', disabled=not can_approve):
                choices[a.id] = 'reject'
            chosen.markdown(f"Choice: **{choices.get(a.id, '—')}**")
        all_decided = all(p['id'] in choices for p in pending)
        if st.button('Submit decisions and resume', type='primary', disabled=not (can_approve and all_decided)):
            st.session_state.outcome = graph.resume(outcome['thread_id'], choices, approver=user, role=role)
            st.session_state.choices = {}
            st.rerun()

with savings_tab:
    totals = state.get('totals')
    if not totals:
        st.info('Savings appear when the run finishes (resolve any approvals first).')
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric('₹ / month', f"₹{totals['inr_month']:,.0f}")
        c2.metric('₹ / year', f"₹{totals['inr_year']:,.0f}")
        c3.metric('kg CO₂e / year', f"{totals['kgco2e_year']:,.1f}")
        st.caption('Counted from actions that completed (DONE). Volumes show no ₹: the gp3 price is unconfirmed.')
    if actions:
        st.dataframe([{
            'action': a.id, 'resource': a.resource_id, 'type': a.type, 'tier': decisions[a.id].tier,
            '₹/month': None if resources[a.resource_id].kind == 'volume' else round(a.inr_month),
            'kg CO₂e/month': round(a.kgco2e_month, 2),
            'status': results[a.id].status if a.id in results else ('PENDING' if a.id in {p['id'] for p in pending} else '—'),
        } for a in actions.values()], hide_index=True)

with audit_tab:
    path = Path(state['audit_path']) if state.get('audit_path') else config.ROOT / 'audit' / f'{client}.jsonl'
    try:
        events = _read_audit(path)
    except ValidationError as e:
        events = []
        st.error(f'{path.name} has a line that is not a valid audit event: {e}')
    if not events and outcome is not None and RUN_FAKE:
        st.info('Fake runs write no audit log. Sample events:')
        events = _sample_audit(state)
    if events:
        st.dataframe([{'ts': e.ts, 'actor': e.actor, 'role': e.role, 'kind': e.kind,
                       'payload': json.dumps(e.payload), 'hash': e.hash[:12]} for e in events], hide_index=True)
    else:
        st.info('No audit events yet.')
    if st.button('Verify chain'):
        try:
            ok, broken = audit.verify(path)
            if ok:
                st.success(f'Audit chain intact ({path.name}).')
            else:
                st.error(f'Audit chain broken at line {broken + 1}.')
        except NotImplementedError:
            st.warning('audit.verify is not implemented yet.')
        except FileNotFoundError:
            st.warning(f'No audit log at {path}.')

with policy_tab:
    if role != 'Admin':
        st.info('Only Admin can view the policy.')
    else:
        st.code((config.ROOT / 'policies' / f'{client}.yaml').read_text(encoding='utf-8'), language='yaml')
