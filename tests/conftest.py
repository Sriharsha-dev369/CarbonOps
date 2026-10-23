"""Shared fixtures. Everything is loaded from the real YAML files via carbonops.config."""

from collections.abc import Callable

import pytest

from carbonops import config
from carbonops.models import Action, ActionType, Metrics, Resource


@pytest.fixture(scope='session')
def prices() -> config.Prices:
    return config.load_prices()


@pytest.fixture(scope='session')
def carbon() -> config.Carbon:
    return config.load_carbon()


@pytest.fixture(scope='session')
def policies() -> dict[str, config.Policy]:
    return {c: config.load_policy(c) for c in ('client_a', 'client_b')}


@pytest.fixture(scope='session')
def policy_a(policies: dict[str, config.Policy]) -> config.Policy:
    return policies['client_a']


@pytest.fixture(scope='session')
def estates() -> dict[str, tuple[list[Resource], dict[str, Metrics], dict[str, dict]]]:
    return {c: config.load_estate(c) for c in ('client_a', 'client_b')}


@pytest.fixture(scope='session')
def resources_a(estates) -> dict[str, Resource]:
    return {r.id: r for r in estates['client_a'][0]}


@pytest.fixture(scope='session')
def metrics_a(estates) -> dict[str, Metrics]:
    return estates['client_a'][1]


@pytest.fixture
def make_action(prices: config.Prices) -> Callable[..., Action]:
    """Build an Action the way detector/analyst would, with params filled per type."""

    def _make(resource: Resource, type_: ActionType, confidence: float = 0.9) -> Action:
        params: dict = {}
        if type_ == 'SCHEDULE':
            params = {'hours_per_week': 50}
        elif type_ == 'RESIZE':
            params = {'to': prices.downsize[resource.instance_type]}
        return Action(id=f'{resource.id}:{type_}', resource_id=resource.id, type=type_,
                      params=params, confidence=confidence)

    return _make
