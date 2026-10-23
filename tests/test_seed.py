import json

import pytest

from carbonops import config, seed
from carbonops.providers.aws import AwsProvider, boto_client, tag_dict


@pytest.mark.parametrize('resource_id', ['dev-ci-runner', 'staging-web', 'prod-db-01'])
def test_seeded_metrics_match_estate(aws, metrics_a, resource_id):
    seed.seed('client_a')
    got = AwsProvider('client_a').get_metrics(resource_id)
    want = metrics_a[resource_id]
    assert got.cpu_avg == pytest.approx(want.cpu_avg, abs=0.5)
    assert got.cpu_p95 == pytest.approx(want.cpu_p95, abs=0.5)
    assert got.cpu_max == pytest.approx(want.cpu_max, abs=0.5)
    assert got.hours_per_week == want.hours_per_week


@pytest.mark.parametrize('client', ['client_a', 'client_b'])
def test_cpu_series_is_exact_for_every_vm(estates, client):
    for m in estates[client][1].values():
        series = seed.cpu_series(m.cpu_avg, m.cpu_p95, m.cpu_max)
        assert len(series) == 168
        assert sum(series) / 168 == pytest.approx(m.cpu_avg, abs=1e-6)
        assert sorted(series)[159] == pytest.approx(m.cpu_p95)   # nearest-rank p95
        assert max(series) == pytest.approx(m.cpu_max)
        assert min(series) >= 0


def test_seed_round_trips_the_estate(aws, estates):
    ids = seed.seed('client_a')
    assert len(ids) == 10
    listed = {r.id: r for r in AwsProvider('client_a').list_resources('client_a')}
    for r in estates['client_a'][0]:
        got = listed[r.id]
        assert (got.kind, got.env, got.owner, got.tags, got.instance_type) == (r.kind, r.env, r.owner, r.tags, r.instance_type)
    assert listed['vol-orphan-01'].attached is False
    assert AwsProvider('client_b').list_resources('client_b') == []


def test_aws_tags_follow_scheduler_conventions_and_never_hold_sim_state(aws):
    ids = seed.seed('client_a')
    inst = boto_client('ec2').describe_instances(InstanceIds=[ids['staging-api']])['Reservations'][0]['Instances'][0]
    tags = tag_dict(inst['Tags'])
    assert tags['Name'] == 'staging-api' and tags['client'] == 'client_a' and tags['uptime_hours_week'] == '168'
    assert not any('health' in k or 'health' in v or 'sim' == k for k, v in tags.items())
    sim = json.loads((config.state_dir() / 'sim_state.json').read_text())
    assert sim['client_a'] == {'staging-api': {'health_fail_after': 'RESIZE'}}


def test_seed_is_idempotent_and_reset_rebuilds(aws):
    first = seed.seed('client_a')
    assert seed.seed('client_a') == first
    rebuilt = seed.reset('client_a')
    assert set(rebuilt) == set(first) and set(rebuilt.values()).isdisjoint(first.values())
    assert len(AwsProvider('client_a').list_resources('client_a')) == 10


def test_seed_refuses_real_aws(aws, monkeypatch):
    monkeypatch.setenv('CARBONOPS_AWS', 'real')
    with pytest.raises(RuntimeError, match='never a real account'):
        seed.seed('client_a')
