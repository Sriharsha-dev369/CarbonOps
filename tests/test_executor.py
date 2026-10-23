from pathlib import Path

import pytest

from carbonops import audit, executor
from carbonops.models import Action, Decision, HealthCheck


class FakeProvider:
    """Records calls; health fails when `healthy` is False; `apply` raises when `boom` is set."""

    def __init__(self, healthy: bool = True, boom: Exception | None = None):
        self.healthy, self.boom, self.calls = healthy, boom, []

    def snapshot(self, resource_id):
        self.calls.append('snapshot')
        return 'snap-1'

    def apply(self, action):
        self.calls.append('apply')
        if self.boom:
            raise self.boom

    def health_ok(self, resource_id):
        self.calls.append('health_ok')
        return HealthCheck(ok=self.healthy, latency_ms=120 if self.healthy else 850, threshold_ms=300)

    def restore(self, resource_id, snapshot_id):
        self.calls.append('restore')


def _action(type_='STOP', params=None):
    return Action(id=f'vm-1:{type_}', resource_id='vm-1', type=type_, params=params or {})


def _decision(action, tier):
    return Decision(action_id=action.id, tier=tier, reasons=['test'])


def _kinds(path):
    return [e.kind for e in audit.read_all(path)]


def test_auto_action_runs_and_is_audited(tmp_path):
    path, provider, a = tmp_path / 'a.jsonl', FakeProvider(), _action()
    result = executor.execute(a, provider, path, None, decision=_decision(a, 'AUTO'))
    assert result.status == 'DONE'
    assert provider.calls == ['snapshot', 'apply', 'health_ok']
    assert _kinds(path) == ['snapshot', 'apply', 'health_check', 'done']
    assert {e.actor for e in audit.read_all(path)} == {'system'}
    assert audit.verify(path) == (True, None)


def test_failed_health_check_rolls_back(tmp_path):
    path, provider, a = tmp_path / 'a.jsonl', FakeProvider(healthy=False), _action('RESIZE', {'to': 'm5.large'})
    result = executor.execute(a, provider, path, 'asha', decision=_decision(a, 'APPROVE'), role='Approver')
    assert result.status == 'ROLLED_BACK'
    assert '850 ms > 300 ms threshold' in result.detail
    assert provider.calls == ['snapshot', 'apply', 'health_ok', 'restore']
    assert _kinds(path) == ['snapshot', 'apply', 'health_check', 'rollback']
    assert {(e.actor, e.role) for e in audit.read_all(path)} == {('asha', 'Approver')}


def test_error_restores_and_fails(tmp_path):
    path, provider, a = tmp_path / 'a.jsonl', FakeProvider(boom=RuntimeError('api down')), _action()
    result = executor.execute(a, provider, path, None, decision=_decision(a, 'AUTO'))
    assert result.status == 'FAILED' and 'api down' in result.detail
    assert provider.calls == ['snapshot', 'apply', 'restore']
    assert _kinds(path) == ['snapshot', 'rollback', 'failed']


@pytest.mark.parametrize('tier, approved_by, role, type_, why', [
    ('BLOCK', None, 'system', 'STOP', 'Blocked'),
    ('BLOCK', 'asha', 'Admin', 'STOP', 'Blocked'),               # approval cannot override a block
    ('APPROVE', None, 'system', 'STOP', 'Needs approval'),
    ('APPROVE', 'ravi', 'Engineer', 'STOP', 'Engineer cannot approve'),
    ('APPROVE', 'vik', 'Viewer', 'STOP', 'Viewer cannot approve'),
    ('AUTO', None, 'system', 'TERMINATE', 'never executed'),
    ('AUTO', None, 'system', 'DELETE', 'never executed'),
    ('AUTO', None, 'system', 'KEEP', 'nothing to change'),
])
def test_refused_actions_never_touch_the_cloud(tmp_path, tier, approved_by, role, type_, why):
    path, provider, a = tmp_path / 'a.jsonl', FakeProvider(), _action(type_)
    result = executor.execute(a, provider, path, approved_by, decision=_decision(a, tier), role=role)
    assert result.status == 'SKIPPED' and why in result.detail
    assert provider.calls == []
    assert _kinds(path) == ['skipped']


def test_mismatched_decision_is_refused(tmp_path):
    a, other = _action(), _action('SCHEDULE')
    result = executor.execute(a, FakeProvider(), tmp_path / 'a.jsonl', None, decision=_decision(other, 'AUTO'))
    assert result.status == 'SKIPPED'


def test_executor_never_imports_llm_or_agents():
    src = Path(executor.__file__).read_text(encoding='utf-8')
    assert 'import llm' not in src
    assert 'analyst' not in src
    assert 'reporter' not in src
