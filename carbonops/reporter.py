"""Reporter: a short client summary from computed numbers only.

`template_report` is the deterministic version; the LLM version (`summarize`) arrives in phase D
and falls back to the template on any failure.
"""

from typing import Any

from pydantic import BaseModel

from carbonops import config
from carbonops.fmt import inr
from carbonops.models import ExecResult

SCHEMA_HINT = '{"summary": str (at most 150 words)}'


class ReporterOutput(BaseModel):
    summary: str


def system_prompt() -> str:
    return (config.ROOT / 'prompts' / 'reporter.md').read_text(encoding='utf-8')


def _readable(action_id: str) -> str:
    return action_id.removesuffix(':tag').replace(':', ' ')


def template_report(client: str, results: list[ExecResult], totals: dict[str, Any]) -> str:
    """Fixed-wording summary (no LLM) built only from `totals` and `results`."""
    name = config.load_policy(client).name
    t = totals
    lines = [
        f"**{name}**: CarbonOps reviewed the estate and realised savings of **{inr(t['inr_month'])}/month** "
        f"({inr(t['inr_year'])}/year) and **{t['kgco2e_year']:,.1f} kg CO₂e/year** from {t['done']} completed actions.",
        f"{t['auto']} actions ran automatically, {t['approve']} needed a human approver, {t['block']} were blocked, "
        f"and {t['no_finding']} resource(s) needed no action.",
    ]
    if t.get('pending_inr_month'):
        lines.append(f"Awaiting approval: {inr(t['pending_inr_month'])}/month more.")
    rolled = [r for r in results if r.status == 'ROLLED_BACK']
    if rolled:
        lines.append('Rolled back safely: ' + '; '.join(f'{_readable(r.action_id)} ({r.detail})' for r in rolled))
    if t['blocked']:
        lines.append('Blocked: ' + '; '.join(f"{_readable(b['action_id'])} ({b['reasons'][0]})" for b in t['blocked']))
    return '\n\n'.join(lines)
