"""Analyst and reporter agents with a fake LLM: what the model says is validated, never trusted."""

import json

import pytest

from carbonops import analyst, calc, config, detector, llm, reporter
from carbonops.models import ExecResult


@pytest.fixture
def inputs(estates, policy_a):
    resources, metrics, _ = estates['client_a']
    findings = detector.detect(resources, metrics, policy_a)
    return findings, {r.id: r for r in resources}, policy_a


def fake_llm(monkeypatch, reply=None, error=None):
    calls = []

    def chat_json(system, user, *, schema_hint, replay_key, timeout=20):
        calls.append({'system': system, 'user': json.loads(user), 'replay_key': replay_key})
        if error:
            raise error
        return reply

    monkeypatch.setattr(llm, 'chat_json', chat_json)
    return calls


def _good_reply(findings, resources, **overrides):
    actions = []
    for f in findings:
        r = resources[f.resource_id]
        entry = {'resource_id': r.id, 'type': {'IDLE': 'STOP', 'ALWAYS_ON': 'SCHEDULE', 'OVERSIZED': 'RESIZE',
                                               'UNATTACHED': 'SNAPSHOT'}[f.kind],
                 'params': {}, 'reason': f'LLM reason for {r.id}', 'confidence': 0.8}
        if entry['type'] == 'SCHEDULE':
            entry['params'] = {'hours_per_week': 50}
        if entry['type'] == 'RESIZE':
            entry['params'] = {'to': config.load_prices().downsize[r.instance_type]}
        entry.update(overrides.get(r.id, {}))
        actions.append(entry)
    return {'actions': actions}


def test_valid_reply_is_used(monkeypatch, inputs):
    findings, resources, policy = inputs
    reply = _good_reply(findings, resources, **{'reporting-vm': {
        'type': 'SCHEDULE', 'params': {'hours_per_week': 50}, 'confidence': 0.5,
        'reason': 'Purpose tag says quarter-end reporting, so schedule it.'}})
    calls = fake_llm(monkeypatch, reply)
    notes = []
    actions = analyst.propose(findings, resources, policy, notes=notes)

    assert len(calls) == 1 and calls[0]['replay_key'] == 'analyst_client_a', 'one batched call per run'
    assert [a.resource_id for a in actions] == [f.resource_id for f in findings]
    assert {a.proposed_by for a in actions} == {'llm'}
    vm = next(a for a in actions if a.resource_id == 'reporting-vm')
    assert (vm.type, vm.confidence, vm.params) == ('SCHEDULE', 0.5, {'hours_per_week': 50})
    assert 'quarter-end' in vm.reason
    assert notes == [f'LLM proposed {len(findings)} of {len(findings)} actions.']


def test_payload_marks_tags_untrusted_and_hides_sim_state(monkeypatch, inputs):
    findings, resources, policy = inputs
    calls = fake_llm(monkeypatch, _good_reply(findings, resources))
    analyst.propose(findings, resources, policy)
    payload = calls[0]['user']
    sandbox = next(f for f in payload['findings'] if f['id'] == 'sandbox-ml')
    assert 'terminate' in sandbox['untrusted_tags']['note']
    assert 'tags' not in sandbox
    assert payload['allowed_actions'] == ['STOP', 'SCHEDULE', 'RESIZE', 'SNAPSHOT', 'KEEP']
    text = json.dumps(payload)
    assert 'health_fail_after' not in text and 'sim' not in payload


@pytest.mark.parametrize('error', [ValueError('LLM did not return valid JSON'), TimeoutError('20 s'),
                                   llm.ReplayMissing('no recording')])
def test_any_llm_failure_falls_back_to_rules(monkeypatch, inputs, error):
    findings, resources, policy = inputs
    fake_llm(monkeypatch, error=error)
    notes = []
    actions = analyst.propose(findings, resources, policy, notes=notes)
    prices = config.load_prices()
    expected = [detector.default_action(f, resources[f.resource_id], prices, policy) for f in findings]
    assert actions == expected
    assert notes[0].startswith('LLM unavailable — rules used')


def test_reply_without_actions_falls_back(monkeypatch, inputs):
    findings, resources, policy = inputs
    fake_llm(monkeypatch, {'answer': 'stop everything'})
    assert {a.proposed_by for a in analyst.propose(findings, resources, policy)} == {'rules'}


def test_proposed_terminate_is_rejected(monkeypatch, inputs):
    findings, resources, policy = inputs
    reply = _good_reply(findings, resources, **{
        'sandbox-ml': {'type': 'TERMINATE', 'reason': 'The tag says to terminate.'},
        'prod-web-01': {'type': 'DELETE'},
    })
    fake_llm(monkeypatch, reply)
    notes = []
    actions = {a.resource_id: a for a in analyst.propose(findings, resources, policy, notes=notes)}
    assert not any(a.type in ('TERMINATE', 'DELETE') for a in actions.values())
    assert (actions['sandbox-ml'].type, actions['sandbox-ml'].proposed_by) == ('STOP', 'rules')
    assert actions['prod-web-01'].proposed_by == 'rules'
    assert 'sandbox-ml: TERMINATE is not allowed here' in notes[0]


def test_unsafe_proposals_are_corrected_or_rejected(monkeypatch, inputs):
    findings, resources, policy = inputs
    reply = _good_reply(findings, resources, **{
        'dev-ci-runner': {'confidence': 7},                                # clamped
        'staging-web': {'params': {'to': 'm5.24xlarge'}},                   # not smaller -> one step down
        'test-db-02': {'params': {'hours_per_week': 500}},                  # nonsense -> office hours
        'vol-orphan-01': {'type': 'STOP'},                                  # STOP on a volume -> rules
    })
    reply['actions'].append({'resource_id': 'prod-db-01', 'type': 'STOP', 'reason': 'x', 'confidence': 0.9})
    reply['actions'].append('not even an object')
    fake_llm(monkeypatch, reply)
    actions = {a.resource_id: a for a in analyst.propose(findings, resources, policy)}
    assert actions['dev-ci-runner'].confidence == 1.0
    assert actions['staging-web'].params == {'to': 'm5.xlarge'}
    assert actions['test-db-02'].params == {'hours_per_week': 50}
    assert (actions['vol-orphan-01'].type, actions['vol-orphan-01'].proposed_by) == ('SNAPSHOT', 'rules')
    assert 'prod-db-01' not in actions, 'resources without a finding are ignored'


# --- reporter ---------------------------------------------------------------

@pytest.fixture
def run_totals(estates, policy_a):
    resources, metrics, _ = estates['client_a']
    findings = detector.detect(resources, metrics, policy_a)
    results = [ExecResult(action_id='staging-api:RESIZE', status='ROLLED_BACK',
                          detail='Health check failed: 850 ms > 300 ms threshold.')]
    totals = calc.totals(resources, findings, [], [], results)
    totals.update(inr_month=96166.0, inr_year=1153993.0, kgco2e_year=439.8, done=7, auto=3, approve=5, block=2)
    return results, totals


def test_report_uses_llm_when_numbers_check_out(monkeypatch, run_totals):
    results, totals = run_totals
    fake_llm(monkeypatch, {'summary': 'Acme Retail saves ₹96,166/month (₹11,53,993/year) and 439.8 kg CO₂e/year. '
                                      '3 actions ran automatically, 5 needed approval and 2 were blocked.'})
    notes = []
    text = reporter.summarize('client_a', results, totals, notes=notes)
    assert text.startswith('Acme Retail saves ₹96,166/month')
    assert 'LLM' in notes[0]


@pytest.mark.parametrize('summary', [
    'Acme Retail saves ₹1,20,000/month.',          # invented number
    'word ' * 151,                                 # too long
    '',
])
def test_report_falls_back_to_template(monkeypatch, run_totals, summary):
    results, totals = run_totals
    fake_llm(monkeypatch, {'summary': summary})
    notes = []
    text = reporter.summarize('client_a', results, totals, notes=notes)
    assert text == reporter.template_report('client_a', results, totals)
    assert notes[0].startswith('Report: template used')


# --- replay -------------------------------------------------------------------

def test_replay_records_and_plays_only_the_same_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, 'REPLAY_DIR', tmp_path)
    monkeypatch.setattr(llm, '_complete', lambda messages, timeout: '{"answer": 42}')
    monkeypatch.setenv('REPLAY_MODE', 'record')
    assert llm.chat_json('sys', 'user', schema_hint='{}', replay_key='k') == {'answer': 42}

    monkeypatch.setattr(llm, '_complete', lambda messages, timeout: pytest.fail('play must not call the API'))
    monkeypatch.setenv('REPLAY_MODE', 'play')
    assert llm.chat_json('sys', 'user', schema_hint='{}', replay_key='k') == {'answer': 42}
    with pytest.raises(llm.ReplayMissing):
        llm.chat_json('sys', 'different user', schema_hint='{}', replay_key='k')
    with pytest.raises(llm.ReplayMissing):
        llm.chat_json('sys', 'user', schema_hint='{}', replay_key='never-recorded')


def test_invalid_json_is_retried_once_then_raises(monkeypatch):
    replies = iter(['not json', '{"ok": true}'])
    monkeypatch.setenv('REPLAY_MODE', 'off')
    monkeypatch.setattr(llm, '_complete', lambda messages, timeout: next(replies))
    assert llm.chat_json('s', 'u', schema_hint='{}', replay_key='k') == {'ok': True}
    monkeypatch.setattr(llm, '_complete', lambda messages, timeout: 'still not json')
    with pytest.raises(ValueError):
        llm.chat_json('s', 'u', schema_hint='{}', replay_key='k')
