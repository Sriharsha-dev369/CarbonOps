"""Deterministic money and carbon maths. The LLM never computes these. STUB: written on the day."""

from typing import Any

from carbonops.config import Carbon, Prices
from carbonops.models import Action, Decision, ExecResult, Finding, Metrics, Resource


def price_action(
    action: Action,
    resource: Resource,
    metrics: Metrics | None,
    prices: Prices,
    carbon: Carbon,
) -> Action:
    """Return a copy of `action` with `inr_month` and `kgco2e_month` filled.

    u = metrics.cpu_avg / 100; fx = prices.currency_rate_inr_per_usd; 730 = prices.hours_per_month.
    watts(type, u)    = (vcpu * (min_w + u * (max_w - min_w)) + mem_gb * mem_w) * pue
    kg(type, u, hrs)  = watts(type, u) * hrs / 1000 * grid_kg_per_kwh

    STOP:     hours = 730. inr = usd_hour * 730 * fx. kg = kg(type, u, 730).
    SCHEDULE: hours_saved = 730 * (metrics.hours_per_week - params['hours_per_week']) / 168.
              inr = usd_hour * hours_saved * fx. kg = kg(type, u, hours_saved).
    RESIZE:   inr = (usd_before - usd_after) * 730 * fx.
              u_after = min(1, u * vcpu_before / vcpu_after).
              kg = kg(before, u, 730) - kg(after, u_after, 730).
    SNAPSHOT, KEEP, others: 0, 0.

    Volumes have no metrics (None) and get no ₹: the gp3 price is UNCONFIRMED.
    Must not mutate the input action (use model_copy).
    """
    raise NotImplementedError


def totals(
    resources: list[Resource],
    findings: list[Finding],
    actions: list[Action],
    decisions: list[Decision],
    results: list[ExecResult],
) -> dict[str, Any]:
    """Summarise a run for the Savings tab and the Reporter.

    Keys:
      auto, approve, block      int   - decisions per tier (tag decisions included)
      no_finding                int   - resources with no finding
      done, rolled_back, failed int   - results per status
      inr_month, inr_year       float - savings from actions whose result is DONE (year = month * 12)
      kgco2e_month, kgco2e_year float - same, for carbon
      blocked                   list[dict] - {'action_id', 'reasons'} for every BLOCK
      needs_human               list[str]  - action ids that needed a human approver (tier APPROVE)
    Client A demo with everything approved: 3 AUTO, 5 APPROVE (one rolls back), 2 BLOCK, 1 no finding.
    """
    raise NotImplementedError
