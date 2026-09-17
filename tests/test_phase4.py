from pathlib import Path
import py_compile

from benchmark.dashboard import render
from benchmark.harder_catalog import CASES
from benchmark.harder_dataset import materialize
from core.search import HybridCodeSearch, chunk_repository
from core.tools import make_executor


def test_ast_chunking_preserves_symbols_and_line_evidence(tmpdir):
    root = Path(str(tmpdir))
    source = root / "service.py"
    source.write_text(
        "import json\n\ndef load_user(user_id):\n    return {'id': user_id}\n\n"
        "class UserService:\n    def fetch(self, user_id):\n        return load_user(user_id)\n",
        encoding="utf-8",
    )
    chunks = chunk_repository(root)
    symbols = {chunk.symbol for chunk in chunks}
    assert {"load_user", "UserService", "<module>"}.issubset(symbols)
    function = next(chunk for chunk in chunks if chunk.symbol == "load_user")
    assert function.path == "service.py"
    assert function.start_line == 3
    assert "return {'id': user_id}" in function.text


def test_hybrid_search_recalls_contract_and_implementation(tmpdir):
    root = Path(str(tmpdir))
    case = CASES[0]
    paths = materialize(case, root / case.id)
    hits = HybridCodeSearch(paths["workspace"]).search(case.issue, top_k=5)
    hit_paths = {hit.chunk.path for hit in hits}
    assert "docs/discounts.md" in hit_paths
    assert "shop/pricing.py" in hit_paths
    assert all(hit.chunk.start_line <= hit.chunk.end_line for hit in hits)


def test_search_tool_returns_citations_and_respects_workspace(tmpdir):
    root = Path(str(tmpdir))
    (root / "rules.md").write_text("Timeout values are milliseconds.", encoding="utf-8")
    search = HybridCodeSearch(root)
    executor = make_executor(root, code_search=search)
    result = executor("search_code", {"query": "timeout milliseconds", "top_k": 2})
    assert "rules.md:1-1" in result
    assert "milliseconds" in result
    assert "工作区之外" in executor("read_file", {"path": str(root.parent / "secret.txt")})


def test_harder_suite_is_multifile_compilable_and_hidden(tmpdir):
    root = Path(str(tmpdir))
    assert len(CASES) >= 6
    for case in CASES:
        paths = materialize(case, root / case.id)
        workspace, hidden = paths["workspace"], paths["hidden_tests"]
        assert len(case.files) >= 4
        assert not (workspace / "test_hidden.py").exists()
        assert (hidden / "test_hidden.py").is_file()
        for path in workspace.rglob("*.py"):
            py_compile.compile(str(path), doraise=True)
        hits = HybridCodeSearch(workspace).search(case.issue, top_k=5)
        retrieved = {hit.chunk.path for hit in hits}
        assert len(retrieved.intersection(case.relevant_files)) >= 2


def test_dashboard_accepts_phase4_title():
    row = {
        "case": "hard", "method": "hybrid", "tests_pass": True,
        "test_tampered": False, "container_timeout": False,
        "elapsed_seconds": 1.0, "total_tokens": 10, "cost_rmb": 0.001,
        "agent_passed": True, "verification_output": "",
    }
    assert render([row], title="Phase 4 retrieval ablation").startswith(
        "# Phase 4 retrieval ablation"
    )
