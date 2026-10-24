"""Deterministic money and carbon maths. The LLM never computes these.

Method: Cloud Carbon Footprint (CCF) power model × CEA grid factor; AWS Mumbai on-demand prices.
"""

from typing import Any

from carbonops.config import Carbon, Prices
from carbonops.models import Action, Decision, ExecResult, Finding, Metrics, Resource

HOURS_PER_WEEK = 168


def watts(instance_type: str, u: float, prices: Prices, carbon: Carbon) -> float:
    """Average power draw of one instance at CPU utilisation u (0-1), including data-centre PUE."""
    spec = prices.instances[instance_type]
    per_vcpu = carbon.min_watts_per_vcpu + u * (carbon.max_watts_per_vcpu - carbon.min_watts_per_vcpu)
    return (spec.vcpu * per_vcpu + spec.mem_gb * carbon.memory_watts_per_gb) * carbon.pue


def kg(instance_type: str, u: float, hours: float, prices: Prices, carbon: Carbon) -> float:
    """kg CO₂e emitted by running the instance for `hours` at utilisation u."""
    return watts(instance_type, u, prices, carbon) * hours / 1000 * carbon.grid_kg_per_kwh


def price_action(
    action: Action,
    resource: Resource,
    metrics: Metrics | None,
    prices: Prices,
    carbon: Carbon,
) -> Action:
    """Return a copy of `action` with `inr_month` and `kgco2e_month` filled.

    u = metrics.cpu_avg / 100; fx = prices.currency_rate_inr_per_usd; 730 = prices.hours_per_month.
    STOP:     inr = usd_hour * 730 * fx; kg = kg(type, u, 730).
    SCHEDULE: hours_saved = 730 * (hours_per_week - params['hours_per_week']) / 168;
              inr = usd_hour * hours_saved * fx; kg = kg(type, u, hours_saved).
    RESIZE:   inr = (usd_before - usd_after) * 730 * fx; u_after = min(1, u * vcpu_before / vcpu_after);
              kg = kg(before, u, 730) - kg(after, u_after, 730).
    SNAPSHOT, KEEP, others (and volumes, whose gp3 price is unconfirmed): 0, 0.
    """
    inr = kgco2e = 0.0
    if resource.kind == 'vm' and metrics is not None and action.type in ('STOP', 'SCHEDULE', 'RESIZE'):
        itype = resource.instance_type
        if itype not in prices.instances:
            raise ValueError(f'no price for instance type {itype!r} ({resource.id})')
        fx, month = prices.currency_rate_inr_per_usd, prices.hours_per_month
        usd_hour = prices.instances[itype].usd_hour
        u = metrics.cpu_avg / 100

        if action.type == 'STOP':
            inr = usd_hour * month * fx
            kgco2e = kg(itype, u, month, prices, carbon)
        elif action.type == 'SCHEDULE':
            target = float(action.params.get('hours_per_week', metrics.hours_per_week))
            hours_saved = max(0.0, month * (metrics.hours_per_week - target) / HOURS_PER_WEEK)
            inr = usd_hour * hours_saved * fx
            kgco2e = kg(itype, u, hours_saved, prices, carbon)
        else:  # RESIZE
            to = action.params.get('to')
            if to not in prices.instances:
                raise ValueError(f'RESIZE target {to!r} has no price ({resource.id})')
            before, after = prices.instances[itype], prices.instances[to]
            u_after = min(1.0, u * before.vcpu / after.vcpu)
            inr = (before.usd_hour - after.usd_hour) * month * fx
            kgco2e = kg(itype, u, month, prices, carbon) - kg(to, u_after, month, prices, carbon)

    return action.model_copy(update={'inr_month': round(inr, 2), 'kgco2e_month': round(kgco2e, 3)})


def totals(
    resources: list[Resource],
    findings: list[Finding],
    actions: list[Action],
    decisions: list[Decision],
    results: list[ExecResult],
    approvals: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Summarise a run for the KPI tiles, the Savings tab and the Reporter.

    Keys:
      auto, approve, block      int   - decisions per tier (tag decisions included)
      no_finding                int   - resources with no finding
      done, rolled_back, failed, skipped, undone int - results per status
      inr_month, inr_year, kgco2e_month, kgco2e_year  float - realised: actions whose result is DONE
      pending_inr_month, pending_inr_year, pending_kgco2e_month, pending_kgco2e_year
                                float - APPROVE actions still awaiting a human verdict
      blocked                   list[dict] - {'action_id', 'reasons'} for every BLOCK
      needs_human               list[str]  - action ids that needed a human approver (tier APPROVE)
    """
    approvals = approvals or {}
    by_id = {a.id: a for a in actions}
    tiers = [d.tier for d in decisions]
    statuses = [r.status for r in results]
    done = [by_id[r.action_id] for r in results if r.status == 'DONE' and r.action_id in by_id]
    pending = [by_id[d.action_id] for d in decisions
               if d.tier == 'APPROVE' and d.action_id not in approvals and d.action_id in by_id
               and d.action_id not in {r.action_id for r in results}]
    with_finding = {f.resource_id for f in findings}

    inr = sum(a.inr_month for a in done)
    kgco2e = sum(a.kgco2e_month for a in done)
    p_inr = sum(a.inr_month for a in pending)
    p_kg = sum(a.kgco2e_month for a in pending)
    return {
        'auto': tiers.count('AUTO'), 'approve': tiers.count('APPROVE'), 'block': tiers.count('BLOCK'),
        'no_finding': sum(r.id not in with_finding for r in resources),
        'done': statuses.count('DONE'), 'rolled_back': statuses.count('ROLLED_BACK'),
        'failed': statuses.count('FAILED'), 'skipped': statuses.count('SKIPPED'), 'undone': statuses.count('UNDONE'),
        'inr_month': round(inr, 2), 'inr_year': round(inr * 12, 2),
        'kgco2e_month': round(kgco2e, 3), 'kgco2e_year': round(kgco2e * 12, 2),
        'pending_inr_month': round(p_inr, 2), 'pending_inr_year': round(p_inr * 12, 2),
        'pending_kgco2e_month': round(p_kg, 3), 'pending_kgco2e_year': round(p_kg * 12, 2),
        'blocked': [{'action_id': d.action_id, 'reasons': d.reasons} for d in decisions if d.tier == 'BLOCK'],
        'needs_human': [d.action_id for d in decisions if d.tier == 'APPROVE'],
    }
