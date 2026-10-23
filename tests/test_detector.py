import pytest

from carbonops import detector
from carbonops.models import Finding

EXPECTED_A = {
    'dev-api-01': 'ALWAYS_ON',
    'dev-ci-runner': 'IDLE',
    'test-db-02': 'ALWAYS_ON',
    'staging-web': 'OVERSIZED',
    'staging-api': 'OVERSIZED',
    'reporting-vm': 'IDLE',
    'sandbox-ml': 'IDLE',
    'prod-web-01': 'OVERSIZED',
    'prod-db-01': None,
    'vol-orphan-01': 'UNATTACHED',
}


@pytest.mark.parametrize('client, prefix', [('client_a', ''), ('client_b', 'nb-')])
def test_detect_finding_kinds(estates, policies, client, prefix):
    resources, metrics, _ = estates[client]
    findings = detector.detect(resources, metrics, policies[client])

    by_id = {f.resource_id: f.kind for f in findings}
    assert len(by_id) == len(findings), 'at most one finding per resource'
    assert {rid: by_id.get(f'{prefix}{rid}') for rid in EXPECTED_A} == EXPECTED_A
    assert all(f.evidence for f in findings), 'evidence holds the triggering metric values'


def test_detect_evidence_for_idle(estates, policy_a):
    resources, metrics, _ = estates['client_a']
    finding = next(f for f in detector.detect(resources, metrics, policy_a) if f.resource_id == 'dev-ci-runner')
    assert finding.evidence.items() >= {'cpu_avg': 2, 'cpu_max': 9}.items()


@pytest.mark.parametrize('resource_id, kind, action_type', [
    ('dev-ci-runner', 'IDLE', 'STOP'),
    ('dev-api-01', 'ALWAYS_ON', 'SCHEDULE'),
    ('staging-web', 'OVERSIZED', 'RESIZE'),
    ('vol-orphan-01', 'UNATTACHED', 'SNAPSHOT'),
])
def test_default_action_type(resources_a, prices, policy_a, resource_id, kind, action_type):
    action = detector.default_action(Finding(resource_id=resource_id, kind=kind, evidence={}),
                                     resources_a[resource_id], prices, policy_a)
    assert action.type == action_type
    assert action.resource_id == resource_id
    assert action.proposed_by == 'rules'


def test_default_action_schedule_uses_office_hours(resources_a, prices, policy_a):
    action = detector.default_action(Finding(resource_id='dev-api-01', kind='ALWAYS_ON', evidence={}),
                                     resources_a['dev-api-01'], prices, policy_a)
    assert action.params == {'hours_per_week': 50}


def test_default_action_resize_one_step_down(resources_a, prices, policy_a):
    action = detector.default_action(Finding(resource_id='staging-api', kind='OVERSIZED', evidence={}),
                                     resources_a['staging-api'], prices, policy_a)
    assert action.type == 'RESIZE'
    assert action.params['to'] == 'm5.large'


@pytest.mark.parametrize('resource_id, confidence', [
    ('reporting-vm', 0.6),   # has a free-text `purpose` tag
    ('sandbox-ml', 0.6),     # has a free-text `note` tag
    ('dev-ci-runner', 0.9),
])
def test_default_action_confidence(resources_a, prices, policy_a, resource_id, confidence):
    action = detector.default_action(Finding(resource_id=resource_id, kind='IDLE', evidence={}),
                                     resources_a[resource_id], prices, policy_a)
    assert action.confidence == confidence
