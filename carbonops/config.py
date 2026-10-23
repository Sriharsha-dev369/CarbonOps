"""Load the YAML config, policies and demo estates into typed pydantic models."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, model_validator

from carbonops.models import ActionType, Env, Metrics, Resource

ROOT = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    """Where seed/provider keep local demo state (sim_state.json, snapshots.json)."""
    return Path(os.environ.get('CARBONOPS_STATE_DIR') or ROOT / 'carbonops')


def audit_path(client: str) -> Path:
    return Path(os.environ.get('CARBONOPS_AUDIT_DIR') or ROOT / 'audit') / f'{client}.jsonl'


class InstancePrice(BaseModel):
    usd_hour: float
    vcpu: int
    mem_gb: float


class VolumePrices(BaseModel):
    gp3_usd_gb_month: float      # UNCONFIRMED: show no ₹ for volumes until confirmed


class Prices(BaseModel):
    region: str
    currency_rate_inr_per_usd: float
    hours_per_month: int
    instances: dict[str, InstancePrice]
    downsize: dict[str, str]     # instance type -> one step down
    volumes: VolumePrices

    @model_validator(mode='after')
    def _downsize_types_are_priced(self) -> 'Prices':
        unknown = {t for pair in self.downsize.items() for t in pair} - self.instances.keys()
        if unknown:
            raise ValueError(f'downsize mentions unpriced instance types: {sorted(unknown)}')
        return self


class Carbon(BaseModel):
    method: str
    min_watts_per_vcpu: float
    max_watts_per_vcpu: float
    memory_watts_per_gb: float
    pue: float
    grid_kg_per_kwh: float
    embodied_included: bool


class Health(BaseModel):
    namespace: str
    metric: str
    threshold_ms: float          # above this the change is rolled back
    healthy_ms: float            # simulated reading when the change is fine
    unhealthy_ms: float          # simulated reading when sim state says the change breaks the app


class OfficeHours(BaseModel):
    days: list[str]
    start: str                   # 'HH:MM'
    end: str
    hours_per_week: float


class Policy(BaseModel):
    client: str
    name: str
    auto_actions: list[ActionType]   # action types that may ever run without approval
    auto_envs: list[Env]
    auto_limit_inr_month: float      # above this, needs approval
    min_confidence_auto: float
    protected_envs: list[Env]        # any change here is BLOCKED
    allowed_actions: list[ActionType]
    never: list[ActionType]
    office_hours: OfficeHours


def _read_yaml(path: str | Path) -> Any:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    with p.open(encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_prices(path: str | Path = 'config/prices.yaml') -> Prices:
    return Prices.model_validate(_read_yaml(path))


def load_carbon(path: str | Path = 'config/carbon.yaml') -> Carbon:
    return Carbon.model_validate(_read_yaml(path))


def load_health(path: str | Path = 'config/health.yaml') -> Health:
    return Health.model_validate(_read_yaml(path))


def load_policy(client: str) -> Policy:
    policy = Policy.model_validate(_read_yaml(f'policies/{client}.yaml'))
    if policy.client != client:
        raise ValueError(f'policies/{client}.yaml declares client {policy.client!r}')
    return policy


def estate_path(client: str) -> Path:
    return ROOT / 'data' / f"estate_{client.removeprefix('client_')}.yaml"


def list_clients() -> list[str]:
    """Client ids that have both a policy and an estate file."""
    return sorted(p.stem for p in (ROOT / 'policies').glob('*.yaml') if estate_path(p.stem).exists())


def load_estate(client: str) -> tuple[list[Resource], dict[str, Metrics], dict[str, dict]]:
    """Return resources, metrics by resource id, and sim settings by resource id.

    Only resources that have metrics (VMs) or sim settings appear in those dicts.
    `sim` is split off here so it never reaches a Resource, its tags, or the LLM.
    """
    path = estate_path(client)
    return parse_estate(_read_yaml(path), client, path.name)


def parse_estate(raw: dict, client: str, source: str = 'estate') -> tuple[list[Resource], dict[str, Metrics], dict[str, dict]]:
    if raw['client'] != client:
        raise ValueError(f'{source} declares client {raw["client"]!r}')
    resources: list[Resource] = []
    metrics: dict[str, Metrics] = {}
    sims: dict[str, dict] = {}
    for entry in raw['resources']:
        entry = dict(entry)
        m, sim = entry.pop('metrics', None), entry.pop('sim', None)
        r = Resource.model_validate({**entry, 'client': client})
        if any(x.id == r.id for x in resources):
            raise ValueError(f'{source}: duplicate resource id {r.id!r}')
        resources.append(r)
        if m is not None:
            metrics[r.id] = Metrics.model_validate(m)
        if sim is not None:
            sims[r.id] = sim
    return resources, metrics, sims
