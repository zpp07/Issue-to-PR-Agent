"""Offline model migration checks; never load credentials or call a provider."""
import json
from types import SimpleNamespace

import pytest

import coder_reviewer
import core.client as client_module
from core.agent import run_agent
from core.client import DEFAULT_MODEL, StdlibOpenAI, _message_dict, resolve_model
from core.tools import build_tool_registry
from issue_to_pr import IssueToPRService


@pytest.mark.parametrize("configured,explicit,expected", [
    (None, None, "deepseek-flash"),
    ("", None, "deepseek-flash"),
    ("  ", None, "deepseek-flash"),
    ("  deployment-model  ", None, "deployment-model"),
    ("deepseek-chat", None, "deepseek-chat"),
    ("deployment-model", "frozen-model", "frozen-model"),
])
def test_model_precedence_without_reading_env_files(monkeypatch, configured, explicit, expected):
    monkeypatch.setattr(client_module, "_load_env", lambda: pytest.fail("must not load .env"))
    if configured is None:
        monkeypatch.delenv("AGENT_MODEL", raising=False)
    else:
        monkeypatch.setenv("AGENT_MODEL", configured)
    assert DEFAULT_MODEL == "deepseek-flash"
    assert resolve_model(explicit) == expected
    assert IssueToPRService(None, None, model=explicit).model == expected


@pytest.mark.parametrize("module_name", ["service_http", "service_api"])
def test_service_resolves_model_after_client_configuration(monkeypatch, module_name):
    if module_name == "service_api":
        pytest.importorskip("fastapi")
    module = pytest.importorskip(module_name)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    fake_client = object()

    def configured_client():
        # Simulate configuration becoming available, without reading any file.
        monkeypatch.setenv("AGENT_MODEL", "configured-after-client")
        return fake_client

    monkeypatch.setattr(module, "make_client", configured_client)
    service = module.service()
    assert service.client is fake_client
    assert service.model == "configured-after-client"


def _response(name, args, reasoning):
    message = SimpleNamespace(
        role="assistant", content=None, reasoning_content=reasoning,
        tool_calls=[SimpleNamespace(
            id=name, type="function",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        )],
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def test_agent_uses_flash_and_retains_reasoning_only_in_model_history(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    responses = [
        _response("read_file", {"path": "example.py"}, "PRIVATE_THINKING_FIELD"),
        _response("finish", {"result": "done"}, "next reasoning"),
    ]
    calls = []

    def create(**kwargs):
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    trace, events = [], []
    result, _ = run_agent(
        client, "system", build_tool_registry("read_file", "finish"), "task",
        execute_tool=lambda name, args: args.get("result", "source"),
        max_steps=2, trace=trace, on_event=events.append,
    )
    assert result == "done"
    assert [call["model"] for call in calls] == ["deepseek-flash", "deepseek-flash"]
    history = calls[1]["messages"]
    assert history[2].reasoning_content == "PRIVATE_THINKING_FIELD"
    assert history[3]["tool_call_id"] == "read_file"
    assert "PRIVATE_THINKING_FIELD" not in json.dumps({"trace": trace, "events": events})


@pytest.mark.parametrize("reasoning", [None, "", "PRIVATE_THINKING_FIELD"])
def test_stdlib_serializer_preserves_optional_reasoning(reasoning):
    message = _response("read_file", {"path": "example.py"}, reasoning).choices[0].message
    serialized = _message_dict(message)
    if reasoning is None:
        assert "reasoning_content" not in serialized
    else:
        assert serialized["reasoning_content"] == reasoning
    assert serialized["tool_calls"][0]["function"]["name"] == "read_file"
    assert _message_dict(serialized) is serialized


def test_stdlib_request_keeps_thinking_and_tool_history(monkeypatch):
    captured = []

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"choices":[{"message":{"role":"assistant","content":"done"}}]}'

    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data))
        assert request.full_url == "https://api.deepseek.com/chat/completions"
        return Response()

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    message = _response("read_file", {"path": "example.py"}, "retained reasoning").choices[0].message
    client = StdlibOpenAI("offline-placeholder", "https://api.deepseek.com")
    client.chat.completions.create(model=DEFAULT_MODEL, tools=[], messages=[
        {"role": "user", "content": "task"}, message,
        {"role": "tool", "tool_call_id": "read_file", "content": "source"},
    ])
    assert captured[0]["model"] == "deepseek-flash"
    assert captured[0]["messages"][1]["reasoning_content"] == "retained reasoning"
    assert captured[0]["messages"][2]["tool_call_id"] == "read_file"


def test_coder_and_reviewer_use_one_resolved_model(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    models = []

    def fake_run(*args, **kwargs):
        models.append(kwargs["model"])
        monkeypatch.setenv("AGENT_MODEL", "changed-during-run")
        return ({"passed": True, "issues": []} if len(models) == 2 else "done"), kwargs["usage"]

    monkeypatch.setattr(coder_reviewer, "run_agent", fake_run)
    passed, issues, rounds, _ = coder_reviewer.multi_agent_review(
        None, str(tmp_path / "example.py"), workspace=tmp_path, verbose=False,
        executor=lambda *_: "unused",
    )
    assert (passed, issues, rounds) == (True, [], 1)
    assert models == ["deepseek-flash", "deepseek-flash"]
