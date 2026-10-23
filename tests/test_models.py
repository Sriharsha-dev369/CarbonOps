import pytest
from pydantic import ValidationError

from carbonops.models import Action, AuditEvent, Decision, ExecResult, Finding, Metrics, Resource


def test_resource_defaults():
    r = Resource(id='dev-api-01', client='client_a', kind='vm', instance_type='m5.xlarge', env='dev')
    assert (r.state, r.attached, r.tags, r.owner, r.size_gb) == ('running', True, {}, '', None)


def test_resource_rejects_unknown_env_and_kind():
    with pytest.raises(ValidationError):
        Resource(id='x', client='client_a', kind='vm', env='qa')
    with pytest.raises(ValidationError):
        Resource(id='x', client='client_a', kind='bucket', env='dev')


def test_mutable_defaults_are_not_shared():
    a = Resource(id='a', client='client_a', kind='vm', env='dev')
    b = Resource(id='b', client='client_a', kind='vm', env='dev')
    a.tags['k'] = 'v'
    assert b.tags == {}


def test_metrics_validate():
    m = Metrics(cpu_avg=3, cpu_p95=25, cpu_max=45, hours_per_week=168)
    assert m.cpu_avg == 3.0


def test_finding_kind_is_constrained():
    Finding(resource_id='x', kind='IDLE', evidence={'cpu_avg': 2})
    with pytest.raises(ValidationError):
        Finding(resource_id='x', kind='WASTEFUL', evidence={})


def test_action_defaults_and_types():
    a = Action(id='a1', resource_id='x', type='STOP')
    assert (a.params, a.confidence, a.proposed_by, a.inr_month, a.kgco2e_month) == ({}, 0.9, 'rules', 0.0, 0.0)
    with pytest.raises(ValidationError):
        Action(id='a2', resource_id='x', type='REBOOT')
    with pytest.raises(ValidationError):
        Action(id='a3', resource_id='x', type='STOP', proposed_by='human')


def test_decision_and_exec_result():
    assert Decision(action_id='a1', tier='BLOCK', reasons=['prod is protected']).tier == 'BLOCK'
    with pytest.raises(ValidationError):
        Decision(action_id='a1', tier='MAYBE', reasons=[])
    assert ExecResult(action_id='a1', status='ROLLED_BACK').detail == ''
    with pytest.raises(ValidationError):
        ExecResult(action_id='a1', status='PENDING')


def test_audit_event_roles():
    e = AuditEvent(ts='2026-10-24T10:00:00+00:00', actor='system', role='system', kind='finding', payload={})
    assert (e.prev_hash, e.hash) == ('', '')
    AuditEvent(ts='2026-10-24T10:00:00+00:00', actor='asha', role='Approver', kind='approval', payload={})
    with pytest.raises(ValidationError):
        AuditEvent(ts='2026-10-24T10:00:00+00:00', actor='asha', role='Owner', kind='approval', payload={})
