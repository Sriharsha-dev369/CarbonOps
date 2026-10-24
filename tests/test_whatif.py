"""What-if simulator: same guard, different policy knobs; prod and never-actions stay blocked."""

import pytest
import yaml

from carbonops import audit, config, graph, guard, whatif


@pytest.fixture
def run_a(aws):
    state = graph.run('client_a', 'Engineer', 'ravi')['state']
    return state['actions'], {r.id: r for r in state['resources']}, state['decisions']


def test_current_policy_reproduces_the_run(run_a):
    actions, resources, decisions = run_a
    simulated = whatif.simulate(actions, resources, config.load_policy('client_a'))
    assert simulated == decisions
    direct = [guard.evaluate(a, resources[a.resource_id], config.load_policy('client_a'),
                             guard.is_suspicious(resources[a.resource_id])) for a in actions]
    assert simulated == direct


def test_prod_and_never_stay_blocked_at_most_permissive(run_a):
    actions, resources, _ = run_a
    loose = whatif.adjust(config.load_policy('client_a'), auto_limit_inr_month=50_000, min_confidence_auto=0.5,
                          auto_actions=['STOP', 'SCHEDULE', 'RESIZE'], auto_envs=['dev', 'test', 'staging'])
    tiers = {d.action_id: d.tier for d in whatif.simulate(actions, resources, loose)}
    for a in actions:
        if resources[a.resource_id].env == 'prod' or a.type in ('DELETE', 'TERMINATE'):
            assert tiers[a.id] == 'BLOCK', a.id
            assert whatif.always_blocked(a, resources[a.resource_id], loose)
    assert tiers['staging-web:RESIZE'] == 'AUTO'


def test_zero_limit_sends_everything_to_humans(run_a):
    actions, resources, _ = run_a
    strict = whatif.adjust(config.load_policy('client_a'), auto_limit_inr_month=0, min_confidence_auto=0.7,
                           auto_actions=['STOP', 'SCHEDULE'], auto_envs=['dev', 'test'])
    s = whatif.summary(actions, whatif.simulate(actions, resources, strict))
    assert s['auto'] == 0 and s['auto_inr_month'] == 0 and s['block'] == 2
    assert s['human_inr_month'] > 0


def test_save_writes_custom_policy_and_audits(tmp_path):
    policy = config.load_policy('client_a')
    log = tmp_path / 'audit.jsonl'
    with pytest.raises(ValueError, match='Admin'):
        whatif.save(policy, 'ravi', 'Engineer', root=tmp_path, audit_path=log)
    path = whatif.save(policy, 'meera', 'Admin', root=tmp_path, audit_path=log)
    assert path == tmp_path / 'policies' / 'client_a_custom.yaml'
    assert config.Policy.model_validate(yaml.safe_load(path.read_text())) == policy
    assert [e.kind for e in audit.read_all(log)] == ['policy_saved']
