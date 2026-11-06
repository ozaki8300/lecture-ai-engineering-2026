"""Day3: 実LLMを呼ばず、クイズの根拠と保存の承認境界を検証する。"""
import json

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.models.test import TestModel

from app import tools
from app.agent import agent, resolve_approvals


def test_quiz_uses_definition_from_requested_lecture():
    quiz = json.loads(tools.make_quiz(2))
    assert any(quiz == {"question": f"「{entry['term']}」の意味は？",
                        "answer": entry["definition"]}
               for entry in tools.load_glossary() if entry["lecture"] == 2)


@pytest.mark.parametrize("lecture", [0, -1, 999])
def test_quiz_missing_lecture(lecture):
    assert "見つかりません" in tools.make_quiz(lecture)


@pytest.mark.parametrize("reply,expected", [("y", True), ("yes", True),
                                           ("n", False), ("", False),
                                           ("maybe", False), (None, False)])
def test_cli_approval_boundary(monkeypatch, tmp_path, reply, expected):
    directory = tmp_path / "learning_log"
    monkeypatch.setattr(tools, "LEARNING_LOG_DIR", directory)
    model = TestModel(call_tools=["save_learning_log"])
    pending = agent.run_sync("学習記録を保存", model=model)
    assert isinstance(pending.output, DeferredToolRequests)
    assert not directory.exists(), "承認前にはフォルダも作らない"
    args = pending.output.approvals[0].args_as_dict()

    def respond(prompt):
        assert not directory.exists()
        if reply is None:
            raise EOFError
        return reply

    monkeypatch.setattr("builtins.input", respond)
    result = resolve_approvals(agent, pending, model)
    assert isinstance(result.output, str)
    files = list(directory.glob("*.json"))
    assert len(files) == int(expected)
    if expected:
        record = json.loads(files[0].read_text(encoding="utf-8"))
        assert record["topic"] == args["topic"]
        assert record["content"] == args["content"]
    else:
        assert not directory.exists()


def test_log_preserves_unicode_and_never_uses_topic_as_path(monkeypatch, tmp_path):
    directory = tmp_path / "learning_log"
    monkeypatch.setattr(tools, "LEARNING_LOG_DIR", directory)
    for _ in range(2):
        tools.save_learning_log("../LoRA", "日本語の学習記録")
    files = list(directory.glob("*.json"))
    assert len(files) == 2
    assert all(json.loads(p.read_text(encoding="utf-8"))["content"] == "日本語の学習記録"
               for p in files)


def test_empty_log_does_not_write(monkeypatch, tmp_path):
    directory = tmp_path / "learning_log"
    monkeypatch.setattr(tools, "LEARNING_LOG_DIR", directory)
    assert "保存しません" in tools.save_learning_log("", "")
    assert not directory.exists()
