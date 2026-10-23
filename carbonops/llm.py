"""Thin OpenAI-compatible JSON chat client with record/replay, so the demo can run offline.

Env (read on every call, so the app's toggles take effect immediately):
  LLM_BASE_URL  default Groq https://api.groq.com/openai/v1
  LLM_API_KEY   required unless REPLAY_MODE=play
  LLM_MODEL     default openai/gpt-oss-120b
  REPLAY_MODE   off (default) | record (call API, save result) | play (load saved result, no API call)

A replay file stores a fingerprint of the prompt it answered; playing it for a different prompt raises
ReplayMissing, so a recording made for other numbers is never shown as if it were about these ones.
"""

import hashlib
import json
import logging
import os
import re
import time

import openai
from dotenv import load_dotenv

from carbonops.config import ROOT

load_dotenv(ROOT / '.env')
log = logging.getLogger(__name__)

REPLAY_DIR = ROOT / 'replay'
DEFAULT_MODEL = 'openai/gpt-oss-120b'
PROMPT_WARN_CHARS = 6000   # ~1,500 tokens; Groq's free tier allows ~8,000 tokens/minute for gpt-oss-120b
MAX_RATE_LIMIT_WAIT = 8    # seconds: wait once for a short rate-limit window, otherwise fail fast to rules
_json_mode_supported = True


class ReplayMissing(RuntimeError):
    """REPLAY_MODE=play but there is no recording for this exact prompt."""


def chat_json(system: str, user: str, *, schema_hint: str, replay_key: str, timeout: float = 20) -> dict:
    """Ask the model for one JSON object and return it parsed.

    On a parse failure, retry once; then raise ValueError. API errors and timeouts raise as they are.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', replay_key):
        raise ValueError(f'replay_key must be a safe file name, got {replay_key!r}')
    replay_file = REPLAY_DIR / f'{replay_key}.json'
    mode = os.environ.get('REPLAY_MODE', 'off')
    system = f'{system}\n\nRespond with a single JSON object only, matching: {schema_hint}'
    fingerprint = hashlib.sha256(f'{system}\n\x00\n{user}'.encode()).hexdigest()

    if mode == 'play':
        if not replay_file.exists():
            raise ReplayMissing(f'no recording {replay_file.name}')
        saved = json.loads(replay_file.read_text(encoding='utf-8'))
        if saved.get('input_sha256') != fingerprint:
            raise ReplayMissing(f'{replay_file.name} was recorded for a different prompt')
        return saved['response']

    if len(system) + len(user) > PROMPT_WARN_CHARS:
        log.warning('LLM prompt is %d chars (> %d); may hit Groq free-tier rate limits',
                    len(system) + len(user), PROMPT_WARN_CHARS)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _parse(_complete(messages, timeout))
            break
        except (ValueError, openai.BadRequestError) as e:  # JSONDecodeError is a ValueError
            # Groq answers 400 json_validate_failed when the model emits invalid JSON in JSON mode.
            last_error = e
            log.warning('LLM returned unusable JSON (attempt %d/2): %s', attempt, e)
    else:
        raise ValueError(f'LLM did not return valid JSON for {replay_key!r}') from last_error

    if mode == 'record':
        REPLAY_DIR.mkdir(exist_ok=True)
        replay_file.write_text(json.dumps({'replay_key': replay_key, 'input_sha256': fingerprint, 'response': result},
                                          indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return result


def _complete(messages: list[dict], timeout: float) -> str:
    api_key = os.environ.get('LLM_API_KEY')
    if not api_key:
        raise RuntimeError('LLM_API_KEY is not set (see .env.example)')
    client = openai.OpenAI(
        base_url=os.environ.get('LLM_BASE_URL', 'https://api.groq.com/openai/v1'),
        api_key=api_key, timeout=timeout, max_retries=0,
    )
    model = os.environ.get('LLM_MODEL') or DEFAULT_MODEL
    kwargs = {'model': model, 'messages': messages, 'temperature': 0}
    if 'gpt-oss' in model:
        kwargs['reasoning_effort'] = 'low'   # fewer hidden reasoning tokens: faster, kinder to rate limits
    try:
        return _create(client, kwargs)
    except openai.RateLimitError as e:
        wait = _retry_after(e)
        if wait is None or wait > MAX_RATE_LIMIT_WAIT:
            raise
        log.warning('LLM rate limited; retrying once in %.1fs', wait)
        time.sleep(wait)
        return _create(client, kwargs)


def _retry_after(e: openai.RateLimitError) -> float | None:
    header = e.response.headers.get('retry-after') if e.response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            return None
    found = re.search(r'try again in ([\d.]+)s', str(e))
    return float(found.group(1)) if found else None


def _create(client: openai.OpenAI, kwargs: dict) -> str:
    global _json_mode_supported
    if _json_mode_supported:
        try:
            resp = client.chat.completions.create(**kwargs, response_format={'type': 'json_object'})
            return resp.choices[0].message.content or ''
        except openai.BadRequestError as e:
            if 'response_format' not in str(e):
                raise
            log.warning('Model does not support response_format=json_object; asking in the prompt only')
            _json_mode_supported = False
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ''


def _parse(text: str) -> dict:
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f'expected a JSON object, got {type(data).__name__}')
    return data
