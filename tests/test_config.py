from carbonops.models import Metrics, Resource

ALL_TYPES = {'t3.large', 't3.xlarge', 'm5.large', 'm5.xlarge', 'm5.2xlarge'}


def test_estate_a_shape(estates):
    resources, metrics, sims = estates['client_a']
    assert len(resources) == 10
    assert len(metrics) == 9
    assert all(isinstance(r, Resource) for r in resources)
    assert all(isinstance(m, Metrics) for m in metrics.values())
    assert set(metrics) == {r.id for r in resources if r.kind == 'vm'}
    assert {r.client for r in resources} == {'client_a'}


def test_estate_b_ids_are_prefixed(estates):
    resources, metrics, sims = estates['client_b']
    assert len(resources) == 10
    assert all(r.id.startswith('nb-') for r in resources)
    assert all(rid.startswith('nb-') for rid in [*metrics, *sims])
    assert {r.client for r in resources} == {'client_b'}


def test_estate_b_mirrors_estate_a(estates):
    a, b = estates['client_a'][0], estates['client_b'][0]
    strip = lambda r: r.model_dump(exclude={'id', 'client'})
    assert [strip(r) for r in a] == [strip(r) for r in b]
    assert [r.id for r in b] == [f'nb-{r.id}' for r in a]


def test_every_resource_has_env_and_owner_tags(estates):
    for resources, _, _ in estates.values():
        for r in resources:
            assert r.tags['env'] == r.env
            assert r.tags['owner'] == r.owner


def test_sim_is_split_off_and_never_a_tag(estates):
    resources, _, sims = estates['client_a']
    assert sims == {'staging-api': {'health_fail_after': 'RESIZE'}}
    for r in resources:
        assert 'sim' not in r.tags and 'health_fail_after' not in r.tags


def test_orphan_volume(resources_a):
    v = resources_a['vol-orphan-01']
    assert (v.kind, v.size_gb, v.attached, v.instance_type) == ('volume', 200, False, None)


def test_free_text_tags(resources_a):
    assert 'quarter-end' in resources_a['reporting-vm'].tags['purpose']
    assert 'terminate' in resources_a['sandbox-ml'].tags['note']


def test_policies_load(policies):
    a, b = policies['client_a'], policies['client_b']
    assert (a.name, a.auto_actions, a.auto_envs) == ('Acme Retail', ['STOP', 'SCHEDULE'], ['dev', 'test'])
    assert (b.name, b.auto_actions, b.auto_envs) == ('Northbank Finance', ['STOP'], ['dev'])
    for p in (a, b):
        assert p.auto_limit_inr_month == 30000
        assert p.min_confidence_auto == 0.7
        assert p.protected_envs == ['prod']
        assert p.allowed_actions == ['STOP', 'SCHEDULE', 'RESIZE', 'SNAPSHOT', 'KEEP']
        assert p.never == ['DELETE', 'TERMINATE']
        assert p.office_hours.hours_per_week == 50


def test_prices_have_all_types(prices):
    assert set(prices.instances) == ALL_TYPES
    assert prices.region == 'ap-south-1'
    assert prices.currency_rate_inr_per_usd == 94.5
    assert prices.hours_per_month == 730
    assert prices.instances['m5.2xlarge'].usd_hour == 0.404
    assert prices.downsize == {'m5.2xlarge': 'm5.xlarge', 'm5.xlarge': 'm5.large', 't3.xlarge': 't3.large'}


def test_carbon_loads(carbon):
    assert carbon.grid_kg_per_kwh == 0.675
    assert carbon.pue == 1.135
    assert carbon.embodied_included is False
