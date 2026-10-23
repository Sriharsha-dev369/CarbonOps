import pytest

from carbonops import calc

REL = 0.01  # 1% relative tolerance


@pytest.mark.parametrize('resource_id, type_, cpu_avg_override, inr, kg', [
    ('dev-ci-runner', 'STOP', None, 27_870, 10.44),
    ('staging-web', 'RESIZE', 15, 13_935, 5.10),   # m5.2xlarge -> m5.xlarge, metrics overridden to avg 15
    ('dev-api-01', 'SCHEDULE', None, 9_788, 3.71),  # 168 -> 50 h/week
    ('vol-orphan-01', 'SNAPSHOT', None, 0, 0),
])
def test_price_action(resources_a, metrics_a, prices, carbon, make_action, resource_id, type_, cpu_avg_override, inr, kg):
    resource = resources_a[resource_id]
    metrics = metrics_a.get(resource_id)
    if cpu_avg_override is not None:
        metrics = metrics.model_copy(update={'cpu_avg': cpu_avg_override})
    action = make_action(resource, type_)

    priced = calc.price_action(action, resource, metrics, prices, carbon)

    assert priced.inr_month == pytest.approx(inr, rel=REL)
    assert priced.kgco2e_month == pytest.approx(kg, rel=REL)


def test_price_action_returns_a_copy(resources_a, metrics_a, prices, carbon, make_action):
    resource = resources_a['dev-ci-runner']
    action = make_action(resource, 'STOP')
    priced = calc.price_action(action, resource, metrics_a[resource.id], prices, carbon)
    assert priced is not action
    assert (action.inr_month, action.kgco2e_month) == (0.0, 0.0)
    assert priced.model_dump(exclude={'inr_month', 'kgco2e_month'}) == action.model_dump(exclude={'inr_month', 'kgco2e_month'})


def test_keep_is_free(resources_a, metrics_a, prices, carbon, make_action):
    resource = resources_a['prod-db-01']
    priced = calc.price_action(make_action(resource, 'KEEP'), resource, metrics_a[resource.id], prices, carbon)
    assert (priced.inr_month, priced.kgco2e_month) == (0, 0)
