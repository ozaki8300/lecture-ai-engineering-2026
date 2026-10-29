"""第2回 宿題（発展）: 用語集から SFT 用の Q&A データ（JSONL）を作る。

使い方:
    uv run python scripts/make_sft_dataset.py > glossary_sft.jsonl

できたファイルを Colab にアップロードし、LoRA 学習のデータとして使う。
質問の言い回しを増やす、別名でも質問を作る、検証用に一部を分ける、などを工夫してみよう。
"""

import json
import sys
from pathlib import Path

GLOSSARY = Path(__file__).resolve().parent.parent / "data" / "glossary.json"
TEMPLATES = ["「{term}」とは何ですか？", "{term}について簡単に説明してください。"]


def main() -> None:
    terms = json.loads(GLOSSARY.read_text(encoding="utf-8"))["terms"]
    for t in terms:
        for template in TEMPLATES:
            record = {
                "messages": [
                    {"role": "user", "content": template.format(term=t["term"])},
                    {"role": "assistant", "content": t["definition"]},
                ]
            }
            sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
