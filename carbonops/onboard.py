"""Onboard a new client with configuration only: a policy from the template plus a copied estate.

Same engine, new client, zero code: the detector, guard and executor are untouched.
"""

import re
from pathlib import Path

import yaml

from carbonops import config
from carbonops.config import Policy

SLUG = re.compile(r'[a-z][a-z0-9_]{1,39}')


def slugify(name: str) -> str:
    """'Globex Corp.' -> 'globex_corp'."""
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return slug if slug[:1].isalpha() else f'client_{slug}'


def _prefix(name: str) -> str:
    """Resource id prefix from the name's initials: 'Globex Corp' -> 'gc-'."""
    letters = ''.join(w[0] for w in re.findall(r'[A-Za-z0-9]+', name.lower()))
    return f'{letters[:3] or "c"}-'


def _strip_common_prefix(ids: list[str]) -> list[str]:
    """Drop a shared 'xx-' id prefix (e.g. Northbank's 'nb-') so the copy gets its own."""
    first = ids[0].split('-', 1)[0] + '-' if '-' in ids[0] else ''
    if first and len(first) <= 4 and all(i.startswith(first) for i in ids):
        return [i.removeprefix(first) for i in ids]
    return ids


def onboard(name: str, auto_actions: list[str], auto_envs: list[str], auto_limit_inr_month: float,
            min_confidence_auto: float, copy_estate_from: str, root: Path = config.ROOT) -> str:
    """Write policies/<slug>.yaml and data/estate_<slug>.yaml; return the new client id.

    Raises ValueError (and writes nothing) if the name is empty or taken, or the result is invalid.
    """
    name = name.strip()
    if not name:
        raise ValueError('Give the client a name.')
    slug = slugify(name)
    if not SLUG.fullmatch(slug):
        raise ValueError(f'"{name}" does not make a usable client id.')
    policy_path = root / 'policies' / f'{slug}.yaml'
    estate_path = root / 'data' / f"estate_{slug.removeprefix('client_')}.yaml"
    if policy_path.exists() or estate_path.exists():
        raise ValueError(f'A client called "{name}" ({slug}) already exists.')

    template = yaml.safe_load((root / 'config' / 'policy_template.yaml').read_text(encoding='utf-8'))
    policy = Policy.model_validate({
        **template, 'client': slug, 'name': name, 'auto_actions': auto_actions, 'auto_envs': auto_envs,
        'auto_limit_inr_month': auto_limit_inr_month, 'min_confidence_auto': min_confidence_auto,
    })
    if set(policy.auto_actions) & set(policy.never):
        raise ValueError('An auto action cannot also be a never action.')

    source_file = root / 'data' / f"estate_{copy_estate_from.removeprefix('client_')}.yaml"
    source = yaml.safe_load(source_file.read_text(encoding='utf-8'))
    prefix = _prefix(name)
    new_ids = [prefix + i for i in _strip_common_prefix([r['id'] for r in source['resources']])]
    estate = {'client': slug, 'resources': [{**r, 'id': new_id} for r, new_id in zip(source['resources'], new_ids)]}
    config.parse_estate(estate, slug, estate_path.name)   # validate before writing anything

    policy_path.write_text(
        f'# Onboarded from the Policy tab; based on config/policy_template.yaml.\n'
        + yaml.safe_dump(policy.model_dump(), sort_keys=False, allow_unicode=True), encoding='utf-8')
    estate_path.write_text(
        f'# Demo estate for {name}, copied from {source_file.name}.\n'
        + yaml.safe_dump(estate, sort_keys=False, allow_unicode=True), encoding='utf-8')
    return slug
