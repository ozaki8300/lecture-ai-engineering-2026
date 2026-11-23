# 第6回 解答

## 演習

- 準備：公式の `lec06-start` から `lec06` を作成し、`uv sync --frozen` の成功と `app/mcp_agent.py` の存在を確認した。
- STEP 1 のツールの名前：直接登録は `search_materials`、MCP は `glossary_search_glossary`。
- STEP 3 の一覧には `fetch_fetch` が追加された。STEP 4 では AWS Knowledge MCP に接続し、`aws_aws___read_documentation`、`aws_aws___search_documentation`、`aws_aws___list_regions`、`aws_aws___get_regional_availability`、`aws_aws___retrieve_skill` が追加された。
- STEP 2〜4 の既定モデルによる `--trace` は HTTP 503、代替モデルは HTTP 503 または 504 で終了した。下記は固定引数を渡す FunctionModel と実際の MCP サーバーを使った接続確認のトレース。

```text
# STEP 2
[tool call]   glossary_search_glossary({'query': '3レベル'})
[tool return] glossary_search_glossary -> [{"term": "宿題の3レベル", "lecture": 1, "definition": "AIE2026の宿題の構成。「基礎（講義内容の確認）」「実装（自分でコードを書く）」「発展（調査や試行）」の3段階があり、どれか1つに取り組…
# STEP 3
[tool call]   fetch_fetch({'url': 'https://modelcontextprotocol.io/'})
[tool return] fetch_fetch -> Contents of https://modelcontextprotocol.io/:
> ## Documentation Index
> Fetch the complete documentation index at: http…
# STEP 4
[tool call]   aws_aws___search_documentation({'search_phrase': 'Amazon S3 versioning', 'limit': 2})
[tool return] aws_aws___search_documentation -> {'content': {'result': [{'rank_order': 1, 'title': 'New Feature: Amazon S3 now supports Object Versioning | AWS News Blo…
```

- 気づき・疑問：直接登録と MCP の両方でツール名、説明文、引数のスキーマがモデルに渡った。MCP では設定名がツール名の先頭に付くため、一覧から接続先を判別できた。

## 宿題

- 取り組んだレベル：実装
- やったこと：fork の `lec05`（`ca6566e`）にあった `make_quiz` と依存する `random` の import を `app/tools.py` に移植した。既存の `load_glossary` と `json` を使い、`app/mcp_server.py` でツールを公開した。
- 結果：最終設定でツールは8個になり、一覧に整数引数 `lecture` を持つ `glossary_make_quiz` が追加された。実際の stdio 接続で TestModel から呼び出すと、第2回は用語の問題と正解が JSON で返り、第99回は別の回を指定する案内が返った。全 pytest は 21 件通過した。

```text
[tool call]   glossary_make_quiz({'lecture': 2})
[tool return] glossary_make_quiz -> {"question": "「教師ありファインチューニング（SFT）」の意味は？", "answer": "入力と望ましい出力のペア（例：質問と模範回答）を使ってモデルを追加学習させる方法。指示に従う振る舞いを身につけさせる代表的な手法。"…
[tool call]   glossary_make_quiz({'lecture': 99})
[tool return] glossary_make_quiz -> 第99回の用語は見つかりませんでした。別の回を指定してください。…
```

- 気づき・疑問：用語がない場合も通常の戻り値にすると、理由と次に指定すべき内容をツールの結果としてモデルに渡せた。
- 第2回と第99回を尋ねた Gemini の `--trace` はどちらも HTTP 503 で終了した。上記の呼び出しと戻り値は TestModel で確認した。

### 発展課題：CodexのMCP設定方法の調査と比較

普段利用しているCodexのMCP設定について、公式ドキュメントと実際の開発環境を調査し、講義で使用したエージェントの設定方法と比較した。

比較を通じて、MCPは共通の接続規格であるものの、設定ファイルの形式や保存場所、設定の適用範囲はアプリケーションによって異なることを理解した。一方、ローカルサーバーをコマンドで起動する方法や、リモートサーバーにURLで接続する方法は、両者に共通する考え方である。また、Codexではユーザー共通とプロジェクト単位で設定を管理できることが分かった。

なお、今回調査した実際のCodex設定には、利用可能な外部ツールや実行環境に関する情報が含まれるため、公開リポジトリへの提出にあたっては具体的な設定値や接続先を記載しないこととした。

今回の調査から、MCPの共通仕様を理解することに加え、利用するエージェントごとの設定方法と情報公開の範囲を適切に判断することの重要性を学んだ。

調査日：2026年11月23日

参考資料：[OpenAI公式 Codex MCPドキュメント](https://developers.openai.com/codex/mcp)
