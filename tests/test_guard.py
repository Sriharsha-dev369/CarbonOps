from pathlib import Path

import pytest

from carbonops import calc, guard

# resource, action, confidence, suspicious, Client A tier, Client B tier
CASES = [
    ('dev-api-01', 'SCHEDULE', 0.9, False, 'AUTO', 'APPROVE'),
    ('dev-ci-runner', 'STOP', 0.9, False, 'AUTO', 'AUTO'),
    ('test-db-02', 'SCHEDULE', 0.9, False, 'AUTO', 'APPROVE'),
    ('staging-web', 'RESIZE', 0.9, False, 'APPROVE', 'APPROVE'),
    ('staging-api', 'RESIZE', 0.9, False, 'APPROVE', 'APPROVE'),
    ('reporting-vm', 'SCHEDULE', 0.5, False, 'APPROVE', 'APPROVE'),
    ('sandbox-ml', 'STOP', 0.9, True, 'APPROVE', 'APPROVE'),
    ('prod-web-01', 'RESIZE', 0.9, False, 'BLOCK', 'BLOCK'),
    ('vol-orphan-01', 'SNAPSHOT', 0.9, False, 'APPROVE', 'APPROVE'),
]
CLIENTS = [('client_a', '', 0), ('client_b', 'nb-', 1)]
BASE_IDS = [
    'dev-api-01', 'dev-ci-runner', 'test-db-02', 'staging-web', 'staging-api',
    'reporting-vm', 'sandbox-ml', 'prod-web-01', 'prod-db-01', 'vol-orphan-01',
]


def _decide(estates, policies, prices, carbon, make_action, client, resource_id, type_, confidence, suspicious):
    resources, metrics, _ = estates[client]
    resource = next(r for r in resources if r.id == resource_id)
    action = calc.price_action(make_action(resource, type_, confidence), resource,
                               metrics.get(resource_id), prices, carbon)
    return guard.evaluate(action, resource, policies[client], suspicious)


@pytest.mark.parametrize('client, prefix, col', CLIENTS, ids=[c for c, _, _ in CLIENTS])
@pytest.mark.parametrize('base_id, type_, confidence, suspicious, tier_a, tier_b', CASES,
                         ids=[f'{c[0]}-{c[1]}' for c in CASES])
def test_evaluate_tier(estates, policies, prices, carbon, make_action,
                       client, prefix, col, base_id, type_, confidence, suspicious, tier_a, tier_b):
    decision = _decide(estates, policies, prices, carbon, make_action,
                       client, f'{prefix}{base_id}', type_, confidence, suspicious)
    assert decision.tier == (tier_a, tier_b)[col]
    assert decision.reasons and all(isinstance(r, str) and r for r in decision.reasons)


@pytest.mark.parametrize('client, prefix, col', CLIENTS, ids=[c for c, _, _ in CLIENTS])
@pytest.mark.parametrize('base_id', BASE_IDS)
def test_delete_is_always_blocked(estates, policies, prices, carbon, make_action, client, prefix, col, base_id):
    decision = _decide(estates, policies, prices, carbon, make_action,
                       client, f'{prefix}{base_id}', 'DELETE', 0.9, False)
    assert decision.tier == 'BLOCK'


def test_suspicious_reason_mentions_tags(estates, policies, prices, carbon, make_action):
    decision = _decide(estates, policies, prices, carbon, make_action, 'client_a', 'sandbox-ml', 'STOP', 0.9, True)
    assert any('tag' in r.lower() for r in decision.reasons)


def test_scan_tags(resources_a):
    assert 'TERMINATE' in guard.scan_tags(resources_a['sandbox-ml'])
    assert guard.scan_tags(resources_a['dev-api-01']) == []
    assert guard.scan_tags(resources_a['reporting-vm']) == []


def test_is_suspicious(resources_a):
    assert guard.is_suspicious(resources_a['sandbox-ml']) is True
    assert guard.is_suspicious(resources_a['dev-api-01']) is False
    assert guard.is_suspicious(resources_a['reporting-vm']) is False   # free text, but no instructions


def test_tag_decisions_are_blocked(resources_a):
    pairs = guard.tag_decisions(resources_a['sandbox-ml'])
    assert pairs, 'the injected TERMINATE request must surface as a decision'
    for action, decision in pairs:
        assert action.proposed_by == 'tag'
        assert action.confidence == 0
        assert decision.action_id == action.id
        assert decision.tier == 'BLOCK'
    assert 'TERMINATE' in {a.type for a, _ in pairs}


def test_tag_decisions_empty_for_clean_tags(resources_a):
    assert guard.tag_decisions(resources_a['dev-api-01']) == []


def test_guard_never_imports_llm_or_agents():
    src = Path(guard.__file__).read_text(encoding='utf-8')
    assert 'import llm' not in src
    assert 'analyst' not in src
    assert 'reporter' not in src
