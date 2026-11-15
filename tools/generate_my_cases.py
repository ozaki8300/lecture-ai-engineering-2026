"""Day4-only checkpointed generation. Default is read-only; never auto-retry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def result_metadata(result, model_name):
    from pydantic_ai.messages import ToolCallPart
    usage = result.usage
    return {'model': model_name, 'model_requests': usage.requests,
            'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens,
            'tools': [part.tool_name for m in result.all_messages() for part in m.parts
                      if isinstance(part, ToolCallPart)]}


def validate(raw):
    rows = [json.loads(s) for s in raw.decode('utf-8').splitlines() if s.strip()]
    if len(rows) != 10 or [r['id'] for r in rows] != [f'{i:02}' for i in range(1, 11)]:
        raise ValueError('Expected exactly IDs 01..10')
    for r in rows:
        if set(r) != {'id', 'category', 'question', 'answer', 'reference'}:
            raise ValueError('Invalid case schema')
        if set(r['reference']) != {'glossary', 'expected'}:
            raise ValueError('Invalid reference schema')
        if not r['question'].strip() or not r['reference']['expected'].strip() or r['answer'] != '':
            raise ValueError('Backup must contain unanswered cases')
    return rows


def run(cases, backup, checkpoint, identity, generate=None, *, limit=1, ack_stop=False, wait=1):
    """A supplied generator is called once per independent question; no retry loop."""
    if not 1 <= limit <= 10 or wait < 1:
        raise ValueError('limit must be 1..10 and wait >= 1 second')
    original = backup.read_bytes()
    rows = validate(original)
    binding = {'input_sha256': digest(original), 'identity': identity}
    state = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {
        **binding, 'answers': {}, 'events': [], 'status': 'ready',
    }
    if any(state.get(k) != v for k, v in binding.items()):
        raise ValueError('Checkpoint input/model/code/glossary mismatch')
    answers = state['answers']
    if not set(answers) <= {r['id'] for r in rows} or any(
        not isinstance(a, str) or not a.strip() for a in answers.values()
    ):
        raise ValueError('Invalid checkpoint answers')
    completed = [{**r, 'answer': answers.get(r['id'], '')} for r in rows]
    expected_final = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in completed).encode('utf-8')
    current = cases.read_bytes()
    if current != original and not (len(answers) == 10 and current == expected_final):
        raise ValueError('Evaluation set changed since backup')
    if generate is None:
        return state
    lock = checkpoint.with_suffix(checkpoint.suffix + '.lock')
    # Fail closed after a crash: a stale lock requires manual investigation.
    with lock.open('x', encoding='utf-8') as stream:
        stream.write(str(os.getpid()))
    try:
        # Recheck after acquiring lock; never overwrite a newer writer's state.
        if checkpoint.exists() and json.loads(checkpoint.read_text(encoding='utf-8')) != state:
            raise ValueError('Checkpoint changed concurrently')
        if state['status'] in ('stopped', 'pending') and not ack_stop:
            raise ValueError('Previous stop/interruption requires explicit --ack-stop after review')
        for row in [r for r in rows if r['id'] not in answers][:limit]:
            state['status'] = 'pending'
            state['events'].append({'id': row['id'], 'event': 'started'})
            save(checkpoint, state)
            try:
                answer, metadata = generate(row['question'])
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError('Agent did not return a nonempty text answer')
            except BaseException as error:
                state['status'] = 'stopped'
                # Do not persist raw exception text, request headers, bodies or keys.
                state['events'].append({'id': row['id'], 'event': 'stopped',
                                        'error_type': type(error).__name__,
                                        'status_code': getattr(error, 'status_code', None)})
                save(checkpoint, state)
                raise
            answers[row['id']] = answer
            state['status'] = 'ready'
            state['events'].append({'id': row['id'], 'event': 'completed', 'metadata': metadata})
            save(checkpoint, state)
            time.sleep(wait)
        if len(answers) == 10:
            if cases.read_bytes() not in (original, expected_final) or backup.read_bytes() != original:
                raise ValueError('Input/backup changed during generation')
            final = [{**r, 'answer': answers[r['id']]} for r in rows]
            atomic_write(cases, ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in final).encode('utf-8'))
            state['status'] = 'complete'
            save(checkpoint, state)
        return state
    finally:
        lock.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true', help='Explicitly enable real Gemini requests')
    p.add_argument('--max-cases', type=int, default=1)
    p.add_argument('--wait', type=float, default=1)
    p.add_argument('--ack-stop', action='store_true', help='Acknowledge reviewed quota/error/interruption before resuming')
    args = p.parse_args()
    cases = ROOT / 'data/eval/my_cases.jsonl'
    backup = ROOT / 'lectures/04/implementation_run/my_cases.before_generation.jsonl'
    checkpoint = ROOT / 'lectures/04/implementation_run/resumable_checkpoint.json'
    from app.config import MODEL
    identity = {'mode': 'real-gemini', 'model': MODEL, 'temperature': 'agent default (unset)',
                'files': {s: digest((ROOT / s).read_bytes()) for s in
                          ('app/agent.py', 'app/config.py', 'app/tools.py', 'app/experiment_budget.py',
                           'data/glossary.json', 'uv.lock', 'tools/generate_my_cases.py')}}
    state = run(cases, backup, checkpoint, identity)
    if not args.execute:
        print(f"READ ONLY: {len(state['answers'])}/10 saved; no API calls. Use --execute only after quota review.")
        return
    from app.agent import agent, build_model
    model = build_model()

    def generate(question):
        # Same agent/model as official generator, no history from another case.
        result = agent.run_sync(question, model=model)
        return result.output, result_metadata(result, MODEL)

    try:
        state = run(cases, backup, checkpoint, identity, generate, limit=args.max_cases,
                    ack_stop=args.ack_stop, wait=args.wait)
        print(f"Saved {len(state['answers'])}/10; status={state['status']}")
    except Exception as error:
        print(f"STOPPED: {type(error).__name__}; status={getattr(error, 'status_code', None)}. No automatic retry.")
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
