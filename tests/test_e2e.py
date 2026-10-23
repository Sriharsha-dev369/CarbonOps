"""End to end: real graph, in-process AWS (mock_aws), LLM off."""

from collections import Counter

import pytest

from carbonops import audit, detector, graph
from carbonops.providers.aws import AwsProvider, boto_client, tag_dict


def _counts(outcome):
    s = outcome['state']
    tiers = Counter(d.tier for d in s['decisions'])
    no_finding = {r.id for r in s['resources']} - {f.resource_id for f in s['findings']}
    return tiers['AUTO'], tiers['APPROVE'], tiers['BLOCK'], no_finding


def _instances(client):
    reservations = boto_client('ec2').describe_instances(Filters=[{'Name': 'tag:client', 'Values': [client]}])
    out = {}
    for inst in (i for r in reservations['Reservations'] for i in r['Instances']):
        tags = tag_dict(inst['Tags'])
        out[tags['Name']] = (inst['InstanceType'], inst['State']['Name'], tags)
    return out


def _approve_all(outcome, role='Approver'):
    return graph.resume(outcome['thread_id'], {p['id']: 'approve' for p in outcome['pending']},
                        approver='asha', role=role)


def test_client_a_end_to_end(aws):
    outcome = graph.run('client_a', 'Engineer', 'ravi')
    assert _counts(outcome) == (3, 5, 2, {'prod-db-01'})
    blocked = {d.action_id for d in outcome['state']['decisions'] if d.tier == 'BLOCK'}
    assert blocked == {'prod-web-01:RESIZE', 'sandbox-ml:TERMINATE:tag'}
    assert len(outcome['pending']) == 5
    assert {r.action_id: r.status for r in outcome['state']['results']} == {
        'dev-api-01:SCHEDULE': 'DONE', 'dev-ci-runner:STOP': 'DONE', 'test-db-02:SCHEDULE': 'DONE'}, \
        'AUTO actions run before the approval pause'
    prod_before = {k: v for k, v in _instances('client_a').items() if k.startswith('prod-')}

    outcome = _approve_all(outcome)
    state = outcome['state']
    assert outcome['pending'] == [] and not state.get('error')
    results = {r.action_id: r for r in state['results']}
    assert results['staging-api:RESIZE'].status == 'ROLLED_BACK'
    assert '850 ms > 300 ms threshold' in results['staging-api:RESIZE'].detail

    after = _instances('client_a')
    assert after['staging-api'][0] == 'm5.xlarge' and after['staging-api'][1] == 'running'
    assert after['staging-web'][0] == 'm5.xlarge'
    assert after['dev-ci-runner'][1] == 'stopped'
    assert after['dev-api-01'][2]['schedule'] == 'office-hours'
    assert audit.verify(state['audit_path']) == (True, None)

    # Prod resources are never modified.
    assert {k: v for k, v in after.items() if k.startswith('prod-')} == prod_before
    touched = {e.payload.get('resource_id') for e in audit.read_all(state['audit_path'])
               if e.kind in ('snapshot', 'apply', 'rollback', 'done')}
    assert not any(rid.startswith('prod-') for rid in touched)
    assert state['totals']['done'] == 7 and state['totals']['rolled_back'] == 1


def test_client_b_same_engine_different_policy(aws):
    outcome = graph.run('client_b', 'Engineer', 'ravi')
    assert _counts(outcome) == (1, 7, 2, {'nb-prod-db-01'})
    auto = [d.action_id for d in outcome['state']['decisions'] if d.tier == 'AUTO']
    assert auto == ['nb-dev-ci-runner:STOP']


def test_engineer_cannot_approve(aws):
    outcome = _approve_all(graph.run('client_a', 'Engineer', 'ravi'), role='Engineer')
    approved = {r.action_id: r for r in outcome['state']['results']
                if r.action_id in outcome['state']['approvals']}
    assert approved and all(r.status == 'SKIPPED' for r in approved.values())
    assert _instances('client_a')['staging-web'][0] == 'm5.2xlarge'


def test_rejected_actions_do_not_run(aws):
    outcome = graph.run('client_a', 'Engineer', 'ravi')
    outcome = graph.resume(outcome['thread_id'], {}, approver='asha', role='Approver')
    assert set(outcome['state']['approvals'].values()) == {'reject'}
    assert len(outcome['state']['results']) == 3            # only the AUTO ones
    assert outcome['state']['totals']['pending_inr_month'] == 0


def test_node_errors_become_friendly_messages(aws, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('detector exploded')

    monkeypatch.setattr(detector, 'detect', broken)
    outcome = graph.run('client_a', 'Engineer', 'ravi')
    state = outcome['state']
    assert 'detect' in state['error'] and 'detector exploded' in state['error']
    assert outcome['pending'] == [] and 'actions' not in state
    assert [e.kind for e in audit.read_all(state['audit_path'])][-1] == 'error'
    assert state['trace'][-1]['summary'].startswith('Error')


def test_trace_covers_every_stage(aws):
    outcome = _approve_all(graph.run('client_a', 'Engineer', 'ravi'))
    trace = outcome['state']['trace']
    assert [t['node'] for t in trace] == [name for name, _, _ in graph.NODES]
    assert {t['stage'] for t in trace} == {'ingest', 'decide', 'act', 'report'}
    assert all(t['summary'] and t['ms'] >= 0 for t in trace)


def test_second_run_finds_nothing_left_to_stop(aws):
    _approve_all(graph.run('client_a', 'Engineer', 'ravi'))
    again = graph.run('client_a', 'Engineer', 'ravi')
    stopped = {r.id for r in again['state']['resources'] if r.state == 'stopped'}
    assert 'dev-ci-runner' in stopped
    assert not any(f.resource_id in stopped for f in again['state']['findings'])
    assert AwsProvider('client_a').list_resources('client_a')
