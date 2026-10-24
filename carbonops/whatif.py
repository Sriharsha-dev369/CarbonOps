"""What-if policy simulator: replay the guard over a finished run's priced actions with another policy.

Only guard.evaluate runs here: no AWS, no LLM, nothing is executed. Protected environments and `never`
actions are not adjustable, so prod changes, delete and terminate stay blocked whatever the sliders say.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from carbonops import audit, config, guard
from carbonops.models import Action, ActionType, AuditEvent, Decision, Env, Resource, Role


def adjust(policy: config.Policy, *, auto_limit_inr_month: float, min_confidence_auto: float,
           auto_actions: list[ActionType], auto_envs: list[Env]) -> config.Policy:
    """The policy with only the four simulator knobs changed (validated like a loaded policy)."""
    return config.Policy.model_validate({**policy.model_dump(), 'auto_limit_inr_month': auto_limit_inr_month,
                                         'min_confidence_auto': min_confidence_auto, 'auto_actions': auto_actions,
                                         'auto_envs': auto_envs})


def simulate(actions: list[Action], resources: dict[str, Resource], policy: config.Policy) -> list[Decision]:
    """The guard's decision for every action under `policy`, exactly as the pipeline computes it."""
    return [guard.evaluate(a, resources[a.resource_id], policy, guard.is_suspicious(resources[a.resource_id]))
            for a in actions]


def always_blocked(action: Action, resource: Resource, policy: config.Policy) -> bool:
    """True when no simulator setting can unblock the action (never / not allowed / protected env)."""
    return (action.type in policy.never or action.type not in policy.allowed_actions
            or (resource.env in policy.protected_envs and action.type != 'KEEP'))


def summary(actions: list[Action], decisions: list[Decision]) -> dict[str, Any]:
    tiers = {d.action_id: d.tier for d in decisions}
    return {
        'auto': sum(t == 'AUTO' for t in tiers.values()),
        'approve': sum(t == 'APPROVE' for t in tiers.values()),
        'block': sum(t == 'BLOCK' for t in tiers.values()),
        'auto_inr_month': round(sum(a.inr_month for a in actions if tiers[a.id] == 'AUTO'), 2),
        'human_inr_month': round(sum(a.inr_month for a in actions if tiers[a.id] == 'APPROVE'), 2),
    }


def save(policy: config.Policy, actor: str, role: Role, *, root: Path = config.ROOT,
         audit_path: str | Path | None = None) -> Path:
    """Admin only: write policies/<client>_custom.yaml. The active policy is not changed."""
    if role != 'Admin':
        raise ValueError('Only an Admin can save a policy.')
    path = root / 'policies' / f'{policy.client}_custom.yaml'
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f'# What-if policy for {policy.name}, saved by {actor} on '
              f'{datetime.now().strftime("%d %b %Y %H:%M")}. Not active.\n')
    path.write_text(header + yaml.safe_dump(policy.model_dump(), sort_keys=False, allow_unicode=True), encoding='utf-8')
    audit.append(AuditEvent(ts=datetime.now(timezone.utc).isoformat(timespec='seconds'), actor=actor, role=role,
                            kind='policy_saved', payload={'path': str(path.relative_to(root)),
                                                          'policy': policy.model_dump()}),
                 audit_path or config.audit_path(policy.client))
    return path
