"""Day1 append-only, process-shared request reservations. Fail closed on ambiguity."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'lectures/01/experiments/04_analysis/day1_api_budget_v1'
POLICY = {'version': 1, 'total': 15, 'compare': 12, 'chat': 3}


class BudgetStopped(RuntimeError):
    pass


def write_new(path, record):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def initialize(root):
    """Explicit one-time setup only; never automatically reset a missing ledger."""
    root.mkdir(parents=True, exist_ok=False)
    write_new(root / 'policy.json', POLICY)
    with (root / 'guard.lock').open('xb') as stream:
        stream.write(b'1')
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def locked(root):
    # Keep an OS lock through reservation, send, and outcome persistence.
    # Nonblocking: a concurrent process stops instead of waiting/retrying.
    try:
        stream = (root / 'guard.lock').open('r+b')
    except OSError:
        raise BudgetStopped('予算台帳が未準備です。送信しません。') from None
    try:
        if os.fstat(stream.fileno()).st_size != 1:
            raise BudgetStopped('予算台帳のロックが不正です。送信しません。')
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BudgetStopped('別プロセスが実行中です。送信しません。') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise BudgetStopped('予算台帳または事前確認が不足・破損しています。送信しません。') from None


def _state(root):
    if _read(root / 'policy.json') != POLICY:
        raise BudgetStopped('予算台帳の方針が不一致です。送信しません。')
    if (root / 'STOP_429.json').exists():
        raise BudgetStopped('429による実験全体の停止が保存されています。再実行しません。')
    counts = {'compare': 0, 'chat': 0}
    reservations = sorted(root.glob('reservation_*.json'))
    for number, path in enumerate(reservations, 1):
        item = _read(path)
        if (path.name != f'reservation_{number:03}.json' or not isinstance(item, dict)
                or item.get('number') != number or item.get('phase') not in counts):
            raise BudgetStopped('予算予約の連番または内容が不正です。送信しません。')
        counts[item['phase']] += 1
        outcome = _read(root / f'outcome_{number:03}.json')
        if (not isinstance(outcome, dict) or outcome.get('number') != number
                or type(outcome.get('success')) is not bool
                or outcome.get('error_code') == 429):
            raise BudgetStopped('未完了または停止すべき予約があります。送信しません。')
    if (len(list(root.glob('outcome_*.json'))) != len(reservations)
            or len(reservations) > POLICY['total']
            or any(counts[p] > POLICY[p] for p in counts)):
        raise BudgetStopped('予算台帳が不整合です。送信しません。')
    return counts


def check(root, phase=None):
    counts = _state(root)
    preflight = _read(root / 'preflight_confirmed.json')
    if (not isinstance(preflight, dict) or any(preflight.get(k) is not True for k in
            ('free_tier', 'model_access_confirmed', 'quotas_confirmed'))):
        raise BudgetStopped('Free Tier・モデル・残枠の確認が未完了です。送信しません。')
    if phase is not None and (phase not in counts or sum(counts.values()) >= 15 or counts[phase] >= POLICY[phase]):
        raise BudgetStopped('共有API予算の上限です。送信しません。')
    return counts


def ensure_ready():
    with locked(ROOT):
        check(ROOT)


def execute(phase, operation):
    """Reserve before send; never refund, including crashes or interrupted requests."""
    with locked(ROOT):
        counts = check(ROOT, phase)
        number = sum(counts.values()) + 1
        write_new(ROOT / f'reservation_{number:03}.json', {
            'number': number, 'phase': phase,
            'reserved_at': datetime.now(timezone.utc).isoformat(),
        })
        # An exception/crash leaves the reservation pending and blocks all future sends.
        result = operation()
        outcome = {'number': number, 'success': result.success, 'error_code': result.error_code}
        if result.error_code == 429:
            write_new(ROOT / 'STOP_429.json', outcome)
        write_new(ROOT / f'outcome_{number:03}.json', outcome)
        return result
