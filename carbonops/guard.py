"""Deterministic policy guard: decides AUTO / APPROVE / BLOCK.

This module must stay pure and deterministic: it may import models and config only,
never the LLM client or any agent module (a test checks the source).
"""

from carbonops.config import Policy, load_policy
from carbonops.models import Action, ActionType, Decision, Resource

# Instruction words in tag values. Tags are untrusted data, never instructions.
REQUESTED_ACTIONS: dict[str, ActionType] = {'terminate': 'TERMINATE', 'delete': 'DELETE'}


def scan_tags(resource: Resource) -> list[ActionType]:
    """Return the action types that the resource's tag values ask for (case-insensitive, no duplicates)."""
    found: list[ActionType] = []
    for value in resource.tags.values():
        text = value.lower()
        for word, action_type in REQUESTED_ACTIONS.items():
            if word in text and action_type not in found:
                found.append(action_type)
    return found


def is_suspicious(resource: Resource) -> bool:
    """True if any tag value contains an instruction: 'terminate', 'delete', or 'ignore' + 'polic'."""
    for value in resource.tags.values():
        text = value.lower()
        if any(word in text for word in REQUESTED_ACTIONS) or ('ignore' in text and 'polic' in text):
            return True
    return False


def evaluate(action: Action, resource: Resource, policy: Policy, suspicious: bool) -> Decision:
    """Pure function. First matching rule sets the tier:

    1. type in policy.never or not in policy.allowed_actions             -> BLOCK
    2. resource.env in policy.protected_envs and type != KEEP            -> BLOCK
    3. type == KEEP                                                       -> AUTO (no-op)
    4. suspicious                                                         -> APPROVE (instructions found in tags)
    5. type in auto_actions and env in auto_envs
       and inr_month <= auto_limit_inr_month
       and confidence >= min_confidence_auto                              -> AUTO
    6. otherwise                                                          -> APPROVE
    `reasons` lists every reason that applies, in plain English.
    """
    t, env, name = action.type, resource.env, policy.name
    tag_note = 'Instructions found in resource tags; tags are data, not commands.'

    def decide(tier: str, reasons: list[str]) -> Decision:
        if suspicious and tag_note not in reasons:
            reasons.append(tag_note)
        return Decision(action_id=action.id, tier=tier, reasons=reasons)

    if t in policy.never:
        return decide('BLOCK', [f'{t} is never allowed for {name}.'])
    if t not in policy.allowed_actions:
        return decide('BLOCK', [f'{t} is not an allowed action for {name}.'])
    if env in policy.protected_envs and t != 'KEEP':
        return decide('BLOCK', [f'{env} is a protected environment: no automated changes.'])
    if t == 'KEEP':
        return Decision(action_id=action.id, tier='AUTO', reasons=['KEEP changes nothing.'])

    checks = [
        (t in policy.auto_actions,
         f'{t} is an auto action for {name}.', f'{t} is not an auto action for {name}.'),
        (env in policy.auto_envs,
         f'{env} is an auto environment.', f'{env} is not an auto environment for {name}.'),
        (action.inr_month <= policy.auto_limit_inr_month,
         f'₹{action.inr_month:,.0f}/month is within the ₹{policy.auto_limit_inr_month:,.0f} auto limit.',
         f'₹{action.inr_month:,.0f}/month is above the ₹{policy.auto_limit_inr_month:,.0f} auto limit.'),
        (action.confidence >= policy.min_confidence_auto,
         f'Confidence {action.confidence:.2f} meets the {policy.min_confidence_auto:.2f} threshold.',
         f'Confidence {action.confidence:.2f} is below the {policy.min_confidence_auto:.2f} threshold.'),
    ]
    failed = [no for ok, _, no in checks if not ok]
    if suspicious:
        return decide('APPROVE', [tag_note, *failed])
    if not failed:
        return decide('AUTO', [yes for _, yes, _ in checks])
    return decide('APPROVE', failed)


def tag_decisions(resource: Resource, policy: Policy | None = None) -> list[tuple[Action, Decision]]:
    """For each action type a tag asks for, build an Action (proposed_by='tag', confidence=0) and its
    Decision via evaluate(..., suspicious=True). These come out BLOCK: "injection blocked" in the demo.
    """
    policy = policy or load_policy(resource.client)
    pairs = []
    for action_type in scan_tags(resource):
        quoted = next((f'{k}: "{v}"' for k, v in resource.tags.items() if action_type.lower() in v.lower()), '')
        action = Action(id=f'{resource.id}:{action_type}:tag', resource_id=resource.id, type=action_type,
                        reason=f'Requested by text in a resource tag ({quoted}).', confidence=0, proposed_by='tag')
        pairs.append((action, evaluate(action, resource, policy, suspicious=True)))
    return pairs
