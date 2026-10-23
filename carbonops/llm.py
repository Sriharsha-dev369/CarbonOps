"""Thin OpenAI-compatible JSON chat client with record/replay, so the demo can run offline.

Env (read on every call, so the app's Demo-mode toggle takes effect immediately):
  LLM_BASE_URL  default Groq https://api.groq.com/openai/v1
  LLM_API_KEY   required unless REPLAY_MODE=play
  LLM_MODEL     default llama-3.3-70b-versatile
  REPLAY_MODE   off (default) | record (call API, save result) | play (load saved result, no API call)
"""

import json
import logging
import os
import re

import openai
from dotenv import load_dotenv

from carbonops.config import ROOT

load_dotenv(ROOT / '.env')
log = logging.getLogger(__name__)

REPLAY_DIR = ROOT / 'replay'
PROMPT_WARN_CHARS = 6000   # ~1,500 tokens; Groq free tier allows 12,000 tokens/minute
_json_mode_supported = True


def chat_json(system: str, user: str, *, schema_hint: str, replay_key: str) -> dict:
    """Ask the model for one JSON object and return it parsed.

    On a parse failure, retry once; then raise ValueError.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', replay_key):
        raise ValueError(f'replay_key must be a safe file name, got {replay_key!r}')
    replay_file = REPLAY_DIR / f'{replay_key}.json'
    mode = os.environ.get('REPLAY_MODE', 'off')

    if mode == 'play':
        if not replay_file.exists():
            raise FileNotFoundError(f'REPLAY_MODE=play but {replay_file} was never recorded')
        return json.loads(replay_file.read_text(encoding='utf-8'))

    system = f'{system}\n\nRespond with a single JSON object only, matching: {schema_hint}'
    if len(system) + len(user) > PROMPT_WARN_CHARS:
        log.warning('LLM prompt is %d chars (> %d); may hit Groq free-tier rate limits',
                    len(system) + len(user), PROMPT_WARN_CHARS)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _parse(_complete(messages))
            break
        except (ValueError, openai.BadRequestError) as e:  # JSONDecodeError is a ValueError
            # Groq answers 400 json_validate_failed when the model emits invalid JSON in JSON mode.
            last_error = e
            log.warning('LLM returned unusable JSON (attempt %d/2): %s', attempt, e)
    else:
        raise ValueError(f'LLM did not return valid JSON for {replay_key!r}') from last_error

    if mode == 'record':
        REPLAY_DIR.mkdir(exist_ok=True)
        replay_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


def _complete(messages: list[dict]) -> str:
    global _json_mode_supported
    api_key = os.environ.get('LLM_API_KEY')
    if not api_key:
        raise RuntimeError('LLM_API_KEY is not set (see .env.example)')
    client = openai.OpenAI(
        base_url=os.environ.get('LLM_BASE_URL', 'https://api.groq.com/openai/v1'),
        api_key=api_key,
    )
    kwargs = {
        'model': os.environ.get('LLM_MODEL', 'llama-3.3-70b-versatile'),
        'messages': messages,
        'temperature': 0,
    }
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
