"""Offline tests only; fake answers stay inside pytest temporary directories."""
import json
from pathlib import Path

import pytest
from tools import generate_my_cases as gen


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(gen.time, 'sleep', lambda _: None)
    raw = (Path(__file__).resolve().parents[1] /
           'lectures/04/implementation_run/my_cases.before_generation.jsonl').read_bytes()
    cases, backup, cp = [tmp_path / n for n in ('cases.jsonl', 'backup.jsonl', 'cp.json')]
    cases.write_bytes(raw)
    backup.write_bytes(raw)
    return cases, backup, cp, {'mode': 'offline-fake'}


def fake(q):
    return 'OFFLINE TEST ANSWER', {'model': 'fake'}


def test_actual_pydantic_result_usage_property_and_tools():
    from app.agent import agent
    from pydantic_ai.models.test import TestModel
    result = agent.run_sync('LoRA', model=TestModel(call_tools=['search_glossary']))
    metadata = gen.result_metadata(result, 'offline-test')
    assert metadata['model_requests'] == result.usage.requests > 0
    assert metadata['input_tokens'] == result.usage.input_tokens
    assert metadata['output_tokens'] == result.usage.output_tokens
    assert metadata['tools'] == ['search_glossary']


def test_default_never_calls_api_or_writes(setup):
    assert gen.run(*setup)['answers'] == {}
    assert not setup[2].exists()


def test_checkpoint_resume_skip_and_final_only_after_ten(setup):
    state = gen.run(*setup, fake, limit=3)
    assert len(state['answers']) == 3
    assert setup[0].read_bytes() == setup[1].read_bytes()
    seen = []
    def remaining(q):
        seen.append(q)
        return fake(q)
    gen.run(*setup, remaining, limit=7)
    assert len(seen) == 7
    rows = [json.loads(s) for s in setup[0].read_text(encoding='utf-8').splitlines()]
    assert all(r['answer'] == 'OFFLINE TEST ANSWER' for r in rows)
    assert gen.run(*setup)['status'] == 'complete'
    gen.run(*setup, lambda q: pytest.fail('Completed answer regenerated'))


@pytest.mark.parametrize('error', [KeyboardInterrupt(), RuntimeError('interrupted')])
def test_interruption_preserves_successes(setup, error):
    count = 0
    def interrupted(q):
        nonlocal count
        count += 1
        if count == 3:
            raise error
        return fake(q)
    with pytest.raises(type(error)):
        gen.run(*setup, interrupted, limit=10)
    assert len(gen.run(*setup)['answers']) == 2
    with pytest.raises(ValueError, match='ack-stop'):
        gen.run(*setup, fake)
    assert len(gen.run(*setup, fake, ack_stop=True)['answers']) == 3


def test_429_stops_without_retry_and_resumes_explicitly(setup):
    class RateLimited(Exception):
        status_code = 429
    calls = []
    def limited(q):
        calls.append(q)
        if len(calls) == 2:
            raise RateLimited('secret must not persist')
        return fake(q)
    with pytest.raises(RateLimited):
        gen.run(*setup, limited, limit=10)
    assert len(calls) == 2
    assert setup[0].read_bytes() == setup[1].read_bytes()
    saved = setup[2].read_text(encoding='utf-8')
    assert 'secret' not in saved and '429' in saved
    assert len(gen.run(*setup, fake, limit=9, ack_stop=True)['answers']) == 10


def test_reject_modified_input_model_backup(setup):
    gen.run(*setup, fake)
    with pytest.raises(ValueError, match='mismatch'):
        gen.run(*setup[:3], {'mode': 'different'}, fake)
    setup[0].write_bytes(setup[0].read_bytes() + b'\n')
    with pytest.raises(ValueError, match='changed'):
        gen.run(*setup, fake)


def test_atomic_replace_failure_keeps_previous_checkpoint(setup, monkeypatch):
    gen.run(*setup, fake)
    previous = setup[2].read_bytes()
    def denied(*args):
        raise PermissionError('simulated replace failure')
    monkeypatch.setattr(gen.os, 'replace', denied)
    with pytest.raises(PermissionError):
        gen.run(*setup, lambda q: pytest.fail('Must not call before pending saved'))
    assert setup[2].read_bytes() == previous
    assert not list(setup[2].parent.glob('*.tmp'))


def test_lock_and_limits_fail_closed(setup):
    with pytest.raises(ValueError):
        gen.run(*setup, fake, limit=11)
    setup[2].with_suffix('.json.lock').write_text('crashed process')
    with pytest.raises(FileExistsError):
        gen.run(*setup, fake)
