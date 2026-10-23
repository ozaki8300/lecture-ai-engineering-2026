# AIE2026 演習・宿題リポジトリ

AIエンジニアリング実践講座（AIE2026）の演習と宿題で使うリポジトリです。
講座を通して、**AIエンジニアリングの学習を支援するエージェント**を少しずつ育てていきます。

## 想定する環境

演習は **ブラウザだけで** 進められるように作っています。自分の PC への導入は不要です。

| 使うもの | 用途 | 注意 |
|---|---|---|
| GitHub の **個人** アカウント | このリポジトリを fork し、作業ブランチと PR で提出する | 会社や学校が管理するアカウントでは Codespaces・Copilot が制限されることがあります |
| GitHub Codespaces | 第1・3・5回などの開発環境。`.devcontainer/` の設定で Python 3.12・uv・Node.js が入ったコンテナが自動で用意されます | 個人アカウントの無料枠は月 120 コア時間（2 コアで約 60 時間）。**使い終わったら停止**してください |
| GitHub Copilot（Free でよい） | コードの補完とチャット。第5回の発展課題では MCP クライアントとしても使います | Free は補完・チャットの回数に月の上限があります |
| Gemini API（Google AI Studio の無料枠） | LLM の呼び出し。**個人**の Google アカウントでキーを発行し、Codespaces secret `GEMINI_API_KEY` で渡します | 無料枠には 1 分あたり・1 日あたりの上限があります（下の「前提と制約」） |
| Google Colab（T4 GPU、無料枠） | 第2回の LoRA 学習だけに使います | GPU の利用時間に上限があります。終わったらランタイムを削除してください |

自分の PC で動かす場合は、Python 3.12 と [uv](https://docs.astral.sh/uv/) を入れて `uv sync --frozen` を実行し、環境変数 `GEMINI_API_KEY` を設定してください。

## はじめに（第1回）

1. このリポジトリを自分のアカウントに **fork** する
2. [Google AI Studio](https://aistudio.google.com/apikey) で Gemini API キーを作る
3. GitHub の **Settings → Codespaces → Secrets** に `GEMINI_API_KEY` を登録し、使う repository に自分の fork を選ぶ
4. 自分の fork で **Code → Codespaces → Create codespace on main**
5. ターミナルで動作確認

```bash
uv run pytest -q
uv run python -m app.ask "LoRAとは何ですか？"
```

詳しい手順は [lectures/01/problem.md](lectures/01/problem.md) を見てください。

## 構成

```
.devcontainer/   Codespaces の環境定義（Python 3.12、uv、Node.js。起動時に uv sync --frozen）
.github/workflows/test.yml  PR ごとにテストを実行（fork では Actions タブで有効化が必要）
app/
  config.py      モデル名とAPIキーの読み込み
  ask.py         第1回：LLMに質問する
  tools.py       第3回：エージェントのツール（用語集の検索など）
  agent.py       第3回：Pydantic AI の学習支援エージェント（第5回：--mcp で MCP 経由）
  mcp_server.py  第5回：用語集を公開する MCP サーバー
  mcp_servers.json 第5回：使う MCP サーバーの設定（mcpServers 形式）
data/
  glossary.json  講座の用語集
  lectures/      各回の要点
lectures/NN/
  problem.md     その回の演習と宿題
  answer.md      あなたが記入して提出するファイル
notebooks/       第2回：Google Colab で動かす LoRA のノートブック（lora_format_tuning.ipynb）
scripts/         補助スクリプト
tests/           テスト（APIキー不要）
pyproject.toml / uv.lock  依存パッケージとその固定版
```

## 提出のしかた

1. 回ごとに作業ブランチを作る（例：`git switch -c lec01`）
2. 演習・宿題を行い、`lectures/NN/answer.md` に記入する
3. push して、**自分の fork の中で** 作業ブランチ → `main` の PR を作る

宿題は「基礎・実装・発展」の3レベルがあり、どれか1つに取り組めば完了です。

## 前提と制約

- **依存パッケージの版は固定**しています（`uv.lock`。Python 3.12、pydantic-ai-slim 2.57.0、google-genai 2.31.0）。講座の期間中は上げません。`uv sync --frozen` でそのまま入れてください。
- **既定のモデルは `gemini-3.5-flash-lite`** です。環境変数 `AIE_MODEL` で切り替えられます（下の「モデルの切り替え」）。ほかのモデルや自前契約の API を使ってもかまいません
- **Gemini API の無料枠にはレート制限**があります。エージェント（第3回以降）は 1 つの質問で LLM を複数回呼ぶので、第1回より早く上限に達します。`レート制限に達しました` と出たら 1 分ほど待ってください。`モデルが混み合っています（503）` が続くときは `AIE_MODEL=gemini-3.1-flash-lite` を試してください
- **API キーは環境変数 `GEMINI_API_KEY` だけ**で渡します。コード・コミット・チャットに貼らないでください。漏れたら AI Studio で削除して作り直します
- Codespaces からインターネットへは出られますが、外から Codespaces の中へは接続できません（ポート転送は既定で非公開）。第5回の MCP サーバーは Codespaces の中で動かします
- テスト（`uv run pytest -q`）は API キーなしで動きます（LLM を呼ばない TestModel を使用）

## 守ってほしいこと

- **APIキーをコードやコミットに書かない。** Codespaces secret で渡します
- **使い終わったら Codespaces を停止する。** 無料枠を使い切ると起動できなくなります
- AIツール（Copilot など）は使ってかまいません。提案されたコードは、読んで理解してから採用してください

## モデルの切り替え

既定モデルは `gemini-3.5-flash-lite` です。環境変数 `AIE_MODEL` で切り替えられます。

```bash
AIE_MODEL=gemini-3.5-flash uv run python -m app.ask "LoRAとは何ですか？"
```

無料枠には1分あたり・1日あたりのリクエスト上限があります。上限は [Gemini API のレート制限のページ](https://ai.google.dev/gemini-api/docs/rate-limits) で確認してください。

## 第1回：対話と比較計測

従来の単発実行と質問省略時の既定質問はそのまま利用できます。
入力・出力トークン数とAPI呼び出し時間を表示します。usageがない場合は
「取得不可」と表示し、0トークンとは区別します。

```powershell
uv run python -m app.ask "LoRAとは何ですか？"
uv run python -m app.ask --chat
uv run python -m app.ask "LoRAとは何ですか？" --chat
```

`--chat` はこのプロセス内でユーザー発言とモデル応答を保持します。
`exit`、EOF、Ctrl+Cで終了します。失敗したターンは履歴に追加せず、続けて
質問できます。履歴のファイル保存は行いません。

以下はAPIキーを設定した後に実行する比較実験の例です。
**この機能の実装時には実APIを使った実験を行っていません。**

```powershell
uv run python -m app.ask --compare --models gemini-3.5-flash-lite gemini-3.1-flash-lite --questions "LoRAとは何ですか？" "AIE2026の講座の宿題にある「3レベル」とは何ですか？" --max-requests 12 --output lectures/01/experiments/01_basic/lec01-comparison.jsonl
```

モデルと質問の各組合せを既定で3回ずつ実行します（`--trials` で変更可能）。
各試行は履歴なしで、質問文字列をそのまま渡します。`--models` を省略すると
`AIE_MODEL` の設定値、未設定なら既定モデルを使います。PowerShellでの切替例：

```powershell
$env:AIE_MODEL = "gemini-3.5-flash-lite"
```

比較結果はUTF-8のJSONL（1行1試行）です。`model`、`question`、`trial`、
`elapsed_seconds`、`input_tokens`、`output_tokens`、`success`、`answer`、
`error_type`、`error_code`、`question_id`（質問UTF-8のSHA-256）、`started_at`（UTC ISO 8601）を記録します。トークン数が取得できない場合は `null`。
回答を行ごとに読んで基礎課題の差を比較でき、同じ記録を発展課題の集計にも使えます。
応答時間は `perf_counter()` による同期生成呼び出しの経過時間で、SDK内部の待機を
含み、クライアント作成・画面表示・ファイル書き込み時間は含みません。
初回トークン到着時間やモデル単体の推論時間とは異なります。

APIエラー・通信失敗も記録し、1件でも失敗すれば終了コード1を返します。
429は記録後に比較・対話を即停止します。アプリ・SDKとも自動再試行しません。
`--max-requests` はプロセス内の追加上限です。永続共有台帳 `lectures/01/experiments/04_analysis/day1_api_budget_v1/` により、再起動・別プロセスを含め比較12回、会話3回、合計15回を上限とします。単発CLIも比較枠を消費します。
送信前に予約を新規保存し、失敗・中断でも返却しません。同時実行は待たず停止し、途中停止の未完了予約・台帳破損・429停止記録があれば以後の送信も拒否します。台帳の自動再作成・自動リセット・別台帳への切替は行いません。
台帳の `preflight_confirmed.json` がない間はキー取得・クライアント作成前に停止します。Free Tier、両モデルのアクセス、残枠を確認した後にのみ、確認日時・制限値・実行間隔とともに `free_tier`、`model_access_confirmed`、`quotas_confirmed` がすべてtrueの新規確認ファイルを保存します。未確認のままtrueにしないでください。
予約・結果・429停止記録は追記専用の新規ファイルです。未完了予約や429停止は自動解除しません。解除が必要な場合は原本を保持したまま別途レビューしてください。
Day1 CLIの許可モデルは `gemini-3.5-flash-lite` と `gemini-3.1-flash-lite` に限定します。2.5 Flash-Liteへの変更は撤回されました。許可設定だけで現在の利用可否や無料枠の残量を保証するものではありません。
本実験前に、使用キーが `Life Cards OAuth` に属すること、Free Tier、両モデルのアクセスと最新利用状況を確認します。リクエストは直列で15秒以上空け、未確認なら実行しません。上記CLI例自体には待機制御がないため、連続実験にそのまま使わないでください。
実行前に利用プロジェクトがFree Tierであること、各モデルへのアクセスとRPM/TPM/RPD残枠をAI Studioで確認してください。無料枠対象のモデルでも有料Tierでは課金されます。
`gemini-3.5-flash` は公式資料で3.6 Flashへ自動転送されるため比較には使いません。
対話も `--chat --max-requests 3 --output lectures/01/experiments/02_chat/lec01-chat.jsonl` で保存できます。
3.5 Flash-Liteではtemperatureは非推奨かつ無視されるため、今回の実験はモデル比較とします。
失敗行も残るため、成功率と成功試行の時間を分けて集計してください。
既存ログは上書きしません。新しいファイル名と既存の親フォルダを指定してください。
中断時には書き込み済みの行を保持します。

SDK例外の詳細文・クライアント設定・APIキーは出力しません。記録する質問と回答は、
認識できるAPIキー形式とパスワード等の代入表現をマスクします。ただし任意の秘密文を
完全に識別する機能ではないため、質問には秘密情報を含めないでください。
`lectures/01/experiments/` は `.git/info/exclude` でローカル除外します。基礎のJSONLを発展でも再利用し、再呼び出しせず集計します。実験結果の考察と提出用の要約は
実測後に `lectures/01/answer.md` に記入してください。

Day1追加テストはSDKクライアントとキー取得を偽物に差し替え、実APIを呼びません。
既存テストを含めた実行方法は `uv run --frozen --no-sync pytest -q` です。
