"""Day1 tests: SDK-shaped responses, synthetic credentials, no real API access."""
import importlib
import io
import json
import sys
from unittest.mock import Mock

import pytest
from google.genai import errors, types
from app import ask
REAL_CLIENT = ask.genai.Client


def response(text='回答', usage=True, input_tokens=12, output_tokens=7):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role='model', parts=[types.Part.from_text(text=text)]))] if text else [],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=input_tokens, candidates_token_count=output_tokens
        ) if usage else None,
    )


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch, tmp_path):
    from app import experiment_budget as budget
    root = tmp_path / 'offline-budget'
    budget.initialize(root)
    budget.write_new(root / 'preflight_confirmed.json', {
        'free_tier': True, 'model_access_confirmed': True, 'quotas_confirmed': True,
    })
    monkeypatch.setattr(budget, 'ROOT', root)
    client = Mock()
    client.models.generate_content.return_value = response()
    monkeypatch.setattr(ask.genai, 'Client', Mock(return_value=client))
    # Never call the real accessor or read any actual key environment variable.
    monkeypatch.setattr(ask, 'get_api_key', lambda: 'synthetic-test-credential')
    monkeypatch.setattr(ask, 'FREE_TIER_MODELS', {*ask.FREE_TIER_MODELS, 'model-from-env', 'a', 'b', 'test', 'model'})
    return client


def cli(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['app.ask', *args])
    ask.main()


@pytest.mark.parametrize('question', [None, 'LoRAとは？'])
def test_original_cli(monkeypatch, fake_sdk, capsys, question):
    cli(monkeypatch, *([] if question is None else [question]))
    fake_sdk.models.generate_content.assert_called_once_with(model=ask.MODEL, contents=question or ask.DEFAULT_QUESTION)
    output = capsys.readouterr().out
    assert '回答' in output and '[model]' in output and '[質問]' in output
    fake_sdk.close.assert_called_once()


def test_ask_returns_string_and_tokens(fake_sdk, capsys):
    assert ask.ask('質問') == '回答'
    assert '入力: 12 / 出力: 7' in capsys.readouterr().out


@pytest.mark.parametrize('usage,values,expected', [
    (False, (12, 7), '入力: 取得不可 / 出力: 取得不可'),
    (True, (None, None), '入力: 取得不可 / 出力: 取得不可'),
    (True, (0, 0), '入力: 0 / 出力: 0'),
])
def test_usage_missing_and_zero(fake_sdk, capsys, usage, values, expected):
    fake_sdk.models.generate_content.return_value = response(usage=usage, input_tokens=values[0], output_tokens=values[1])
    ask.ask('質問')
    assert expected in capsys.readouterr().out


def test_history_passes_complete_pairs(fake_sdk):
    history = []
    ask.measure(fake_sdk, '最初', history=history)
    ask.measure(fake_sdk, '続き', history=history)
    contents = fake_sdk.models.generate_content.call_args.kwargs['contents']
    assert [x.role for x in contents] == ['user', 'model', 'user']
    assert [x.parts[0].text for x in contents] == ['最初', '回答', '続き']
    assert len(history) == 4
    assert len(contents) == 3  # SDK input list was not mutated after the call.


@pytest.mark.parametrize('failure', [errors.APIError(429, {'message': 'synthetic-private-detail'}), RuntimeError('synthetic-private-detail')])
def test_failure_preserves_history(fake_sdk, failure):
    history = []
    ask.measure(fake_sdk, '成功', history=history)
    before = history.copy()
    fake_sdk.models.generate_content.side_effect = failure
    result = ask.measure(fake_sdk, '失敗', history=history)
    assert not result.success and history == before
    assert 'synthetic-private-detail' not in repr(result)
    if isinstance(failure, errors.APIError) and failure.code == 429:
        with pytest.raises(ask.experiment_budget.BudgetStopped):
            ask.measure(fake_sdk, '再開禁止', history=history)
        assert history == before
        return
    fake_sdk.models.generate_content.side_effect = None
    ask.measure(fake_sdk, '再開', history=history)
    assert [x.parts[0].text for x in fake_sdk.models.generate_content.call_args.kwargs['contents']] == ['成功', '回答', '再開']


def test_empty_response_does_not_commit(fake_sdk):
    fake_sdk.models.generate_content.return_value = response(text=None)
    history = []
    result = ask.measure(fake_sdk, '質問', history=history)
    assert not result.success and result.error_type == 'empty_response' and history == []


@pytest.mark.parametrize('ending', ['exit', EOFError(), KeyboardInterrupt()])
def test_chat_exit(monkeypatch, fake_sdk, ending):
    def end(_):
        if isinstance(ending, BaseException):
            raise ending
        return ending
    monkeypatch.setattr('builtins.input', end)
    cli(monkeypatch, '--chat')
    fake_sdk.models.generate_content.assert_not_called()
    fake_sdk.close.assert_called_once()


def test_chat_initial_question_and_blank_input(monkeypatch, fake_sdk):
    values = iter(['', '続き', 'exit'])
    monkeypatch.setattr('builtins.input', lambda _: next(values))
    cli(monkeypatch, '最初', '--chat')
    assert fake_sdk.models.generate_content.call_count == 2
    assert len(fake_sdk.models.generate_content.call_args.kwargs['contents']) == 3


def test_elapsed_success_and_failure(monkeypatch, fake_sdk):
    times = iter([10.0, 10.25, 20.0, 20.75])
    monkeypatch.setattr(ask.time, 'perf_counter', lambda: next(times))
    assert ask.measure(fake_sdk, '成功').elapsed_seconds == 0.25
    fake_sdk.models.generate_content.side_effect = RuntimeError('private')
    assert ask.measure(fake_sdk, '失敗').elapsed_seconds == 0.75


def test_model_env_at_import(monkeypatch):
    from app import config
    original = config.MODEL
    monkeypatch.setenv('AIE_MODEL', 'model-from-env')
    try:
        importlib.reload(config)
        assert config.MODEL == 'model-from-env'
    finally:
        config.MODEL = original


def test_compare_defaults_three_trials_and_env_model(monkeypatch, fake_sdk, tmp_path):
    monkeypatch.setattr(ask, 'MODEL', 'model-from-env')
    path = tmp_path / 'comparison.jsonl'
    cli(monkeypatch, '質問', '--compare', '--output', str(path))
    rows = [json.loads(line) for line in path.read_text('utf8').splitlines()]
    assert len(rows) == 3
    assert [r['trial'] for r in rows] == [1, 2, 3]
    assert all(r['model'] == 'model-from-env' and r['answer'] == '回答' for r in rows)
    assert all(isinstance(r['elapsed_seconds'], float) and r['success'] for r in rows)


def test_multiple_models_questions_independent(fake_sdk):
    stream = io.StringIO()
    assert ask.compare(fake_sdk, ['a', 'b'], ['x', 'y'], 3, stream)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(rows) == 12
    for model in ['a', 'b']:
        for question in ['x', 'y']:
            assert [r['trial'] for r in rows if r['model'] == model and r['question'] == question] == [1, 2, 3]
    assert all(isinstance(call.kwargs['contents'], str) for call in fake_sdk.models.generate_content.call_args_list)


def test_errors_recorded_without_private_details(monkeypatch, fake_sdk, tmp_path, capsys):
    private = 'synthetic-private-detail'
    fake_sdk.models.generate_content.side_effect = [
        errors.APIError(503, {'message': private}),
        errors.APIError(403, {'message': private}),
        RuntimeError(private),
    ]
    path = tmp_path / 'failures.jsonl'
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '--compare', '--output', str(path))
    assert error.value.code == 1
    log = path.read_text('utf8')
    rows = [json.loads(line) for line in log.splitlines()]
    assert [r['error_code'] for r in rows] == [503, 403, None]
    assert all(not r['success'] for r in rows)
    output = capsys.readouterr()
    assert '503' in output.out
    assert private not in log + output.out + output.err
    assert 'synthetic-test-credential' not in log + output.out + output.err


def test_redacts_question_answer_credentials(fake_sdk, capsys):
    key = 'AIza' + 'Z' * 35  # Synthetic format, not an actual key.
    fake_sdk.models.generate_content.return_value = response(f'GEMINI_API_KEY={key} password=synthetic-password')
    stream = io.StringIO()
    ask.compare(fake_sdk, ['test'], [f'Authorization: Bearer {key}'], 1, stream)
    text = stream.getvalue() + capsys.readouterr().out
    assert key not in text and 'synthetic-password' not in text
    assert '[REDACTED]' in text


def test_existing_log_protected(monkeypatch, fake_sdk, tmp_path):
    path = tmp_path / 'existing.jsonl'
    path.write_text('keep', encoding='utf8')
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '--compare', '--output', str(path))
    assert error.value.code == 1 and path.read_text('utf8') == 'keep'
    ask.genai.Client.assert_not_called()


@pytest.mark.parametrize('args', [
    ['--compare'], ['--chat', '--compare'], ['--trials', '0'], ['--models', 'model'],
])
def test_invalid_cli_before_client(monkeypatch, args):
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, *args)
    assert error.value.code == 2
    ask.genai.Client.assert_not_called()


def test_single_api_failure_exit_and_safe_message(monkeypatch, fake_sdk, capsys):
    fake_sdk.models.generate_content.side_effect = errors.APIError(403, {'message': 'do-not-print'})
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '質問')
    assert error.value.code == 1
    assert 'do-not-print' not in capsys.readouterr().out


def test_client_initialization_error_safe(monkeypatch, capsys):
    ask.genai.Client.side_effect = RuntimeError('do-not-print')
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '質問')
    assert error.value.code == 1
    output = capsys.readouterr()
    assert 'do-not-print' not in output.out + output.err


def test_comparison_stops_after_429(fake_sdk):
    fake_sdk.models.generate_content.side_effect = [
        response('first'), errors.APIError(429, {'message': 'private'}), response('third')
    ]
    stream = io.StringIO()
    assert not ask.compare(fake_sdk, ['model'], ['question'], 3, stream)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row['success'] for row in rows] == [True, False]
    assert [row['answer'] for row in rows] == ['first', None]
    assert fake_sdk.models.generate_content.call_count == 2


def test_three_turns_refer_to_first_turn(fake_sdk):
    history = []
    for question in ['名前は空です', '色は青です', '最初の名前と色は？']:
        assert ask.measure(fake_sdk, question, history=history).success
    contents = fake_sdk.models.generate_content.call_args.kwargs['contents']
    assert [part.role for part in contents] == ['user', 'model', 'user', 'model', 'user']
    assert contents[0].parts[0].text == '名前は空です'
    assert contents[2].parts[0].text == '色は青です'
    assert len(history) == 6


def test_chat_stops_on_429(fake_sdk):
    fake_sdk.models.generate_content.side_effect = errors.APIError(429, {'message': 'private'})
    ask.chat(fake_sdk, 'question')
    assert fake_sdk.models.generate_content.call_count == 1


def test_chat_request_budget(monkeypatch, fake_sdk):
    monkeypatch.setattr('builtins.input', lambda _: 'question')
    ask.chat(fake_sdk, max_requests=3)
    assert fake_sdk.models.generate_content.call_count == 3


def test_comparison_budget_rejected_before_client(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '--compare', '--trials', '16', '--output', str(tmp_path / 'new.jsonl'))
    assert error.value.code == 2
    ask.genai.Client.assert_not_called()
    assert not (tmp_path / 'new.jsonl').exists()


def test_unverified_model_rejected_before_client(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '--compare', '--models', 'paid-or-unknown', '--output', str(tmp_path / 'new.jsonl'))
    assert error.value.code == 2
    ask.genai.Client.assert_not_called()


def test_sdk_retry_disabled(fake_sdk):
    ask.ask('question')
    options = ask.genai.Client.call_args.kwargs['http_options']
    assert options.retry_options.attempts == 1


def test_fixed_record_schema_timestamp_question_id(fake_sdk):
    from datetime import datetime
    stream = io.StringIO()
    assert ask.compare(fake_sdk, ['a'], ['same'], 2, stream)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert set(rows[0]) == {'model', 'question', 'trial', 'elapsed_seconds', 'input_tokens',
                          'output_tokens', 'success', 'answer', 'error_type', 'error_code',
                          'question_id', 'started_at'}
    assert rows[0]['question_id'] == rows[1]['question_id']
    assert datetime.fromisoformat(rows[0]['started_at']).utcoffset().total_seconds() == 0


@pytest.mark.parametrize('status', [429, 503])
def test_real_sdk_no_retry_with_mock_http(monkeypatch, status):
    import httpx
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(status, json={'error': {'code': status, 'message': 'synthetic'}})

    # Use the production client factory, replacing only HTTP transport and key.
    def local_client(**kwargs):
        kwargs['http_options'].httpx_client = httpx.Client(transport=httpx.MockTransport(handle))
        return REAL_CLIENT(**kwargs)

    monkeypatch.setattr(ask.genai, 'Client', local_client)
    client = ask.create_client(['gemini-3.5-flash-lite'])
    try:
        result = ask.measure(client, 'question')
        assert not result.success and result.error_code == status
        assert calls == ['POST']
    finally:
        client.close()


def test_chat_log_three_turns(monkeypatch, fake_sdk, tmp_path):
    values = iter(['first', 'second', 'refer to first', 'exit'])
    monkeypatch.setattr('builtins.input', lambda _: next(values))
    path = tmp_path / 'chat.jsonl'
    cli(monkeypatch, '--chat', '--output', str(path), '--max-requests', '3')
    rows = [json.loads(line) for line in path.read_text('utf-8').splitlines()]
    assert [r['trial'] for r in rows] == [1, 2, 3]
    assert all(r['success'] and r['started_at'] and r['question_id'] for r in rows)


def test_chat_existing_log_protected(monkeypatch, tmp_path):
    path = tmp_path / 'chat.jsonl'
    path.write_text('keep', encoding='utf-8')
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '--chat', '--output', str(path))
    assert error.value.code == 1
    assert path.read_text('utf-8') == 'keep'
    ask.genai.Client.assert_not_called()


def test_chat_continues_after_api_failure(monkeypatch, fake_sdk):
    fake_sdk.models.generate_content.side_effect = [
        response('first answer'), errors.APIError(503, {'message': 'private'}), response('last answer')
    ]
    questions = iter(['first', 'failed', 'last', 'exit'])
    monkeypatch.setattr('builtins.input', lambda _: next(questions))
    cli(monkeypatch, '--chat')
    contents = fake_sdk.models.generate_content.call_args.kwargs['contents']
    assert [part.parts[0].text for part in contents] == ['first', 'first answer', 'last']


def test_ctrl_c_during_request_does_not_commit(monkeypatch, fake_sdk):
    fake_sdk.models.generate_content.side_effect = KeyboardInterrupt()
    ask.chat(fake_sdk, 'question')
    assert fake_sdk.models.generate_content.call_count == 1


def test_shared_twelve_plus_three_budget_and_sixteenth_rejected(fake_sdk):
    from app import experiment_budget as budget
    comparison, conversation = io.StringIO(), io.StringIO()
    assert ask.compare(fake_sdk, ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite'],
                       ['LoRAとは何ですか？', ask.DEFAULT_QUESTION], 3, comparison, 12)
    history = []
    for turn in range(1, 4):
        ask.write_record(conversation, ask.measure(fake_sdk, f'turn {turn}', trial=turn, history=history))
    assert len(list(budget.ROOT.glob('reservation_*.json'))) == 15
    assert budget._state(budget.ROOT) == {'compare': 12, 'chat': 3}
    with pytest.raises(budget.BudgetStopped):
        ask.measure(fake_sdk, 'sixteenth', history=history)
    assert fake_sdk.models.generate_content.call_count == 15
    rows = [json.loads(line) for line in (comparison.getvalue() + conversation.getvalue()).splitlines()]
    assert len(rows) == 15 and all(set(row) == set(rows[0]) for row in rows)
    assert all(row['success'] for row in rows)


def test_phase_limit_even_when_total_is_not_exhausted(fake_sdk):
    from app import experiment_budget as budget
    for _ in range(12):
        ask.measure(fake_sdk, 'compare')
    with pytest.raises(budget.BudgetStopped):
        ask.measure(fake_sdk, 'compare thirteen')
    assert fake_sdk.models.generate_content.call_count == 12
    assert ask.measure(fake_sdk, 'chat still available', history=[]).success


def test_cancelled_comparison_model_rejected(monkeypatch, fake_sdk, tmp_path):
    assert 'gemini-3.1-flash-lite' in ask.FREE_TIER_MODELS
    assert 'gemini-2.5-flash-lite' not in ask.FREE_TIER_MODELS
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, '--compare', '--models', 'gemini-2.5-flash-lite',
            '--output', str(tmp_path / 'old-model.jsonl'))
    assert error.value.code == 2
    ask.genai.Client.assert_not_called()


def test_reservation_is_persisted_before_send(fake_sdk):
    from app import experiment_budget as budget

    def generate(**kwargs):
        item = json.loads((budget.ROOT / 'reservation_001.json').read_text('utf-8'))
        assert item['phase'] == 'compare'
        assert not (budget.ROOT / 'outcome_001.json').exists()
        return response()

    fake_sdk.models.generate_content.side_effect = generate
    assert ask.measure(fake_sdk, 'question').success


def test_missing_preflight_blocks_send(fake_sdk):
    from app import experiment_budget as budget
    # A separate never-confirmed ledger; do not alter the fixture ledger.
    root = budget.ROOT.parent / 'unconfirmed'
    budget.initialize(root)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(budget, 'ROOT', root)
        with pytest.raises(budget.BudgetStopped):
            ask.measure(fake_sdk, 'question')
    fake_sdk.models.generate_content.assert_not_called()


def test_missing_preflight_before_key_access(monkeypatch, fake_sdk):
    from app import experiment_budget as budget
    root = budget.ROOT.parent / 'not-ready'
    budget.initialize(root)
    monkeypatch.setattr(budget, 'ROOT', root)
    key_accessor = Mock(side_effect=AssertionError('must not access credentials'))
    monkeypatch.setattr(ask, 'get_api_key', key_accessor)
    with pytest.raises(budget.BudgetStopped):
        ask.create_client(['gemini-3.5-flash-lite'])
    key_accessor.assert_not_called()
    ask.genai.Client.assert_not_called()


def test_corrupt_outcome_blocks_send(fake_sdk):
    from app import experiment_budget as budget
    budget.write_new(budget.ROOT / 'reservation_001.json', {'number': 1, 'phase': 'compare'})
    budget.write_new(budget.ROOT / 'outcome_001.json', {'number': 999, 'success': True})
    with pytest.raises(budget.BudgetStopped):
        ask.measure(fake_sdk, 'forbidden')
    fake_sdk.models.generate_content.assert_not_called()


def test_interruption_blocks_future_send(fake_sdk):
    from app import experiment_budget as budget
    fake_sdk.models.generate_content.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ask.measure(fake_sdk, 'interrupted')
    with pytest.raises(budget.BudgetStopped):
        ask.measure(fake_sdk, 'retry forbidden')
    assert fake_sdk.models.generate_content.call_count == 1


def test_429_persisted_before_jsonl_failure(fake_sdk):
    from app import experiment_budget as budget
    fake_sdk.models.generate_content.side_effect = errors.APIError(429, {'message': 'private'})

    class BrokenLog:
        def write(self, text):
            raise OSError('synthetic disk failure')

    with pytest.raises(OSError):
        ask.compare(fake_sdk, ['model'], ['question'], 3, BrokenLog())
    assert (budget.ROOT / 'STOP_429.json').exists()
    with pytest.raises(budget.BudgetStopped):
        ask.measure(fake_sdk, 'another phase', history=[])
    assert fake_sdk.models.generate_content.call_count == 1


def _budget_child(root, body):
    import subprocess
    # Only stdlib and the budget module; no SDK, credentials or external network.
    code = ("import sys; from pathlib import Path; from types import SimpleNamespace; "
            "from app import experiment_budget as b; b.ROOT=Path(sys.argv[1]);\n" + body)
    return subprocess.Popen([sys.executable, '-c', code, str(root)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_separate_process_cannot_send_sixteenth(fake_sdk):
    from app import experiment_budget as budget
    for _ in range(12):
        ask.measure(fake_sdk, 'compare')
    for _ in range(3):
        ask.measure(fake_sdk, 'chat', history=[])
    child = _budget_child(budget.ROOT,
        "try:\n b.execute('chat', lambda: print('SENT') or SimpleNamespace(success=True,error_code=None))\n"
        "except b.BudgetStopped:\n print('BLOCKED')\n")
    output, error = child.communicate(timeout=15)
    assert child.returncode == 0 and output.strip() == 'BLOCKED' and not error


def test_separate_process_cannot_restart_after_429(fake_sdk):
    from app import experiment_budget as budget
    fake_sdk.models.generate_content.side_effect = errors.APIError(429, {'message': 'private'})
    ask.measure(fake_sdk, 'question')
    child = _budget_child(budget.ROOT,
        "try:\n b.execute('chat', lambda: print('SENT') or SimpleNamespace(success=True,error_code=None))\n"
        "except b.BudgetStopped:\n print('BLOCKED')\n")
    output, error = child.communicate(timeout=15)
    assert child.returncode == 0 and output.strip() == 'BLOCKED' and not error


def test_parallel_process_and_crash_fail_closed(fake_sdk):
    from app import experiment_budget as budget
    holder = _budget_child(budget.ROOT,
        "def operation():\n print('RESERVED',flush=True)\n sys.stdin.readline()\n"
        " return SimpleNamespace(success=True,error_code=None)\n"
        "b.execute('compare',operation)\n")
    try:
        assert holder.stdout.readline().strip() == 'RESERVED'
        competitor = _budget_child(budget.ROOT,
            "try:\n b.execute('compare', lambda: print('SENT') or SimpleNamespace(success=True,error_code=None))\n"
            "except b.BudgetStopped:\n print('BLOCKED')\n")
        output, error = competitor.communicate(timeout=15)
        assert competitor.returncode == 0 and output.strip() == 'BLOCKED' and not error
        # Simulate process termination after reservation, before any outcome.
        holder.kill()
        holder.communicate(timeout=15)
        with pytest.raises(budget.BudgetStopped):
            ask.measure(fake_sdk, 'after crash')
        assert len(list(budget.ROOT.glob('reservation_*.json'))) == 1
        fake_sdk.models.generate_content.assert_not_called()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=15)
