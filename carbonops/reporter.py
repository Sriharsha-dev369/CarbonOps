"""Reporter agent: a short client summary from computed numbers only.

`summarize` asks the LLM and checks the answer: at most 150 words, and every number in it must be
one we gave it. Anything else falls back to `template_report`, the deterministic version.
"""

import json
import logging
import re
from typing import Any

from pydantic import BaseModel

from carbonops import config, llm
from carbonops.fmt import inr
from carbonops.models import ExecResult

log = logging.getLogger(__name__)

SCHEMA_HINT = '{"summary": str (at most 150 words)}'
MAX_WORDS = 150


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


def build_payload(client: str, results: list[ExecResult], totals: dict[str, Any]) -> dict:
    t = totals
    return {
        'client': config.load_policy(client).name,
        'realised': {'inr_month': inr(t['inr_month']), 'inr_year': inr(t['inr_year']),
                     'kgco2e_year': round(t['kgco2e_year'], 1), 'completed_actions': t['done']},
        'pending_approval': {'inr_month': inr(t['pending_inr_month']), 'kgco2e_year': round(t['pending_kgco2e_year'], 1)},
        'counts': {'auto': t['auto'], 'needed_approval': t['approve'], 'blocked': t['block'],
                   'no_action_needed': t['no_finding'], 'rolled_back': t['rolled_back'], 'failed': t['failed']},
        'results': [{'action': _readable(r.action_id), 'status': r.status,
                     'detail': re.sub(r'\s*Restored from \S+?\.?$', '', r.detail)} for r in results],
        'blocked': [{'action': _readable(b['action_id']), 'why': b['reasons']} for b in t['blocked']],
        'needed_a_human': [_readable(a) for a in t['needs_human']],
    }


def _numbers(text: str) -> list[float]:
    return [float(n.replace(',', '')) for n in re.findall(r'\d[\d,]*(?:\.\d+)?', text)]


def numbers_are_given(summary: str, payload: dict) -> bool:
    """Every number in the summary appears in the payload (allowing for rounding)."""
    given = _numbers(json.dumps(payload, ensure_ascii=False)) + [2.0, 12.0, 150.0]   # CO2, months, word limit
    for n in _numbers(summary):
        if not any(abs(n - g) <= max(0.51, 0.005 * abs(g)) for g in given):
            return False
    return True


def summarize(client: str, results: list[ExecResult], totals: dict[str, Any], *,
              notes: list[str] | None = None) -> str:
    """LLM summary of at most 150 words using only the given numbers; template on any failure."""
    notes = notes if notes is not None else []
    payload = build_payload(client, results, totals)
    try:
        reply = llm.chat_json(system_prompt(), json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                              schema_hint=SCHEMA_HINT, replay_key=f'reporter_{client}', timeout=20)
        summary = ReporterOutput.model_validate(reply).summary.strip()
        if not summary or len(summary.split()) > MAX_WORDS:
            raise ValueError(f'summary has {len(summary.split())} words (max {MAX_WORDS})')
        if not numbers_are_given(summary, payload):
            raise ValueError('summary contains a number that was not given')
    except Exception as e:
        log.warning('reporter LLM failed: %s', e)
        notes.append(f'Report: template used ({type(e).__name__}: {e})')
        return template_report(client, results, totals)
    notes.append('Report written by the LLM from computed numbers (checked).')
    return summary
