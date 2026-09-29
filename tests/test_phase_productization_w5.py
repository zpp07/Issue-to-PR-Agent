"""Offline startup checks: no model calls, containers or real browser needed."""
from __future__ import annotations

import errno
from types import SimpleNamespace

import service_http
from core import startup


def test_startup_checks_ready_without_leaking_model_key(tmp_path, monkeypatch, capsys):
    marker = "SECRET_KEY_VALUE_MUST_NOT_APPEAR"
    monkeypatch.setenv("DEEPSEEK_API_KEY", marker)
    monkeypatch.setenv("AGENT_SANDBOX_IMAGE", "issue-to-pr-runner:phase3")
    monkeypatch.setattr(startup.DockerSandbox, "_resolve_docker_binary", staticmethod(lambda _: "docker"))
    monkeypatch.setattr(startup.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(startup.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(startup.importlib.util, "find_spec", lambda _: object())

    checks = startup.startup_checks(tmp_path)
    startup.print_startup_checks(checks)
    output = capsys.readouterr().out
    assert marker not in output
    assert "[就绪] Docker" in output
    assert "[就绪] Runner 镜像" in output
    assert "[存在] 模型配置" in output


def test_startup_checks_missing_docker_image_and_unverified_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(startup.DockerSandbox, "_resolve_docker_binary", staticmethod(lambda _: "docker"))
    monkeypatch.setattr(startup.shutil, "which", lambda _: "docker")
    calls = []

    def probe(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0 if "version" in argv else 1)

    monkeypatch.setattr(startup.subprocess, "run", probe)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=DO_NOT_DISCLOSE\n", encoding="utf-8")
    checks = startup.startup_checks(tmp_path)
    startup.print_startup_checks(checks)
    output = capsys.readouterr().out
    assert "DO_NOT_DISCLOSE" not in output
    assert "[缺失] Runner 镜像" in output
    assert "[未验证] 模型配置" in output
    assert len(calls) == 2


def test_startup_checks_docker_missing_without_running_probe(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(startup.DockerSandbox, "_resolve_docker_binary", staticmethod(lambda _: "docker"))
    monkeypatch.setattr(startup.shutil, "which", lambda _: None)
    monkeypatch.setattr(startup.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("no probe expected")))
    checks = {item.name: item for item in startup.startup_checks(tmp_path)}
    assert checks["Docker"].status == "缺失"
    assert checks["Runner 镜像"].status == "未检查"
    assert checks["模型配置"].status == "缺失"


def test_browser_open_failure_only_prints_url(monkeypatch, capsys):
    url = "http://127.0.0.1:8765/"
    monkeypatch.setattr(service_http.webbrowser, "open", lambda *args, **kwargs: False)
    service_http._open_browser(url)
    assert url in capsys.readouterr().out

    def broken_browser(*args, **kwargs):
        raise RuntimeError("headless")

    monkeypatch.setattr(service_http.webbrowser, "open", broken_browser)
    service_http._open_browser(url)
    assert url in capsys.readouterr().out


def test_port_occupied_returns_clear_error_without_recovering_tasks(monkeypatch, capsys):
    monkeypatch.setattr(service_http, "startup_checks", lambda root: [])
    monkeypatch.setattr(service_http, "print_startup_checks", lambda checks: None)
    monkeypatch.setattr(service_http, "ThreadingHTTPServer", lambda *args: (_ for _ in ()).throw(
        OSError(errno.EADDRINUSE, "already in use")))
    monkeypatch.setattr(service_http.execution_manager, "recover_interrupted", lambda: (_ for _ in ()).throw(
        AssertionError("must not recover before bind")))
    assert service_http.main([]) == 2
    assert "127.0.0.1:8765 已被占用" in capsys.readouterr().err


def test_one_command_open_and_fake_mode_use_local_url(monkeypatch, capsys):
    class FakeServer:
        served = False
        closed = False

        def serve_forever(self):
            self.served = True

        def server_close(self):
            self.closed = True

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args
            assert daemon is True

        def start(self):
            self.target(*self.args)

    server = FakeServer()
    opened = []
    monkeypatch.setattr(service_http, "startup_checks", lambda root: [])
    monkeypatch.setattr(service_http, "print_startup_checks", lambda checks: None)
    monkeypatch.setattr(service_http, "ThreadingHTTPServer", lambda *args: server)
    monkeypatch.setattr(service_http.execution_manager, "recover_interrupted", lambda: None)
    monkeypatch.setattr(service_http.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(service_http.webbrowser, "open", lambda url, new: opened.append((url, new)) or True)
    monkeypatch.setattr(service_http, "FAKE_PUBLISHER_MODE", False)

    assert service_http.main(["--open", "--fake-publisher"]) == 0
    assert opened == [("http://127.0.0.1:8765/", 2)]
    assert server.served and server.closed
    assert service_http.FAKE_PUBLISHER_MODE is True
    assert "Issue-to-PR local UI: http://127.0.0.1:8765/" in capsys.readouterr().out


def test_browser_thread_failure_keeps_server_running(monkeypatch, capsys):
    server = SimpleNamespace(serve_forever=lambda: None, server_close=lambda: None)
    monkeypatch.setattr(service_http, "startup_checks", lambda root: [])
    monkeypatch.setattr(service_http, "print_startup_checks", lambda checks: None)
    monkeypatch.setattr(service_http, "ThreadingHTTPServer", lambda *args: server)
    monkeypatch.setattr(service_http.execution_manager, "recover_interrupted", lambda: None)

    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("no GUI thread")

    monkeypatch.setattr(service_http.threading, "Thread", BrokenThread)
    assert service_http.main(["--open"]) == 0
    assert "请手动访问 http://127.0.0.1:8765/" in capsys.readouterr().out
