"use strict";

const byId = (id) => document.getElementById(id);
const invite = new URLSearchParams(location.hash.slice(1)).get("invite");
const inviteHeaders = invite ? {"X-Trial-Invite": invite} : {};
if (invite) history.replaceState(null, "", location.pathname + location.search);
let pending;
let patch;

function status(message) {
  byId("status").textContent = message;
  byId("status").hidden = !message;
}

async function request(action, data) {
  const response = await fetch(`/trial/${action}`, {
    method: "POST", credentials: "omit", cache: "no-store",
    headers: {"Content-Type": "application/json", ...inviteHeaders},
    body: JSON.stringify({...data, api_key: byId("key").value}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "试用请求失败");
  return result;
}

function busy(value) {
  byId("plan").disabled = value;
  byId("execute").disabled = value;
}

byId("trial-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  busy(true);
  pending = undefined;
  patch = undefined;
  byId("approval").hidden = true;
  byId("result").hidden = true;
  status("正在生成计划…");
  try {
    pending = await request("plan", {preset: byId("preset").value});
    byId("plan-content").textContent = JSON.stringify(pending.plan, null, 2);
    byId("approval").hidden = false;
    status("计划已生成，请在 10 分钟内审阅并批准。");
  } catch (error) {
    byId("key").value = "";
    status(error.message);
  } finally {
    busy(false);
  }
});

byId("execute").addEventListener("click", async () => {
  if (!pending || !byId("trial-form").reportValidity()) return;
  busy(true);
  status("正在修复并运行 Docker 验证，可能需要几分钟…");
  try {
    const result = await request("execute", {ticket: pending.ticket, plan_sha256: pending.plan_sha256});
    patch = result.diff;
    byId("evidence").textContent = patch ? `测试通过 ${result.junit?.passed ?? "—"} 项。补丁 SHA-256 ${result.diff_sha256}`
      : `验证未通过。测试退出码 ${result.tests_exit_code ?? "—"}。`;
    byId("diff").textContent = patch || "";
    byId("download").hidden = !patch;
    byId("approval").hidden = true;
    byId("result").hidden = false;
    pending = undefined;
    status("");
  } catch (error) {
    status(error.message);
  } finally {
    byId("key").value = "";
    busy(false);
  }
});

byId("download").addEventListener("click", () => {
  if (!patch) return;
  const url = URL.createObjectURL(new Blob([patch + "\n"], {type: "text/x-diff"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = "python-addition.patch";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

window.addEventListener("pagehide", () => { byId("key").value = ""; });

async function openTrial() {
  try {
    const response = await fetch("/trial/access", {
      credentials: "omit", cache: "no-store", headers: inviteHeaders,
    });
    if (!response.ok) throw new Error("请重新打开有效的邀请链接。");
    byId("trial-form").hidden = false;
  } catch (error) {
    status(error.message);
  }
}

openTrial();
