"""One-click Undo: a DONE action goes back to its restore point."""

import pytest

from carbonops import audit, config, executor, seed
from carbonops.models import Action, Decision
from carbonops.providers.aws import AwsProvider, boto_client, tag_dict


def _instance(name):
    reservations = boto_client('ec2').describe_instances(Filters=[{'Name': 'tag:Name', 'Values': [name]}])
    inst = reservations['Reservations'][0]['Instances'][0]
    return inst['InstanceType'], inst['State']['Name'], tag_dict(inst['Tags'])


def test_undo_stop_and_resize_restores_state_and_type(aws):
    seed.seed('client_a')
    provider, log = AwsProvider('client_a'), config.audit_path('client_a')
    stop = Action(id='dev-ci-runner:STOP', resource_id='dev-ci-runner', type='STOP')
    resize = Action(id='staging-web:RESIZE', resource_id='staging-web', type='RESIZE', params={'to': 'm5.xlarge'})
    done_stop = executor.execute(stop, provider, log, None, decision=Decision(action_id=stop.id, tier='AUTO', reasons=[]))
    done_resize = executor.execute(resize, provider, log, 'asha', role='Approver',
                                   decision=Decision(action_id=resize.id, tier='APPROVE', reasons=[]))
    assert (done_stop.status, done_resize.status) == ('DONE', 'DONE')
    assert _instance('dev-ci-runner')[1] == 'stopped' and _instance('staging-web')[0] == 'm5.xlarge'

    with pytest.raises(ValueError, match='only Approver or Admin'):
        executor.undo(stop, done_stop, provider, log, 'ravi', 'Engineer')

    undone = [executor.undo(stop, done_stop, provider, log, 'asha', 'Approver'),
              executor.undo(resize, done_resize, provider, log, 'asha', 'Admin')]
    assert [u.status for u in undone] == ['UNDONE', 'UNDONE']
    assert _instance('dev-ci-runner')[1] == 'running'
    assert _instance('staging-web')[:2] == ('m5.2xlarge', 'running')
    events = audit.read_all(log)
    assert [e.actor for e in events if e.kind == 'undo'] == ['asha', 'asha']
    assert audit.verify(log) == (True, None)

    with pytest.raises(ValueError, match='no completed change'):
        executor.undo(stop, undone[0], provider, log, 'asha', 'Approver')


def test_undo_schedule_removes_the_schedule(aws):
    seed.seed('client_a')
    provider, log = AwsProvider('client_a'), config.audit_path('client_a')
    action = Action(id='dev-api-01:SCHEDULE', resource_id='dev-api-01', type='SCHEDULE', params={'hours_per_week': 50})
    done = executor.execute(action, provider, log, None, decision=Decision(action_id=action.id, tier='AUTO', reasons=[]))
    assert _instance('dev-api-01')[2]['schedule'] == 'office-hours'
    executor.undo(action, done, provider, log, 'asha', 'Approver')
    tags = _instance('dev-api-01')[2]
    assert 'schedule' not in tags and tags['uptime_hours_week'] == '168'
