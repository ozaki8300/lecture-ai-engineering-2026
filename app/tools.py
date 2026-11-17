"""第3回: エージェントに持たせるツール。

ここに書いた関数の docstring と型ヒントが、そのままモデルへの「ツールの説明文」になる。
説明文を書き換えると、モデルがツールを使うかどうか・どんな引数で呼ぶかが変わる。
"""

import json
import random
from datetime import datetime, timezone
from uuid import uuid4
from functools import cache
from pathlib import Path

from app.config import api_error_message

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAX_RESULTS = 3
LEARNING_LOG_DIR = Path(__file__).resolve().parent.parent / "learning_log"


@cache
def load_glossary() -> list[dict]:
    with open(DATA_DIR / "glossary.json", encoding="utf-8") as f:
        return json.load(f)["terms"]


def _normalize(text: str) -> str:
    return text.casefold().replace(" ", "").replace("　", "")


def _names(entry: dict) -> list[str]:
    return [_normalize(entry["term"]), _normalize(entry["id"])] + [
        _normalize(a) for a in entry["aliases"]
    ]


def search_glossary(query: str) -> str:
    """講座の用語集から用語を検索し、定義と、どの回で扱うかを返す。

    AIエンジニアリングの用語や、この講座（AIE2026）に固有の言葉について
    答える前に使うこと。query には調べたい用語を1つだけ入れる（例: "LoRA"）。
    """
    q = _normalize(query)
    if not q:
        return "検索語が空です。調べたい用語を1つ指定してください。"
    glossary = load_glossary()

    exact = [e for e in glossary if q in _names(e)]
    # 「r」のような短い別名が何にでも当たらないよう、名前側が短いものは除く
    partial = [
        e for e in glossary
        if e not in exact and any(q in n or (len(n) >= 3 and n in q) for n in _names(e))
    ]
    in_definition = [
        e for e in glossary
        if e not in exact and e not in partial and q in _normalize(e["definition"])
    ]
    hits = (exact + partial + in_definition)[:MAX_RESULTS]

    if not hits:
        return f"用語集に「{query}」は見つかりませんでした。"
    return json.dumps(
        [
            {
                "term": e["term"],
                "lecture": e["lecture"],
                "definition": e["definition"],
                "related": e["related"],
            }
            for e in hits
        ],
        ensure_ascii=False,
    )


def get_lecture_summary(lecture: int) -> str:
    """指定した回（1始まりの整数）の講義の要点を返す。

    「第3回では何を学ぶ？」のように、回の内容や関係を聞かれたときに使う。
    """
    path = DATA_DIR / "lectures" / f"{lecture:02d}.md"
    if not path.exists():
        available = sorted(p.stem for p in (DATA_DIR / "lectures").glob("*.md"))
        return f"第{lecture}回の要点はまだありません。用意されている回: {', '.join(available)}"
    return path.read_text(encoding="utf-8")


def make_quiz(lecture: int) -> str:
    """確認問題や復習クイズを求められたら使う。指定の回の用語から1問と正解を返す。"""
    terms = [entry for entry in load_glossary() if entry["lecture"] == lecture]
    if not terms:
        return f"第{lecture}回の用語は見つかりませんでした。別の回を指定してください。"
    entry = random.choice(terms)
    return json.dumps({"question": f"「{entry['term']}」の意味は？",
                       "answer": entry["definition"]}, ensure_ascii=False)


def save_learning_log(topic: str, content: str) -> str:
    """利用者が学習記録の保存を頼んだときに使う。承認後にlearning_log/へ新規保存する。

    topic は学習テーマ、content は保存する本文。APIキーなどの秘密情報を含めないこと。
    """
    if not topic.strip() or not content.strip():
        return "テーマと本文を空にせず指定してください。保存しませんでした。"
    now = datetime.now(timezone.utc)
    filename = f"{now:%Y%m%dT%H%M%S%fZ}_{uuid4().hex}.json"
    record = {"created_at": now.isoformat(), "topic": topic, "content": content}
    LEARNING_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LEARNING_LOG_DIR / filename).open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return f"学習記録を保存しました: learning_log/{filename}"


def search_materials(query: str) -> str:
    """第1〜3回の本講義資料を検索し、関連ページの本文・タイトル・ページ番号を返す。

    queryに調べたい内容を指定する。
    """
    from google.genai.errors import APIError
    import sqlite3
    from app.retrieval import search

    try:
        return json.dumps({"results": search(query)}, ensure_ascii=False)
    except (ValueError, FileNotFoundError, sqlite3.Error) as e:
        return json.dumps({"error": str(e), "results": []}, ensure_ascii=False)
    except APIError as e:
        if e.code == 429:
            from pydantic_ai.exceptions import ModelHTTPError
            from app.config import EMBEDDING_MODEL
            raise ModelHTTPError(429, EMBEDDING_MODEL, api_error_message(429)) from e
        return json.dumps({"error": api_error_message(e.code), "results": []}, ensure_ascii=False)


def search_materials_with_neighbors(query: str) -> str:
    """第1〜3回の本講義資料を検索し、関連ページと同じ資料の前後1ページの本文・引用情報を返す。"""
    import sqlite3
    from google.genai.errors import APIError
    from app.retrieval import search_with_neighbors

    try:
        return json.dumps({"results": search_with_neighbors(query)}, ensure_ascii=False)
    except (ValueError, OSError, sqlite3.Error) as e:
        return json.dumps({"error": str(e), "results": []}, ensure_ascii=False)
    except APIError as e:
        if e.code == 429:
            from pydantic_ai.exceptions import ModelHTTPError
            from app.config import EMBEDDING_MODEL
            raise ModelHTTPError(429, EMBEDDING_MODEL, api_error_message(429)) from e
        return json.dumps({"error": api_error_message(e.code), "results": []}, ensure_ascii=False)


def browse_tree(node_id: str | None = None) -> str:
    """目次とページの先頭文を返す。ページIDを指定すると全文を返す。

    最初はnode_idを省略する。続いて返されたIDを指定して目次をたどる。
    """
    from app.retrieval import browse_tree as browse

    try:
        return json.dumps(browse(node_id), ensure_ascii=False)
    except (ValueError, OSError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def search_graph(query: str) -> str:
    """資料を検索し、前後ページ・共通用語をたどって関連ページの本文と引用情報を返す。

    queryに調べたい内容を指定する。
    """
    import sqlite3
    from google.genai.errors import APIError
    from app.retrieval import search_graph as search

    try:
        return json.dumps(search(query), ensure_ascii=False)
    except (ValueError, OSError, sqlite3.Error) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except APIError as e:
        return json.dumps({"error": api_error_message(e.code)}, ensure_ascii=False)
