"""Reporter agent: the LLM writes a short client summary from computed numbers only. STUB: written on the day."""

from typing import Any

from pydantic import BaseModel

from carbonops.config import ROOT, Policy
from carbonops.models import ExecResult

SCHEMA_HINT = '{"summary": str (at most 150 words)}'

TEMPLATE = (
    '{client}: CarbonOps found savings of ₹{inr_month:,.0f}/month (₹{inr_year:,.0f}/year) '
    'and {kgco2e_year:,.1f} kg CO₂e/year. {auto} actions ran automatically, {approve} needed approval '
    'and {block} were blocked; {no_finding} resource(s) had no finding. '
    'Blocked: {blocked}. Needed a human approver: {needs_human}.'
)


class ReporterOutput(BaseModel):
    summary: str


def system_prompt() -> str:
    return (ROOT / 'prompts' / 'reporter.md').read_text(encoding='utf-8')


def summarise(policy: Policy, totals: dict[str, Any], results: list[ExecResult]) -> str:
    """Return a client summary of at most 150 words.

    1. Build the user message: JSON with client (policy.name), totals, results,
       blocked (totals['blocked']) and needs_human (totals['needs_human']).
    2. llm.chat_json(system_prompt(), user, schema_hint=SCHEMA_HINT, replay_key=f'reporter_{policy.client}').
    3. Validate with ReporterOutput; reject a summary over 150 words.
    4. On any failure, fall back to TEMPLATE filled from `totals` (no LLM).
    """
    raise NotImplementedError
