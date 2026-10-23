"""Rule-based waste detection and the rule fallback for choosing an action. STUB: written on the day."""

from carbonops.config import Policy, Prices
from carbonops.models import Action, Finding, Metrics, Resource


def detect(resources: list[Resource], metrics: dict[str, Metrics], policy: Policy) -> list[Finding]:
    """Return at most one Finding per resource; first matching rule wins.

    1. volume and not attached                          -> UNATTACHED
    2. vm, cpu_avg < 5 and cpu_max < 20                 -> IDLE
    3. vm, env in {dev, test} and hours_per_week >= 160 -> ALWAYS_ON
    4. vm, cpu_p95 < 30                                 -> OVERSIZED
    5. otherwise                                        -> no finding

    `evidence` holds the metric values that triggered the rule
    (e.g. {'cpu_avg': 2, 'cpu_max': 9} for IDLE; {'attached': False, 'size_gb': 200} for UNATTACHED).
    """
    raise NotImplementedError


def default_action(finding: Finding, resource: Resource, prices: Prices, policy: Policy) -> Action:
    """Rule fallback used when the LLM is off or fails.

    IDLE       -> STOP
    ALWAYS_ON  -> SCHEDULE, params={'hours_per_week': policy.office_hours.hours_per_week}
    OVERSIZED  -> RESIZE,   params={'to': prices.downsize[resource.instance_type]}
    UNATTACHED -> SNAPSHOT

    confidence = 0.9, but 0.6 if the resource has a free-text `purpose` or `note` tag.
    proposed_by = 'rules'. Leave inr_month / kgco2e_month at 0 (calc.py fills them).
    Action id convention: f'{resource.id}:{type}'.
    """
    raise NotImplementedError
