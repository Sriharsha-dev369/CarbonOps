"""Deterministic policy guard: decides AUTO / APPROVE / BLOCK. STUB: written on the day.

This module must stay pure and deterministic: it may import models and config only,
never the LLM client or any agent module (a test checks the source).
"""

from carbonops.config import Policy
from carbonops.models import Action, ActionType, Decision, Resource


def scan_tags(resource: Resource) -> list[ActionType]:
    """Return the action types that the resource's tag values ask for.

    Case-insensitive scan of every tag value for instruction words:
      'terminate' -> TERMINATE
      'delete'    -> DELETE
      'ignore' (+ 'polic') marks the resource suspicious but requests no action type.
    No duplicates. e.g. sandbox-ml -> ['TERMINATE']; dev-api-01 -> [].
    """
    raise NotImplementedError


def is_suspicious(resource: Resource) -> bool:
    """True if any instruction word ('terminate', 'delete', 'ignore' + 'polic') appears in any tag value."""
    raise NotImplementedError


def evaluate(action: Action, resource: Resource, policy: Policy, suspicious: bool) -> Decision:
    """Pure function. First matching rule sets the tier:

    1. type in policy.never or not in policy.allowed_actions             -> BLOCK
    2. resource.env in policy.protected_envs and type != KEEP            -> BLOCK
    3. type == KEEP                                                       -> AUTO (no-op)
    4. suspicious                                                         -> APPROVE (reason: instructions found in tags)
    5. type in auto_actions and env in auto_envs
       and inr_month <= auto_limit_inr_month
       and confidence >= min_confidence_auto                              -> AUTO
    6. otherwise                                                          -> APPROVE

    `reasons` lists every reason that applies, in plain English
    (e.g. 'RESIZE is not an auto action for Acme Retail', 'confidence 0.50 is below 0.70').
    """
    raise NotImplementedError


def tag_decisions(resource: Resource, policy: Policy | None = None) -> list[tuple[Action, Decision]]:
    """For each action type from scan_tags, build an Action and its Decision.

    Action: proposed_by='tag', confidence=0, reason quotes the tag, id f'{resource.id}:{type}:tag'.
    Decision: evaluate(action, resource, policy, suspicious=True) -> these come out BLOCK.
    `policy` defaults to config.load_policy(resource.client).
    Used to show "injection blocked" in the demo.
    """
    raise NotImplementedError
