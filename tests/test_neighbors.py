"""前後ページ取得とCLI経路をAPIなしで検証する。"""

import importlib
import json
import sys

import pytest
from pydantic_ai.models.test import TestModel

from app import retrieval, tools


def page(document, number):
    return {"page_id": f"{document}-p{number:03d}", "document_id": document,
            "lecture": 1, "document_title": document, "section": "章",
            "title": f"ページ{number}", "page": number, "content": f"本文{number}"}


@pytest.mark.parametrize("number,expected", [(1, [1, 2]), (2, [2, 1, 3]), (3, [3, 2])])
def test_neighbors_and_boundaries(number, expected):
    pages = [page(document, n) for document in ("a", "b") for n in (1, 2, 3)]
    hit = {**page("a", number), "distance": 0.1}
    result = retrieval.add_neighbor_pages([hit], pages)
    assert [p["page"] for p in result] == expected
    assert all(p["document_id"] == "a" for p in result)
    assert result[0] == hit
    assert all(p == page("a", p["page"]) for p in result[1:])
    assert hit == {**page("a", number), "distance": 0.1}


def test_overlap_duplicate_seeds_and_document_boundary():
    pages = [page(d, n) for d in ("a", "b") for n in range(1, 5)]
    hits = [{**page(d, n), "distance": 0.1} for d, n in [("a", 2), ("a", 3), ("a", 2), ("b", 2)]]
    result = retrieval.add_neighbor_pages(hits, pages)
    keys = [(p["document_id"], p["page"]) for p in result]
    assert keys == [("a", 2), ("a", 3), ("b", 2), ("a", 1), ("a", 4), ("b", 1), ("b", 3)]
    assert len(keys) == len(set(keys))
    assert all("distance" not in p for p in result[3:])


def test_missing_pages_empty_hits_and_section_boundary():
    pages = [page("a", n) for n in (1, 3, 4)]
    pages[-1]["section"] = "次の章"
    assert retrieval.add_neighbor_pages([], pages) == []
    assert retrieval.add_neighbor_pages([pages[0]], pages) == [pages[0]]
    assert retrieval.add_neighbor_pages([pages[1]], pages) == pages[1:]


def test_search_expansion_and_tool_result(monkeypatch):
    pages = [page("a", n) for n in (1, 2, 3)]
    calls = []
    monkeypatch.setattr(retrieval, "search", lambda query: calls.append(query) or [pages[1]])
    monkeypatch.setattr(retrieval, "load_pages", lambda source: pages)
    result = json.loads(tools.search_materials_with_neighbors("質問"))
    assert calls == ["質問"]
    assert [p["page"] for p in result["results"]] == [2, 1, 3]
    assert json.loads(tools.search_materials("質問"))["results"] == [pages[1]]


@pytest.mark.parametrize("flag,expected", [
    ([], {"search_materials", "search_glossary", "get_lecture_summary", "make_quiz", "save_learning_log"}),
    (["--neighbors"], {"search_materials", "search_glossary", "get_lecture_summary", "make_quiz", "save_learning_log"}),
    (["--tree"], {"browse_tree"}), (["--graph"], {"search_graph"}), (["--full-context"], set()),
])
def test_cli_routes_register_tools_without_api(monkeypatch, flag, expected):
    module = importlib.import_module("app.agent")
    observed = []
    def inspect(target):
        model = TestModel(call_tools=[])
        target.run_sync("一覧", model=model)
        observed.append({t.name for t in model.last_model_request_parameters.function_tools})
    monkeypatch.setattr(module, "print_tools", inspect)
    monkeypatch.setattr(sys, "argv", ["agent", *flag, "--list-tools"])
    module.main()
    assert observed == [expected]


def test_neighbors_keep_save_approval_and_denial(monkeypatch, tmp_path):
    module = importlib.import_module("app.agent")
    directory = tmp_path / "learning_log"
    monkeypatch.setattr(tools, "LEARNING_LOG_DIR", directory)
    monkeypatch.setattr(module, "build_model", lambda: TestModel(call_tools=["save_learning_log"]))
    prompts = []
    def deny(prompt):
        assert not directory.exists()
        prompts.append(prompt)
        return "n"
    monkeypatch.setattr("builtins.input", deny)
    monkeypatch.setattr(sys, "argv", ["agent", "--neighbors", "保存して"])
    module.main()
    assert len(prompts) == 1
    assert not directory.exists()


def test_mcp_instructions_do_not_require_unpublished_rag_tool(monkeypatch):
    module = importlib.import_module("app.agent")
    monkeypatch.setattr(module, "Agent", lambda **kwargs: kwargs)
    target = module.build_mcp_agent()
    assert target["instructions"] == module.MCP_INSTRUCTIONS
    assert "search_materials" not in target["instructions"]


def test_tree_and_graph_do_not_expand_neighbors(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Tree/Graphから前後ページ改良を呼んだ")
    monkeypatch.setattr(retrieval, "search_with_neighbors", forbidden)
    monkeypatch.setattr(retrieval, "add_neighbor_pages", forbidden)
    assert retrieval.browse_tree()["children"]
    monkeypatch.setattr(retrieval, "search", lambda query: [retrieval.read_page("lec02-main-p052") | {"distance": 0.1}])
    assert retrieval.search_graph("LoRA")["seeds"][0]["page_id"] == "lec02-main-p052"


@pytest.mark.parametrize("name", ["search_materials", "search_materials_with_neighbors"])
def test_embedding_429_stops_agent_without_second_tool_call(monkeypatch, name):
    from google.genai.errors import APIError
    from pydantic_ai import Tool
    from pydantic_ai.exceptions import ModelHTTPError
    module = importlib.import_module("app.agent")
    calls = []
    def limited(query):
        calls.append(query)
        raise APIError(429, {"message": "test rate limit"})
    monkeypatch.setattr(retrieval, "search", limited)
    with module.agent.override(tools=[Tool(getattr(tools, name), name="search_materials")]):
        with pytest.raises(ModelHTTPError) as error:
            module.agent.run_sync("質問", model=TestModel(call_tools=["search_materials"]))
    assert error.value.status_code == 429
    assert len(calls) == 1
