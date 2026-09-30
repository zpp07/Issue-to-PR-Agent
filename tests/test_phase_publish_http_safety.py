"""Offline guards for the opt-in real publisher; no GitHub or model requests."""
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import service_http


@pytest.mark.parametrize("overrides", [
    {"Host": "attacker.invalid:8765"},
    {"Host": "127.0.0.1:8080"},
    {"Origin": "https://attacker.invalid"},
    {"Origin": "null"},
    {"Origin": "http://localhost:8765"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Content-Type": "text/plain"},
    {"Content-Type": "application/x-www-form-urlencoded"},
    {"Content-Type": ""},
])
def test_cross_origin_and_form_publish_requests_never_reach_publisher(monkeypatch, overrides):
    headers = {"Host": "127.0.0.1:8765", "Content-Type": "application/json", **overrides}
    monkeypatch.setattr(service_http, "REAL_PUBLISHER_MODE", True)
    monkeypatch.setattr(service_http.GitHubAPI, "from_env", lambda: pytest.fail("must not access credentials"))
    handler = SimpleNamespace(
        headers=headers, path="/tasks/task-1/draft-pr/publish",
        _body=lambda: {"subject_hash": "approved", "confirmation": "create-draft-pr"},
        _reply=lambda status, payload: (status, payload),
    )
    status, result = service_http.Handler.do_POST(handler)
    assert status == 403
    assert result["detail"]


@pytest.mark.parametrize("headers,port", [
    ({"Host": "127.0.0.1:8765", "Content-Type": "application/json"}, 8765),
    ({"Host": "localhost:8765", "Origin": "http://localhost:8765",
      "Content-Type": "application/json; charset=utf-8", "Sec-Fetch-Site": "same-origin"}, 8765),
    ({"Host": "127.0.0.1:12345", "Origin": "http://127.0.0.1:12345",
      "Content-Type": "application/json"}, 12345),
])
def test_local_json_publish_header_contract(headers, port):
    assert service_http._publish_request_error(headers, port) is None


def test_frontend_cannot_reenable_or_repeat_publication_while_busy():
    node = shutil.which("node")
    if node is None:
        pytest.skip("optional Node runtime for frontend behavior check")
    script = (Path(service_http.WEB_DIR) / "app.js").read_text(encoding="utf-8")
    script = script.rsplit("initialize();", 1)[0]
    harness = r'''
const assert = require("node:assert/strict");
const nodes = new Map();
global.document = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {
      disabled: false, checked: false, textContent: "", listeners: {},
      addEventListener(name, handler) { this.listeners[name] = handler; },
    });
    return nodes.get(id);
  },
  querySelectorAll() { return []; },
};
'''
    checks = r'''
wireEvents();
state.realPublisher = true;
state.task = {id: "task-1", status: "ready_to_publish"};
state.artifacts.draft_pr_request = {approval_subject_sha256: "approved"};
$("real-publish-confirm").checked = true;
let calls = 0;
let rejectRequest;
api = () => { calls += 1; return new Promise((_, reject) => { rejectRequest = reject; }); };
report = () => {};
(async () => {
  const pending = publishDraft();
  assert.equal(state.publishing, true);
  $("real-publish-confirm").listeners.change();
  assert.equal($("confirm-real-publish").disabled, true);
  await publishDraft();
  assert.equal(calls, 1);
  let cancelled = false;
  $("real-publish-dialog").listeners.cancel({preventDefault() { cancelled = true; }});
  assert.equal(cancelled, true);
  rejectRequest(new Error("offline failure"));
  await pending;
  assert.equal(state.publishing, false);
  assert.equal($("real-publish-confirm").disabled, false);
  assert.equal($("confirm-real-publish").disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    # Pass the full frontend through stdin, below the Windows argv size limit.
    result = subprocess.run([node, "-"], input=harness + script + checks,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
