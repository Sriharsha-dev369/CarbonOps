"""Core data types shared by every CarbonOps module."""

from typing import Literal

from pydantic import BaseModel

Env = Literal['prod', 'staging', 'test', 'dev']
ActionType = Literal['STOP', 'SCHEDULE', 'RESIZE', 'SNAPSHOT', 'KEEP', 'DELETE', 'TERMINATE']
FindingKind = Literal['IDLE', 'ALWAYS_ON', 'OVERSIZED', 'UNATTACHED']
Tier = Literal['AUTO', 'APPROVE', 'BLOCK']
Role = Literal['Viewer', 'Engineer', 'Approver', 'Admin']
ExecStatus = Literal['DONE', 'ROLLED_BACK', 'FAILED', 'SKIPPED']


class Resource(BaseModel):
    id: str                      # e.g. 'dev-api-01'
    client: str                  # 'client_a' | 'client_b'
    kind: Literal['vm', 'volume']
    instance_type: str | None = None
    size_gb: int | None = None   # volumes only
    env: Env
    owner: str = ''
    tags: dict[str, str] = {}
    state: str = 'running'
    attached: bool = True        # volumes only


class Metrics(BaseModel):
    cpu_avg: float               # percent, 0-100
    cpu_p95: float
    cpu_max: float
    hours_per_week: float        # 168 = always on


class Finding(BaseModel):
    resource_id: str
    kind: FindingKind
    evidence: dict


class Action(BaseModel):
    id: str
    resource_id: str
    type: ActionType
    params: dict = {}            # RESIZE: {'to': 'm5.xlarge'}; SCHEDULE: {'hours_per_week': 50}
    reason: str = ''
    confidence: float = 0.9      # 0-1
    proposed_by: Literal['rules', 'llm', 'tag'] = 'rules'
    inr_month: float = 0.0       # filled by calc.py only
    kgco2e_month: float = 0.0    # filled by calc.py only


class Decision(BaseModel):
    action_id: str
    tier: Tier
    reasons: list[str]


class ExecResult(BaseModel):
    action_id: str
    status: ExecStatus
    detail: str = ''


class HealthCheck(BaseModel):
    ok: bool
    latency_ms: float
    threshold_ms: float


class AuditEvent(BaseModel):
    ts: str                      # ISO 8601
    actor: str                   # 'system' | user name
    role: Role | Literal['system']
    kind: str                    # 'run' | 'finding' | 'analysis' | 'decision' | 'approval' | 'snapshot' | 'apply' |
                                 # 'health_check' | 'rollback' | 'done' | 'skipped' | 'failed' | 'report' | 'error'
    payload: dict
    prev_hash: str = ''
    hash: str = ''
