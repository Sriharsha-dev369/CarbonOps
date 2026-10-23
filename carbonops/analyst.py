"""Analyst agent: the LLM picks one action per finding; rules take over on any failure. STUB: written on the day."""

from typing import Literal

from pydantic import BaseModel, Field

from carbonops.config import ROOT, Policy, Prices
from carbonops.models import Action, Finding, Resource

SCHEMA_HINT = (
    '{"actions": [{"resource_id": str, "type": "STOP"|"SCHEDULE"|"RESIZE"|"SNAPSHOT"|"KEEP", '
    '"params": object, "reason": str, "confidence": number 0-1}]}'
)


class ProposedAction(BaseModel):
    resource_id: str
    type: Literal['STOP', 'SCHEDULE', 'RESIZE', 'SNAPSHOT', 'KEEP']
    params: dict = {}
    reason: str
    confidence: float = Field(ge=0, le=1)


class AnalystOutput(BaseModel):
    actions: list[ProposedAction]


def system_prompt() -> str:
    return (ROOT / 'prompts' / 'analyst.md').read_text(encoding='utf-8')


def propose(
    findings: list[Finding],
    resources: dict[str, Resource],
    policy: Policy,
    prices: Prices,
) -> list[Action]:
    """Return one Action per finding.

    1. Build the user message: JSON with `allowed_actions` (policy.allowed_actions minus policy.never)
       and `findings`, each with resource metadata (id, env, instance_type as `type`, owner, tags)
       and evidence. Never include sim settings.
    2. llm.chat_json(system_prompt(), user, schema_hint=SCHEMA_HINT, replay_key=f'analyst_{policy.client}').
    3. Validate with AnalystOutput. Keep only entries whose resource_id has a finding and whose type
       is allowed; turn each into an Action (proposed_by='llm', id f'{resource_id}:{type}').
    4. On any failure (API error, bad JSON, validation error), and for any finding the LLM skipped,
       fall back to detector.default_action.
    """
    raise NotImplementedError
