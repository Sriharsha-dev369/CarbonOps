"""CarbonOps: governed GreenOps for IT infrastructure. Run with `./run.sh` or `uv run streamlit run app.py`."""

import html
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import dotenv_values, load_dotenv

from carbonops import audit, calc, config, graph, onboard
from carbonops.fmt import action_label, evidence, inr
from carbonops.models import Action, AuditEvent, Decision, ExecResult, Finding, Resource, Role
from carbonops.moto_runtime import ensure_moto, is_sandbox

load_dotenv(config.ROOT / '.env')
log = logging.getLogger('carbonops.app')

ROLES: list[Role] = ['Viewer', 'Engineer', 'Approver', 'Admin']
APPROVERS = ('Approver', 'Admin')
RESETTERS = ('Engineer', 'Admin')
TIER_LABEL = {'AUTO': 'Auto', 'APPROVE': 'Needs approval', 'BLOCK': 'Blocked'}
TIER_ICON = {'AUTO': '🟢', 'APPROVE': '🟠', 'BLOCK': '🔴'}
STATUS_LABEL = {'DONE': 'Done', 'ROLLED_BACK': 'Rolled back', 'SKIPPED': 'Skipped', 'FAILED': 'Failed'}
STAGE_HINT = {'ingest': 'Read estate & metrics', 'decide': 'Detect · analyse · price · guard',
              'act': 'Execute · approve · verify', 'report': 'Summarise'}
# Savings chart: one validated palette (dataviz validator, light surface).
CHART_STATES = ['Realised', 'Pending approval', 'Not executed']
CHART_COLORS = ['#1B7F4E', '#E3A008', '#7F7E78']

CSS = """
<style>
.co-header{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px;
  border-bottom:1px solid #E3E9E5;padding-bottom:12px;margin-bottom:8px}
.co-brand{font-size:1.9rem;font-weight:700;color:#1F6F4A;letter-spacing:-0.02em;line-height:1.1}
.co-tagline{color:#55605A;font-size:0.95rem;margin-top:2px}
.co-who{color:#33413A;font-size:0.9rem;text-align:right}
.co-badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:0.75rem;font-weight:600;
  border:1px solid transparent;white-space:nowrap;vertical-align:middle}
.co-AUTO,.co-DONE{background:#E6F4EC;color:#145C3A;border-color:#B9DEC9}
.co-APPROVE{background:#FDF3DC;color:#875A00;border-color:#F0D58E}
.co-BLOCK,.co-FAILED{background:#FBE7E6;color:#A12A20;border-color:#F0BDB8}
.co-ROLLED_BACK{background:#EFE8FA;color:#5B3A99;border-color:#D3C3F0}
.co-SKIPPED,.co-env{background:#EFEFEC;color:#4A4A45;border-color:#DCDCD6}
.co-prod{background:#FBE7E6;color:#A12A20;border-color:#F0BDB8}
.co-role{background:#E6EEF9;color:#24508F;border-color:#C3D4EE}
.co-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:4px 0 14px}
.co-stage{border:1px solid #DCE3DF;border-radius:10px;padding:8px 12px;background:#FAFBFA;color:#8A948F}
.co-stage b{display:block;font-size:0.95rem}
.co-stage span{font-size:0.78rem}
.co-stage.active{border-color:#1F6F4A;color:#1F6F4A;background:#F1F8F4}
.co-stage.waiting{border-color:#E3A008;color:#875A00;background:#FFF9EA}
.co-stage.done{border-color:#1F6F4A;background:#1F6F4A;color:#FFFFFF}
.co-card{font-size:0.92rem;color:#17211C}
.co-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.co-row code{font-size:0.8rem}
.co-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px 18px;margin:6px 0 10px}
.co-k{font-size:0.72rem;text-transform:uppercase;letter-spacing:0.05em;color:#6B7570;margin-bottom:1px}
.co-v{font-weight:500}
.co-card ul{margin:2px 0 6px 18px;padding:0}
.co-banner{background:#FBE7E6;border:1px solid #E7A39C;color:#8F2219;border-radius:8px;padding:8px 12px;
  margin:4px 0 10px;font-weight:600}
.co-result{border-radius:8px;padding:6px 10px;margin-top:6px}
.co-section{display:flex;align-items:center;gap:8px;margin:14px 0 4px;font-weight:600;font-size:1.02rem}
</style>
"""


def esc(value: object) -> str:
    return html.escape(str(value))


def badge(kind: str, text: str) -> str:
    return f'<span class="co-badge co-{kind}">{esc(text)}</span>'


@st.cache_resource(show_spinner=False)
def start_aws() -> str | None:
    """Start the AWS emulator once per app process (no-op in real-AWS mode)."""
    return ensure_moto()


def replay_default() -> str:
    return dotenv_values(config.ROOT / '.env').get('REPLAY_MODE') or 'off'


def client_names() -> dict[str, str]:
    return {c: config.load_policy(c).name for c in config.list_clients()}


# --- view model ----------------------------------------------------------------

class View:
    """Everything the tabs need from the last run of the selected client."""

    def __init__(self, outcome: graph.Outcome | None):
        self.outcome = outcome
        s: graph.State = outcome['state'] if outcome else {}
        self.state = s
        self.pending: list[dict] = outcome['pending'] if outcome else []
        self.pending_ids = {p['id'] for p in self.pending}
        self.resources: dict[str, Resource] = {r.id: r for r in s.get('resources', [])}
        self.findings: dict[str, Finding] = {f.resource_id: f for f in s.get('findings', [])}
        self.actions: dict[str, Action] = {a.id: a for a in s.get('actions', [])}
        self.decisions: dict[str, Decision] = {d.action_id: d for d in s.get('decisions', [])}
        self.results: dict[str, ExecResult] = {r.action_id: r for r in s.get('results', [])}
        self.approvals: dict[str, str] = s.get('approvals', {})
        # resource id -> action types its tags asked for (all blocked)
        self.injected: dict[str, list[str]] = {}
        for d in s.get('decisions', []):
            a = self.actions[d.action_id]
            if d.tier == 'BLOCK' and a.proposed_by == 'tag':
                self.injected.setdefault(a.resource_id, []).append(a.type)
        complete = bool(s.get('decisions')) and len(self.decisions) == len(self.actions)
        self.totals = calc.totals(s['resources'], s['findings'], s['actions'], s['decisions'],
                                  s.get('results', []), self.approvals) if complete else None

    def state_of(self, a: Action) -> str:
        if a.id in self.results and self.results[a.id].status == 'DONE':
            return 'Realised'
        if a.id in self.pending_ids:
            return 'Pending approval'
        return 'Not executed'


# --- components ------------------------------------------------------------------

def strip_html(view: View, seen: set[str] | None = None, running: bool = False) -> str:
    done = seen if seen is not None else {t['node'] for t in view.state.get('trace', [])}
    cells = []
    for stage, label in graph.STAGES:
        nodes = [n for n, s in graph.NODE_STAGE.items() if s == stage]
        if all(n in done for n in nodes):
            css, hint = 'done', STAGE_HINT[stage]
        elif stage == 'act' and view.pending and not running:
            css, hint = 'waiting', 'Waiting for approval'
        elif any(n in done for n in nodes) or (running and stage == graph.NODE_STAGE[_next_node(done)]):
            css, hint = 'active', STAGE_HINT[stage]
        else:
            css, hint = '', STAGE_HINT[stage]
        cells.append(f'<div class="co-stage {css}"><b>{esc(label)}</b><span>{esc(hint)}</span></div>')
    return f'<div class="co-strip">{"".join(cells)}</div>'


def _next_node(done: set[str]) -> str:
    return next((name for name, _, _ in graph.NODES if name not in done), graph.NODES[-1][0])


def render_action_card(action: Action, decision: Decision, resource: Resource, finding: Finding | None,
                       result: ExecResult | None = None, injected: list[str] | None = None) -> None:
    """The full card for one action. `injected`: action types the resource's tags asked for (all blocked)."""
    env_css = 'prod' if resource.env == 'prod' else 'env'
    size = resource.instance_type or f'{resource.size_gb} GB volume'
    head = [f'<b>{esc(resource.id)}</b>', badge(env_css, resource.env), f'<code>{esc(size)}</code>',
            badge(decision.tier, TIER_LABEL[decision.tier])]
    if result is not None:
        head.append(badge(result.status, STATUS_LABEL[result.status]))

    banner = ''
    if injected:
        banner = (f'<div class="co-banner">⚠ Instructions found in a resource tag were ignored. '
                  f'Requested {esc(" and ".join(injected))} was blocked.</div>')

    if action.proposed_by == 'tag':
        finding_text = 'Text in a resource tag asked for this action'
    elif finding is not None:
        finding_text = f'{finding.kind.replace("_", " ").title()} · {evidence(finding.kind, finding.evidence)}'
    else:
        finding_text = '—'
    money = ('₹ — (volume price unconfirmed)' if resource.kind == 'volume'
             else f'{inr(action.inr_month)}/month')
    reasons = ''.join(f'<li>{esc(r)}</li>' for r in decision.reasons)
    result_html = ''
    if result is not None:
        result_html = (f'<div class="co-k">Result</div><div class="co-result co-{result.status}">'
                       f'<b>{esc(STATUS_LABEL[result.status])}</b> · {esc(result.detail)}</div>')

    st.html(f"""<div class="co-card">
      <div class="co-row">{' '.join(head)}</div>{banner}
      <div class="co-grid">
        <div><div class="co-k">Finding</div><div class="co-v">{esc(finding_text)}</div></div>
        <div><div class="co-k">Proposed action</div><div class="co-v">{esc(action_label(action.type, action.params, resource.instance_type))}</div></div>
        <div><div class="co-k">Saving</div><div class="co-v">{esc(money)} · {action.kgco2e_month:,.2f} kg CO₂e/month</div></div>
        <div><div class="co-k">Confidence</div><div class="co-v">{action.confidence:.0%} · proposed by {esc(action.proposed_by.upper() if action.proposed_by == 'llm' else action.proposed_by)}</div></div>
      </div>
      <div class="co-k">Reason</div><div>{esc(action.reason or '—')}</div>
      <div class="co-k" style="margin-top:8px">Guard · {esc(TIER_LABEL[decision.tier])}</div><ul>{reasons}</ul>
      {result_html}
    </div>""")


def card_label(view: View, a: Action) -> str:
    r = view.resources[a.resource_id]
    money = '' if r.kind == 'volume' or not a.inr_month else f' · {inr(a.inr_month)}/month'
    status = f" · {STATUS_LABEL[view.results[a.id].status]}" if a.id in view.results else ''
    flag = ' · ⚠ tag instructions ignored' if a.resource_id in view.injected else ''
    return f'{TIER_ICON[view.decisions[a.id].tier]} **{a.resource_id}** · {action_label(a.type, a.params, r.instance_type)}{money}{status}{flag}'


def action_card(view: View, a: Action, expanded: bool = False) -> None:
    with st.expander(card_label(view, a), expanded=expanded):
        render_action_card(a, view.decisions[a.id], view.resources[a.resource_id], view.findings.get(a.resource_id),
                           view.results.get(a.id), view.injected.get(a.resource_id))


def event_summary(e: AuditEvent) -> str:
    p = e.payload
    target = p.get('action_id') or p.get('resource_id') or ''
    match e.kind:
        case 'run':
            name = client_names().get(p.get('client'), p.get('client'))
            return f"Run started for {name} ({p.get('resources')} resources, LLM {'on' if p.get('use_llm') else 'off'})"
        case 'finding':
            return f"{p.get('resource_id')}: {p.get('kind')} {json.dumps(p.get('evidence'))}"
        case 'decision':
            return f"{target} → {p.get('tier')}: {'; '.join(p.get('reasons', []))}"
        case 'approval':
            comment = f" — “{p['comment']}”" if p.get('comment') else ''
            return f"{target} {p.get('verdict')}d{comment}"
        case 'snapshot':
            return f"{target}: restore point {p.get('snapshot_id')}"
        case 'apply':
            return f"{target}: applied {json.dumps(p.get('params') or {})}"
        case 'health_check':
            return f"{target}: {p.get('latency_ms'):.0f} ms vs {p.get('threshold_ms'):.0f} ms threshold → {'ok' if p.get('ok') else 'FAIL'}"
        case 'rollback':
            return f"{target}: rolled back. {p.get('reason', '')}"
        case 'done':
            return f"{target}: {p.get('detail', 'done')}"
        case 'skipped':
            return f"{target}: skipped. {p.get('reason', '')}"
        case 'failed':
            return f"{target}: failed. {p.get('error', '')}"
        case 'report':
            t = p.get('totals', {})
            return f"Report: {inr(t.get('inr_month', 0))}/month realised, {t.get('kgco2e_year', 0):,.1f} kg CO₂e/year"
        case 'error':
            return f"{p.get('node')}: {p.get('error')}"
    return json.dumps(p)[:160]


# --- actions -------------------------------------------------------------------

def outcomes() -> dict[str, graph.Outcome]:
    return st.session_state.setdefault('outcomes', {})


def do_run(client: str, role: Role, user: str, use_llm: bool, strip_slot) -> None:
    seen: set[str] = set()
    empty = View(None)

    def on_step(node: str) -> None:
        seen.add(node)
        strip_slot.html(strip_html(empty, seen, running=True))
        time.sleep(0.12)   # let the audience see each stage light up

    strip_slot.html(strip_html(empty, seen, running=True))
    try:
        start_aws()
        outcomes()[client] = graph.run(client, role, user, use_llm=use_llm, on_step=on_step)
    except Exception as e:
        log.error('run failed', exc_info=True)
        st.session_state.flash = ('error', f'The run could not start: {e}')


def do_reset(client: str, name: str) -> None:
    try:
        start_aws()
        graph.reset_demo(client)
        outcomes().pop(client, None)
        st.session_state.flash = ('success', f'{name}: demo estate re-seeded and audit log cleared.')
    except Exception as e:
        log.error('reset failed', exc_info=True)
        st.session_state.flash = ('error', f'Reset failed: {e}')


def approve_all(keys: list[str]) -> None:
    for key in keys:
        st.session_state[key] = 'Approve'


# --- page ----------------------------------------------------------------------

st.set_page_config(page_title='CarbonOps', page_icon='🌱', layout='wide')
st.html(CSS)

try:
    start_aws()
except Exception as e:
    st.error(f'CarbonOps could not start its AWS emulator: {e}')
    st.stop()

names = client_names()
with st.sidebar:
    st.markdown('### CarbonOps')
    user = st.text_input('Your name', value='Priya').strip() or 'anonymous'
    role: Role = st.selectbox('Role', ROLES, index=1)
    if 'next_client' in st.session_state:          # set by onboarding; applied before the widget exists
        st.session_state.client = st.session_state.pop('next_client')
    client = st.selectbox('Client', list(names), format_func=names.get, key='client')
    st.divider()
    demo = st.toggle('Demo mode (replay)', value=False,
                     help='Replay recorded LLM answers: no API calls, works offline. Turns the LLM analyst on.')
    if demo:   # a separate, locked widget so the live toggle keeps its own setting
        use_llm = st.toggle('LLM analyst', value=True, disabled=True, key='llm_in_demo',
                            help='On in Demo mode, using recorded answers.')
    else:
        use_llm = st.toggle('LLM analyst', value=False, key='llm_live',
                            help='Let the LLM choose actions. Rules are used when it is off or fails.')
    record = role == 'Admin' and not demo and st.checkbox(
        'Record replay', help='Admin: save live LLM answers to replay/ so Demo mode can play them offline.')
    os.environ['REPLAY_MODE'] = 'play' if demo else 'record' if record else replay_default()
    st.divider()
    # Tooltips only on disabled buttons: a focused button keeps its tooltip open over the next one.
    run_clicked = st.button('Run analysis', type='primary', disabled=role == 'Viewer', width='stretch',
                            help='Viewers are read-only.' if role == 'Viewer' else None)
    reset_clicked = st.button('Reset demo', disabled=role not in RESETTERS, width='stretch',
                              help='Engineer or Admin only.' if role not in RESETTERS else None)
    if not is_sandbox():
        st.caption('Connected to real AWS.')

st.html(f"""<div class="co-header">
  <div><div class="co-brand">🌱 CarbonOps</div><div class="co-tagline">Governed GreenOps for IT infrastructure</div></div>
  <div class="co-who">Client <b>{esc(names[client])}</b> · Signed in as <b>{esc(user)}</b> {badge('role', role)}</div>
</div>""")

tabs = st.tabs(['Run', 'Approvals', 'Savings', 'Audit', 'Report', 'Policy'])
with tabs[0]:
    strip_slot = st.empty()

if reset_clicked:
    do_reset(client, names[client])
if run_clicked:
    do_run(client, role, user, use_llm, strip_slot)

flash = st.session_state.pop('flash', None)
if flash:
    st.toast(flash[1], icon='⚠️' if flash[0] == 'error' else '✅')

view = View(outcomes().get(client))
s = view.state

# --- Run -------------------------------------------------------------------------
with tabs[0]:
    strip_slot.html(strip_html(view))
    if view.outcome is None:
        st.info(f'Press **Run analysis** to scan {names[client]}’s estate. Nothing changes until policy allows it.')
    elif s.get('error'):
        st.error(s['error'])
    already = [r.id for r in view.resources.values() if r.kind == 'vm' and r.state != 'running']
    if already and view.totals:
        st.info(f"{len(already)} VM(s) were already stopped by an earlier run, so they need no action now. "
                f"Press **Reset demo** for a fresh estate.")
    if view.totals:
        t = view.totals
        cols = st.columns(6)
        cols[0].metric('Auto-executed', t['auto'], border=True)
        cols[1].metric('Awaiting approval', len(view.pending), border=True)
        cols[2].metric('Blocked', t['block'], border=True)
        cols[3].metric('No action needed', t['no_finding'], border=True)
        cols[4].metric('₹ saved / month', inr(t['inr_month']), border=True,
                       help=f"Realised. {inr(t['pending_inr_month'])}/month more is awaiting approval.")
        cols[5].metric('CO₂e saved / yr', f"{t['kgco2e_year']:,.0f} kg", border=True,
                       help=f"Realised. {t['pending_kgco2e_year']:,.1f} kg/year more is awaiting approval.")
        if view.pending:
            st.caption(f"{inr(t['pending_inr_month'])}/month more is waiting in **Approvals**.")
        analysis = next((e['summary'] for e in s.get('trace', []) if e['node'] == 'analyse'), '')
        if s.get('use_llm') and analysis:
            st.caption(f'Analyst: {analysis}')

        for tier, title in (('AUTO', 'Auto-executed'), ('APPROVE', 'Needs approval'), ('BLOCK', 'Blocked')):
            group = [a for a in view.actions.values() if view.decisions[a.id].tier == tier]
            if group:
                st.html(f'<div class="co-section">{badge(tier, TIER_LABEL[tier])} {esc(title)} · {len(group)}</div>')
                for a in group:
                    action_card(view, a)

        with st.expander('Agent trace'):
            st.dataframe(pd.DataFrame([{
                'Stage': dict(graph.STAGES)[t['stage']], 'Node': t['node'], 'What happened': t['summary'], 'ms': t['ms'],
            } for t in s.get('trace', [])]), hide_index=True, width='stretch')
            proposals = [a for a in view.actions.values() if a.proposed_by != 'tag']
            if proposals:
                st.markdown('**Proposals**')
                st.dataframe(pd.DataFrame([{
                    'Resource': a.resource_id, 'Action': action_label(a.type, a.params),
                    'Proposed by': a.proposed_by, 'Confidence': f'{a.confidence:.0%}', 'Reason': a.reason,
                } for a in proposals]), hide_index=True, width='stretch')

# --- Approvals -------------------------------------------------------------------
with tabs[1]:
    can_approve = role in APPROVERS
    tooltip = None if can_approve else 'Only an Approver or Admin can decide.'
    if view.outcome is None:
        st.info('Nothing to approve yet. Run an analysis first.')
    elif view.pending:
        if not can_approve:
            st.warning(f'You are signed in as **{role}**: you can review, but only an Approver or Admin can decide.')
        thread = view.outcome['thread_id']
        keys = {p['id']: f'verdict-{thread}-{p["id"]}' for p in view.pending}
        top = st.columns([3, 1])
        top[0].markdown(f'**{len(view.pending)} action(s) need a human decision.**')
        top[1].button('Approve all', on_click=approve_all, args=(list(keys.values()),), disabled=not can_approve,
                      help=tooltip, width='stretch')
        for p in view.pending:
            a = view.actions[p['id']]
            with st.container(border=True):
                render_action_card(a, view.decisions[a.id], view.resources[a.resource_id],
                                   view.findings.get(a.resource_id), injected=view.injected.get(a.resource_id))
                c1, c2 = st.columns([1, 2])
                c1.segmented_control('Decision', ['Approve', 'Reject'], key=keys[a.id], disabled=not can_approve,
                                     help=tooltip, label_visibility='collapsed')
                c2.text_input('Comment', key=f'comment-{thread}-{a.id}', placeholder='Comment (optional)',
                              disabled=not can_approve, label_visibility='collapsed')
        choices = {aid: st.session_state.get(key) for aid, key in keys.items()}
        decided = sum(v is not None for v in choices.values())
        st.caption(f'{decided} of {len(choices)} decided.')
        if st.button('Submit decisions', type='primary', disabled=not can_approve or decided < len(choices),
                     help=tooltip):
            verdicts = {aid: 'approve' if v == 'Approve' else 'reject' for aid, v in choices.items()}
            comments = {aid: st.session_state.get(f'comment-{thread}-{aid}', '') for aid in choices}
            try:
                outcomes()[client] = graph.resume(thread, verdicts, approver=user, role=role, comments=comments)
            except Exception as e:
                log.error('resume failed', exc_info=True)
                st.session_state.flash = ('error', f'Could not resume the run: {e}')
            st.rerun()
    else:
        st.success('No pending approvals.')
        decided = [(aid, v) for aid, v in view.approvals.items()]
        if decided:
            st.markdown(f"**Decisions by {esc(s.get('approver', ''))} ({esc(s.get('approver_role', ''))})**")
            for aid, verdict in decided:
                r = view.results.get(aid)
                status = badge(r.status, STATUS_LABEL[r.status]) if r else badge('SKIPPED', 'Rejected')
                detail = esc(r.detail) if r else 'Not executed.'
                comment = s.get('comments', {}).get(aid)
                note = f' · “{esc(comment)}”' if comment else ''
                st.html(f'<div class="co-row">{status} <b>{esc(aid)}</b> {esc(verdict)}d{note} — {detail}</div>')

# --- Savings ---------------------------------------------------------------------
with tabs[2]:
    if not view.totals:
        st.info('Savings appear after a run.')
    else:
        t = view.totals
        st.markdown('**Realised** (executed)')
        c = st.columns(4)
        c[0].metric('₹ / month', inr(t['inr_month']), border=True)
        c[1].metric('₹ / year', inr(t['inr_year']), border=True)
        c[2].metric('kg CO₂e / month', f"{t['kgco2e_month']:,.1f}", border=True)
        c[3].metric('kg CO₂e / year', f"{t['kgco2e_year']:,.1f}", border=True)
        st.markdown('**Pending approval**')
        c = st.columns(4)
        c[0].metric('₹ / month', inr(t['pending_inr_month']), border=True)
        c[1].metric('₹ / year', inr(t['pending_inr_year']), border=True)
        c[2].metric('kg CO₂e / month', f"{t['pending_kgco2e_month']:,.1f}", border=True)
        c[3].metric('kg CO₂e / year', f"{t['pending_kgco2e_year']:,.1f}", border=True)

        rows = [{'Action': f'{a.resource_id} · {action_label(a.type, a.params)}', '₹/month': round(a.inr_month),
                 'kg CO₂e/month': round(a.kgco2e_month, 2), 'State': view.state_of(a),
                 'Tier': view.decisions[a.id].tier,
                 'Result': STATUS_LABEL[view.results[a.id].status] if a.id in view.results else '—'}
                for a in view.actions.values()]
        chart_rows = [r for r in rows if r['₹/month'] > 0]
        if chart_rows:
            df = pd.DataFrame(chart_rows)
            chart = (alt.Chart(df).mark_bar(cornerRadiusEnd=4, size=16).encode(
                x=alt.X('₹/month:Q', title='₹ per month', axis=alt.Axis(format=',.0f', gridColor='#E1E0D9')),
                y=alt.Y('Action:N', sort='-x', title=None, axis=alt.Axis(labelLimit=320)),
                color=alt.Color('State:N', scale=alt.Scale(domain=CHART_STATES, range=CHART_COLORS),
                                legend=alt.Legend(orient='top', title=None)),
                tooltip=['Action', alt.Tooltip('₹/month:Q', format=',.0f'), 'kg CO₂e/month', 'State'],
            ).properties(height=34 * len(df) + 40))
            st.altair_chart(chart, width='stretch')
        prices, carbon = config.load_prices(), config.load_carbon()
        st.caption(
            f'₹ = on-demand USD/hour × hours avoided per month ({prices.hours_per_month} h for STOP; office-hours '
            f'share for SCHEDULE; price difference for RESIZE) × ₹{prices.currency_rate_inr_per_usd}/USD. '
            f'kg CO₂e = (vCPU × ({carbon.min_watts_per_vcpu} + CPU% × ({carbon.max_watts_per_vcpu} − '
            f'{carbon.min_watts_per_vcpu})) W + {carbon.memory_watts_per_gb} W/GB × memory) × PUE {carbon.pue} '
            f'× hours × {carbon.grid_kg_per_kwh} kg/kWh. Sources: AWS Mumbai ({prices.region}) price list; '
            f'Cloud Carbon Footprint; CEA CO₂ Baseline Database v22.0. Volumes show no ₹ until the gp3 price is confirmed.')
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')

# --- Audit -----------------------------------------------------------------------
with tabs[3]:
    path = config.audit_path(client)
    try:
        events = audit.read_all(path)
    except Exception as e:
        events = []
        st.error(f'The audit log could not be read: {e}')
    if events:
        st.dataframe(pd.DataFrame([{
            'Time': datetime.fromisoformat(e.ts).astimezone().strftime('%d %b %H:%M:%S'), 'Actor': e.actor, 'Role': e.role,
            'Kind': e.kind, 'Summary': event_summary(e),
        } for e in events]), hide_index=True, width='stretch', height=420)
    else:
        st.info('No audit events yet for this client.')
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button('Verify chain', width='stretch'):
        ok, broken = audit.verify(path)
        if ok:
            st.success(f'Chain intact: {len(events)} events.')
        else:
            st.error(f'Chain broken at line {broken + 1}: that event or the one before it was altered.')
    c2.download_button('Download JSONL', data=path.read_bytes() if path.exists() else b'',
                       file_name=f'carbonops-audit-{client}.jsonl', mime='application/jsonl',
                       disabled=not path.exists(), width='stretch')

# --- Report ----------------------------------------------------------------------
with tabs[4]:
    if s.get('report'):
        st.markdown(s['report'])
        stamp = datetime.now().strftime('%Y-%m-%d')
        doc = f"# CarbonOps report: {names[client]}\n\n_{stamp}_\n\n{s['report']}\n"
        st.download_button('Download report (Markdown)', data=doc.encode('utf-8'),
                           file_name=f'carbonops-report-{client}-{stamp}.md', mime='text/markdown')
    elif view.pending:
        st.info('The report is written when the run completes. Resolve the pending approvals first.')
    else:
        st.info('Run an analysis to get a report.')

# --- Policy ----------------------------------------------------------------------
with tabs[5]:
    if role != 'Admin':
        st.info('Only an Admin can view client policies.')
    else:
        st.markdown('**Same engine, different policy.** Every client runs the identical detector, guard and '
                    'executor; only this YAML differs. Onboarding a client is configuration, not code.')
        policies = [config.load_policy(c) for c in names]
        st.dataframe(pd.DataFrame([{
            'Client': p.name, 'Auto actions': ', '.join(p.auto_actions), 'Auto environments': ', '.join(p.auto_envs),
            'Auto limit ₹/month': inr(p.auto_limit_inr_month), 'Min confidence': p.min_confidence_auto,
            'Protected': ', '.join(p.protected_envs), 'Never': ', '.join(p.never),
        } for p in policies]), hide_index=True, width='stretch')
        st.code((config.ROOT / 'policies' / f'{client}.yaml').read_text(encoding='utf-8'), language='yaml')

        with st.form('onboard'):
            st.markdown('**Onboard a client** — writes a policy from the template and a demo estate. No code changes.')
            new_name = st.text_input('Client name', placeholder='Globex Corp')
            c1, c2 = st.columns(2)
            new_actions = c1.multiselect('Auto actions', ['STOP', 'SCHEDULE', 'RESIZE', 'SNAPSHOT'], default=['STOP'])
            new_envs = c2.multiselect('Auto environments', ['dev', 'test', 'staging'], default=['dev'],
                                      help='prod is always protected.')
            c3, c4, c5 = st.columns(3)
            new_limit = c3.number_input('Auto limit (₹/month)', min_value=0, max_value=10_00_000, value=30_000, step=5_000)
            new_conf = c4.slider('Confidence threshold', 0.0, 1.0, 0.7, 0.05)
            new_source = c5.selectbox('Copy demo estate from', list(names), format_func=names.get)
            if st.form_submit_button('Onboard client', type='primary'):
                try:
                    slug = onboard.onboard(new_name, new_actions, new_envs, float(new_limit), float(new_conf), new_source)
                except ValueError as e:
                    st.error(f'Could not onboard: {e}')
                else:
                    st.session_state.next_client = slug
                    st.session_state.flash = ('success', f'{new_name.strip()} onboarded. Same engine, new client, zero code.')
                    st.rerun()
