import shutil

import pytest

from carbonops import config, onboard


@pytest.fixture
def root(tmp_path):
    for sub in ('config', 'policies', 'data'):
        (tmp_path / sub).mkdir()
    shutil.copy(config.ROOT / 'config' / 'policy_template.yaml', tmp_path / 'config')
    for f in ('estate_a.yaml', 'estate_b.yaml'):
        shutil.copy(config.ROOT / 'data' / f, tmp_path / 'data')
    return tmp_path


def test_onboard_writes_a_valid_policy_and_estate(root):
    slug = onboard.onboard('Globex Corp.', ['STOP', 'SCHEDULE'], ['dev'], 20000, 0.8, 'client_b', root=root)
    assert slug == 'globex_corp'
    import yaml
    policy = config.Policy.model_validate(yaml.safe_load((root / 'policies' / 'globex_corp.yaml').read_text()))
    assert (policy.name, policy.auto_actions, policy.auto_limit_inr_month, policy.min_confidence_auto) == \
        ('Globex Corp.', ['STOP', 'SCHEDULE'], 20000, 0.8)
    assert policy.never == ['DELETE', 'TERMINATE'] and policy.protected_envs == ['prod']
    raw = yaml.safe_load((root / 'data' / 'estate_globex_corp.yaml').read_text())
    resources, metrics, sims = config.parse_estate(raw, 'globex_corp')
    assert len(resources) == 10 and len(metrics) == 9
    assert resources[0].id == 'gc-dev-api-01', "Northbank's nb- prefix is replaced, not stacked"
    assert sims == {'gc-staging-api': {'health_fail_after': 'RESIZE'}}


@pytest.mark.parametrize('name, kwargs, message', [
    ('', {}, 'name'),
    ('Acme', {'auto_actions': ['TERMINATE']}, 'never'),
    ('Acme', {'auto_envs': ['qa']}, 'auto_envs'),
])
def test_onboard_rejects_bad_input(root, name, kwargs, message):
    args = {'auto_actions': ['STOP'], 'auto_envs': ['dev'], 'auto_limit_inr_month': 1000,
            'min_confidence_auto': 0.7, 'copy_estate_from': 'client_a', **kwargs}
    with pytest.raises(ValueError, match=message):
        onboard.onboard(name, root=root, **args)
    assert list((root / 'policies').iterdir()) == []


def test_onboard_refuses_duplicates(root):
    onboard.onboard('Globex', ['STOP'], ['dev'], 1000, 0.7, 'client_a', root=root)
    with pytest.raises(ValueError, match='already exists'):
        onboard.onboard('globex', ['STOP'], ['dev'], 1000, 0.7, 'client_a', root=root)
