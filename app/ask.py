"""Day1: 単発質問、会話、モデル比較（実行時のみGeminiを呼ぶ）。"""
import argparse
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from google import genai
from google.genai import errors, types

from app.config import MODEL, api_error_message, get_api_key
from app import experiment_budget

DEFAULT_QUESTION = "AIE2026の講座の宿題にある「3レベル」とは何ですか？"
FREE_TIER_MODELS = {'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite'}
DEFAULT_MAX_REQUESTS = 15


def create_client(models: list[str]):
    # Public free-tier eligibility is not proof that this project is on Free Tier.
    if any(model not in FREE_TIER_MODELS for model in models):
        raise ValueError('無料枠対象として確認していないモデルです。')
    experiment_budget.ensure_ready()
    return genai.Client(api_key=get_api_key(), http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=1)))


@dataclass
class Measurement:
    model: str
    question: str
    trial: int
    elapsed_seconds: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    success: bool = False
    answer: str | None = None
    error_type: str | None = None
    error_code: int | None = None
    question_id: str = ''
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RequestFailed(Exception):
    def __init__(self, result: Measurement):
        self.result = result
        super().__init__(error_message(result))


def error_message(result: Measurement) -> str:
    # Never echo SDK exception details, request headers or credentials.
    if result.error_code == 429:
        return 'レート制限（429）を記録しました。実験全体を停止し、再実行を禁止します。'
    if result.error_code is not None:
        return api_error_message(result.error_code)
    if result.error_type == 'empty_response':
        return 'テキスト応答を取得できませんでした。'
    return 'API呼び出しに失敗しました（通信またはクライアントエラー）。'


def measure(client, question: str, *, model: str = MODEL, trial: int = 1,
            history: list | None = None) -> Measurement:
    if model not in FREE_TIER_MODELS or (history is not None and model != 'gemini-3.5-flash-lite'):
        raise experiment_budget.BudgetStopped('許可していない実験モデルです。送信しません。')
    return experiment_budget.execute('chat' if history is not None else 'compare',
        lambda: _measure_once(client, question, model=model, trial=trial, history=history))


def _measure_once(client, question: str, *, model: str, trial: int,
                  history: list | None) -> Measurement:
    result = Measurement(model, question, trial, 0.0)
    result.question_id = 'sha256:' + hashlib.sha256(question.encode('utf-8')).hexdigest()
    contents = question if history is None else [
        *history, types.Content(role='user', parts=[types.Part.from_text(text=question)])
    ]
    start = time.perf_counter()
    try:
        response = client.models.generate_content(model=model, contents=contents)
    except errors.APIError as error:
        result.error_type = 'api_error'
        result.error_code = error.code if isinstance(error.code, int) else None
        return result
    except Exception:
        result.error_type = 'client_error'
        return result
    finally:
        result.elapsed_seconds = time.perf_counter() - start
    usage = response.usage_metadata
    if usage is not None:
        result.input_tokens = usage.prompt_token_count
        result.output_tokens = usage.candidates_token_count
    result.answer = response.text
    if not result.answer:
        result.error_type = 'empty_response'
        return result
    result.success = True
    if history is not None:
        # Commit a complete user/model pair only after a successful response.
        history.extend([
            contents[-1],
            types.Content(role='model', parts=[types.Part.from_text(text=result.answer)])
        ])
    return result


def print_metrics(result: Measurement) -> None:
    def token(value):
        return '取得不可' if value is None else str(value)
    print(f'[tokens] 入力: {token(result.input_tokens)} / 出力: {token(result.output_tokens)}')
    print(f'[time] {result.elapsed_seconds:.6f} 秒')


def ask(question: str) -> str:
    """Keep the original string-in/string-out API and single-request behavior."""
    client = create_client([MODEL])
    try:
        result = measure(client, question)
        print_metrics(result)
        if not result.success:
            raise RequestFailed(result)
        return result.answer
    finally:
        client.close()


# Redact recognizable credential formats in experiment text; never read env keys.
_CREDENTIAL = re.compile(
    r'(?i)(\b(?:[a-z_]*api[_-]?key|password|passwd|access[_-]?token|'
    r'client[_-]?secret|authorization)\b[\s"\x27]*[:=][\s"\x27]*)'
    r'(?:Bearer\s+)?[^\s,;"\x27}\]]+'
)
_SECRET = re.compile(r'AIza[A-Za-z0-9_-]{35}|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bAKIA[A-Z0-9]{16}\b')


def redact(text: str) -> str:
    return _SECRET.sub('[REDACTED]', _CREDENTIAL.sub(r'\1[REDACTED]', text))


def write_record(stream, result: Measurement) -> None:
    record = asdict(result)
    for field in ('model', 'question', 'answer'):
        if record[field] is not None:
            record[field] = redact(record[field])
    stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    stream.flush()


def compare(client, models: list[str], questions: list[str], trials: int, stream,
            max_requests: int = DEFAULT_MAX_REQUESTS) -> bool:
    """Independent requests, no conversation history and no automatic retry loop."""
    if trials < 1 or max_requests < 1 or len(models) * len(questions) * trials > min(max_requests, 12):
        raise ValueError('予定リクエスト数が上限を超えています。')
    success = True
    for model in models:
        for question in questions:
            for trial in range(1, trials + 1):
                result = measure(client, question, model=model, trial=trial)
                write_record(stream, result)
                print(f'[compare] model={redact(model)} trial={trial} success={result.success}')
                print_metrics(result)
                if not result.success:
                    print(error_message(result))
                    success = False
                    if result.error_code == 429:
                        return False
    return success


def chat(client, first_question: str | None = None,
         max_requests: int = DEFAULT_MAX_REQUESTS, stream=None) -> None:
    history = []
    question = first_question
    requests = 0
    while requests < min(max_requests, 3):
        try:
            if question is None:
                question = input('質問（exitで終了）> ')
            if question.strip().lower() == 'exit':
                return
            if not question.strip():
                question = None
                continue
            requests += 1
            result = measure(client, question, history=history, trial=requests)
            if stream is not None:
                write_record(stream, result)
            print_metrics(result)
            print(redact(result.answer) if result.success else error_message(result))
            if result.error_code == 429:
                return
            question = None
        except (EOFError, KeyboardInterrupt):
            print('\n対話を終了しました。')
            return
    print('リクエスト上限に達したため対話を終了しました。')


def main() -> None:
    parser = argparse.ArgumentParser(description='LLMに質問する（単発・対話・モデル比較）')
    parser.add_argument('question', nargs='?')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--chat', action='store_true')
    mode.add_argument('--compare', action='store_true')
    parser.add_argument('--models', nargs='+', help='比較対象。省略時はAIE_MODELの設定値')
    parser.add_argument('--questions', nargs='+', help='比較する質問。各条件は独立した呼び出し')
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--max-requests', type=int, default=DEFAULT_MAX_REQUESTS,
                        help='プロセス内の呼び出し上限（既定15、SDK再試行なし）')
    parser.add_argument('--output', type=Path, help='比較結果の新規JSONLファイル（上書き不可）')
    args = parser.parse_args()
    if args.max_requests < 1:
        parser.error('--max-requestsは1以上にしてください')
    if args.trials < 1:
        parser.error('--trialsは1以上にしてください')
    if not args.compare and (args.models or args.questions or (args.output and not args.chat) or args.trials != 3):
        parser.error('比較用オプションには--compareが必要です')
    if args.compare and not args.output:
        parser.error('--compareには--outputが必要です')
    if args.compare and args.question and args.questions:
        parser.error('questionと--questionsは同時に指定できません')
    question = args.question or DEFAULT_QUESTION
    models = args.models or [MODEL]
    questions = args.questions or [question]
    if any(model not in FREE_TIER_MODELS for model in models):
        parser.error('無料枠対象として確認していないモデルです')
    if args.chat and MODEL != 'gemini-3.5-flash-lite':
        parser.error('会話実験のモデルはgemini-3.5-flash-liteに限定します')
    if args.compare and len(models) * len(questions) * args.trials > min(args.max_requests, 12):
        parser.error('予定リクエスト数が--max-requestsを超えています')
    try:
        if not args.chat and not args.compare:
            print(f'[model] {redact(MODEL)}')
            print(f'[質問] {redact(question)}\n')
            print(redact(ask(question)))
            return
        # Open the log before creating a client; existing results are protected.
        if args.compare:
            with args.output.open('x', encoding='utf-8') as stream:
                client = create_client(models)
                try:
                    ok = compare(client, models, questions, args.trials, stream,
                                 args.max_requests)
                finally:
                    client.close()
            if not ok:
                raise SystemExit(1)
        else:
            print(f'[model] {redact(MODEL)}')
            with (args.output.open('x', encoding='utf-8') if args.output else nullcontext()) as stream:
                client = create_client([MODEL])
                try:
                    chat(client, args.question, args.max_requests, stream)
                finally:
                    client.close()
    except experiment_budget.BudgetStopped as error:
        print(str(error))
        raise SystemExit(1) from None
    except RequestFailed as error:
        print(error_message(error.result))
        raise SystemExit(1) from None
    except OSError:
        print('比較結果ファイルを作成・保存できません。親フォルダと新規ファイル名を確認してください。')
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print('\n処理を中断しました。')
        raise SystemExit(130) from None
    except Exception:
        print('処理に失敗しました（クライアントまたは応答処理エラー）。')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
