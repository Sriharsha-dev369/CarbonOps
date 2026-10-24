"""Ask CarbonOps: retrieval picks the right events; the LLM may only quote given numbers."""

import pytest

from carbonops import ask, audit, graph, llm


@pytest.fixture
def finished(aws):
    out = graph.run('client_a', 'Engineer', 'ravi')
    out = graph.resume(out['thread_id'], {p['id']: 'approve' for p in out['pending']}, approver='asha', role='Approver')
    s = out['state']
    return s, audit.read_all(s['audit_path'])


def test_retrieval_is_about_the_named_resource(finished):
    s, events = finished
    picked = ask.retrieve('Why was sandbox-ml blocked?', ask.run_events(events), [r.id for r in s['resources']])
    assert 0 < len(picked) <= ask.MAX_EVENTS
    kinds = [(e.kind, e.payload.get('tier')) for _, e in picked]
    assert ('decision', 'BLOCK') in kinds
    assert any(e.payload.get('action_id') == 'sandbox-ml:TERMINATE:tag' for _, e in picked)


def test_rollback_question_finds_the_rollback(finished):
    s, events = finished
    picked = ask.retrieve('What was rolled back and why?', ask.run_events(events), [r.id for r in s['resources']])
    assert any(e.kind == 'rollback' and e.payload['resource_id'] == 'staging-api' for _, e in picked)


def test_without_llm_returns_events_only(finished):
    s, events = finished
    out = ask.answer('What did we save?', 'client_a', events, [r.id for r in s['resources']], s['results'],
                     s['totals'], use_llm=False)
    assert out['answer'] is None and out['events'] and out['source'] == 'events'


def test_llm_answer_with_invented_number_is_rejected(finished, monkeypatch):
    s, events = finished
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **k: {'answer': 'We saved ₹12,34,567 [e3].', 'cited': ['e3']})
    out = ask.answer('What did we save?', 'client_a', events, [r.id for r in s['resources']], s['results'],
                     s['totals'], use_llm=True)
    assert out['answer'] is None and 'unavailable' in out['note']


def test_llm_answer_with_given_numbers_is_kept(finished, monkeypatch):
    s, events = finished
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **k: {'answer': 'Realised ₹96,166/month.', 'cited': ['e999']})
    out = ask.answer('What did we save?', 'client_a', events, [r.id for r in s['resources']], s['results'],
                     s['totals'], use_llm=True)
    assert out['answer'] == 'Realised ₹96,166/month.' and out['cited'] == [] and out['source'] == 'llm'


def test_prompt_stays_small(finished):
    s, events = finished
    import json
    for q in ask.CHIPS:
        picked = ask.retrieve(q, ask.run_events(events), [r.id for r in s['resources']])
        payload = ask.build_payload(q, 'client_a', picked, s['results'], s['totals'])
        assert len(json.dumps(payload, ensure_ascii=False)) < 12_000, q     # ~3,000 tokens
