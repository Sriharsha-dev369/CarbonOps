"""Rule-based waste detection and the rule fallback for choosing an action."""

from carbonops.config import Policy, Prices
from carbonops.models import Action, Finding, Metrics, Resource

IDLE_CPU_AVG = 5
IDLE_CPU_MAX = 20
ALWAYS_ON_HOURS = 160
OVERSIZED_CPU_P95 = 30
FREE_TEXT_TAGS = ('purpose', 'note')


def detect(resources: list[Resource], metrics: dict[str, Metrics], policy: Policy) -> list[Finding]:
    """Return at most one Finding per resource; first matching rule wins.

    1. volume and not attached                          -> UNATTACHED
    2. vm, cpu_avg < 5 and cpu_max < 20                 -> IDLE
    3. vm, env in {dev, test} and hours_per_week >= 160 -> ALWAYS_ON
    4. vm, cpu_p95 < 30                                 -> OVERSIZED
    5. otherwise                                        -> no finding
    A VM that is not running costs no compute, so it gets no finding (e.g. stopped by an earlier run).
    `evidence` holds the metric values that triggered the rule.
    """
    findings = []
    for r in resources:
        if r.kind == 'volume':
            if not r.attached:
                findings.append(Finding(resource_id=r.id, kind='UNATTACHED',
                                        evidence={'attached': False, 'size_gb': r.size_gb}))
            continue
        m = metrics.get(r.id)
        if m is None or r.state != 'running':
            continue
        if m.cpu_avg < IDLE_CPU_AVG and m.cpu_max < IDLE_CPU_MAX:
            findings.append(Finding(resource_id=r.id, kind='IDLE',
                                    evidence={'cpu_avg': m.cpu_avg, 'cpu_max': m.cpu_max}))
        elif r.env in ('dev', 'test') and m.hours_per_week >= ALWAYS_ON_HOURS:
            findings.append(Finding(resource_id=r.id, kind='ALWAYS_ON',
                                    evidence={'hours_per_week': m.hours_per_week}))
        elif m.cpu_p95 < OVERSIZED_CPU_P95:
            findings.append(Finding(resource_id=r.id, kind='OVERSIZED',
                                    evidence={'cpu_p95': m.cpu_p95}))
    return findings


def default_action(finding: Finding, resource: Resource, prices: Prices, policy: Policy) -> Action:
    """Rule fallback used when the LLM is off or fails.

    IDLE -> STOP · ALWAYS_ON -> SCHEDULE (office hours) · OVERSIZED -> RESIZE one step down ·
    UNATTACHED -> SNAPSHOT. confidence 0.9, or 0.6 if a free-text `purpose`/`note` tag exists.
    """
    params: dict = {}
    if finding.kind == 'IDLE':
        type_, reason = 'STOP', f"Idle: average CPU {_ev(finding, 'cpu_avg')}%, max {_ev(finding, 'cpu_max')}%."
    elif finding.kind == 'ALWAYS_ON':
        type_ = 'SCHEDULE'
        params = {'hours_per_week': policy.office_hours.hours_per_week}
        reason = (f"Runs {_ev(finding, 'hours_per_week')} h/week in {resource.env}; "
                  f"office hours need {policy.office_hours.hours_per_week:g}.")
    elif finding.kind == 'OVERSIZED':
        to = prices.downsize.get(resource.instance_type)
        if to is None:
            # Already the smallest size we price: nothing safe to resize to.
            type_, reason = 'KEEP', f'Low p95 CPU, but {resource.instance_type} has no smaller size configured.'
        else:
            type_, params = 'RESIZE', {'to': to}
            reason = f"p95 CPU {_ev(finding, 'cpu_p95')}%: one size down ({resource.instance_type} → {to}) fits."
    else:  # UNATTACHED
        type_, reason = 'SNAPSHOT', f'Unattached {resource.size_gb} GB volume: snapshot it for safekeeping.'

    free_text = any(k in resource.tags for k in FREE_TEXT_TAGS)
    return Action(id=f'{resource.id}:{type_}', resource_id=resource.id, type=type_, params=params,
                  reason=reason, confidence=0.6 if free_text else 0.9, proposed_by='rules')


def _ev(finding: Finding, key: str) -> str:
    value = finding.evidence.get(key)
    return '?' if value is None else f'{value:g}' if isinstance(value, (int, float)) else str(value)
