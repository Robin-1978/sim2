import { request, write } from "./admin_api.js";
import { $, consoleNotice, describeRejection, ensureOperator, make, setConnection, setConsoleResult, setInstanceResult, setInteractionResult, setPlayerResult, showNotice, state, withPending } from "./admin_core.js";
import { parseDraft, renderDiagnostics, sourcePackageName, switchView } from "./admin_editor.js";
import { renderCharacters, renderDialogueTargets, renderEvolution, renderInstances, renderMap, renderMetrics, renderNpcDecisions, renderNpcDialogue, renderPlayerWorld, renderRuntime, renderTimeline } from "./admin_render.js";


export async function loadInstances() {
    try { renderInstances(await request("/api/v1/instances")); }
    catch (error) { showNotice(error.message || "无法读取实例列表"); }
  }


export async function activateInstance(dbPath) {
    if (!dbPath || dbPath === state.activeDb) return;
    if (!(await ensureOperator())) {
      $("instance-select").value = state.activeDb || "";
      return;
    }
    try {
      const result = await write("/api/v1/api/v1/instances/activate", { db_path: dbPath });
      if (result.status !== "accepted") throw new Error(result.message || "实例切换失败");
      window.location.reload();
    } catch (error) {
      $("instance-select").value = state.activeDb || "";
      throw error;
    }
  }


export async function startRuntime() {
    if (!(await ensureOperator())) return;
    const result = await write("/api/v1/runtime/start", { provider: $("runtime-provider").value || null, max_world_minutes: Number($("runtime-minutes").value || 1440), max_llm_calls: Number($("runtime-llm-calls").value || 120) });
    if (result.status !== "accepted") throw new Error(result.message || result.failed_check || "自动运行未启动");
    renderRuntime(result); await refreshRuntime(true);
  }


export async function runtimeCommand(command, body = {}) {
    if (!(await ensureOperator())) return;
    const result = await write(`/api/v1/runtime/${command}`, body);
    if (result.status === "rejected") throw new Error(result.message || result.failed_check || "运行命令被拒绝");
    renderRuntime(result.runtime || result); await refreshRuntime(false);
    return result;
  }


export async function toggleCausal(row, toggle, eventId) {
    const open = row.querySelector(".causal-box");
    if (open) { open.remove(); toggle.setAttribute("aria-expanded", "false"); toggle.textContent = "因果链"; return; }
    toggle.disabled = true;
    try {
      const result = await request(`/api/v1/events/${encodeURIComponent(eventId)}/related`);
      const box = make("div", "causal-box");
      const origin = result.event;
      if (origin) box.append(make("div", "causal-item", `本题：#${origin.world_minute ?? "—"} · ${origin.event_type || ""} · ${origin.actor || ""}${origin.summary ? ` — ${origin.summary}` : ""}`));
      for (const item of result.related || []) {
        const line = make("div", "causal-item");
        line.append(make("strong", "", `#${item.world_minute ?? "—"} · ${item.event_type || ""} · ${item.actor || ""}`));
        if (item.summary) line.append(document.createTextNode(` — ${item.summary}`));
        box.append(line);
      }
      if (!(result.related || []).length) box.append(make("div", "causal-item", "没有关联的因果事件。"));
      row.append(box);
      toggle.setAttribute("aria-expanded", "true");
      toggle.textContent = "收起";
    } catch (error) {
      row.append(make("div", "causal-item", `因果链读取失败：${error.message || "未知错误"}`));
    } finally {
      toggle.disabled = false;
    }
  }


export async function refreshRuntime(full = false) {
    try {
      const [status, observer, map, timeline, runtime] = await Promise.all([
        request("/api/v1/status"), request("/api/v1/observer/summary?limit=30"), request("/api/v1/map?observer=1"),
        request(`/api/v1/observer/timeline?limit=80&after=${full ? 0 : state.after}&ticks=${state.includeTicks ? 1 : 0}`),
        request("/api/v1/runtime"),
      ]);
      if (full) { state.events.clear(); state.after = 0; }
      for (const event of timeline.events || []) state.events.set(event.event_id, event);
      state.after = Math.max(state.after, Number(timeline.next_after_event_id || 0));
      state.lastObserver = observer;
      renderMetrics(status, observer, runtime); renderRuntime(runtime); renderCharacters(observer); renderDialogueTargets(observer); renderMap(map); renderTimeline();
      await Promise.all([renderEvolution(), renderNpcDialogue(), renderNpcDecisions()]);
      setConnection(true, "已连接"); showNotice(""); $("last-update").textContent = `updated ${new Date().toLocaleTimeString()}`;
    } catch (error) { setConnection(false); showNotice(error.message || "无法读取世界投影"); }
  }


export async function control(command) {
    try { await runtimeCommand(command, { reason: "web" }); }
    catch (error) { showNotice(error.message || "命令未提交"); }
  }


export async function enterPlayer() {
    const playerId = $("play-player-id")?.value.trim();
    const originId = $("play-player-origin")?.value;
    const name = $("play-player-name")?.value.trim();
    const resultBox = $("play-notice") || $("interaction-result");
    if (!playerId && !name) { setPlayerResult("新角色需要填写名字", false); return; }
    try {
      const payload = playerId
        ? { player_id: playerId, origin_id: originId || undefined, name: name || undefined }
        : { origin_id: originId || undefined, name };
      const result = await write("/api/v1/player/enter", payload);
      if (result.status !== "accepted") throw new Error(result.message || "玩家进入失败");
      const resolvedId = result.player_id || playerId;
      state.playerId = resolvedId;
      state.playerToken = result.session_token || state.playerToken;
      state.sessionToken = state.playerToken || state.observerToken;
      localStorage.setItem("sim2_player_id", resolvedId);
      if (result.session_token) localStorage.setItem("sim2_player_token", result.session_token);
      await loadPlayerView();
      switchView("play-view");
      if (result.resumed) {
        setPlayerResult(result.migrated ? `已恢复角色 ${result.name || resolvedId}，并补齐了模板能力与物资。` : `已恢复角色 ${result.name || resolvedId}。`, true);
      } else {
        setPlayerResult(`你已经以 ${result.name || resolvedId} 的身份进入世界。先看看周围，再选择下一步。`, true);
      }
    } catch (error) { setPlayerResult(error.message || "玩家进入失败", false); }
  }


export async function loadPlayerEntry() {
    try {
      const entry = await request("/api/v1/player/entry");
      state.playerEntry = entry;
      $("play-world-id").textContent = entry.world_id || "world";
      $("player-entry-copy").textContent = `从 ${entry.entry_location?.name || "入口地点"} 开始；已有玩家 ID 会恢复该角色并补齐模板能力。`;
      const select = $("play-player-origin"); select.replaceChildren();
      for (const origin of entry.origins || []) select.append(new Option(origin.id, origin.id));
      if (entry.origins?.length) { select.value = entry.origins[0].id; updateOriginPreview(); }
    } catch (error) { setPlayerResult(error.message || "无法读取世界入口", false); }
  }


export function updateOriginPreview() {
    const id = $("play-player-origin")?.value;
    const origin = (state.playerEntry?.origins || []).find((item) => item.id === id);
    $("origin-preview").textContent = origin?.goal ? `起始目标：${origin.goal}` : "选择一个出身后开始。";
  }


export async function loadPlayerView() {
    if (!state.playerId) return;
    try {
      const data = await request(`/api/v1/player/${encodeURIComponent(state.playerId)}/view`);
      if (data.status !== "ok") throw new Error(data.message || "无法读取玩家状态");
      state.playerData = data;
      renderPlayerWorld(data);
      $("player-setup").classList.add("hidden");
      $("player-world").classList.remove("hidden");
      $("play-world-time").textContent = data.world_time?.label || "—";
      return true;
    } catch (error) { setPlayerResult(error.message || "玩家状态暂不可用", false); return false; }
  }


export function setPlayerTradeResult(message, ok) {
    const notice = $("player-trade-result");
    if (!notice) return;
    notice.textContent = message || "";
    notice.className = `interaction-result ${ok ? "ok" : "error"}`;
  }


export async function actOnPlayerOffer(kind, offer, button) {
    if (!state.playerId) return;
    await withPending(button, async () => {
      try {
        let result;
        if (kind === "accept") {
          result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades/${encodeURIComponent(offer.offer_id)}/accept`, {});
        } else if (kind === "reject") {
          result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades/${encodeURIComponent(offer.offer_id)}/reject`, {});
        } else {
          const give = parseBundle($("player-trade-give")?.value || "");
          const want = parseBundle($("player-trade-want")?.value || "");
          result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades/${encodeURIComponent(offer.offer_id)}/counter`, { give, want });
        }
        if (result.status !== "accepted") throw new Error(describeRejection(result, "交易未提交"));
        setPlayerTradeResult(kind === "accept" ? "交易已完成。" : kind === "reject" ? "已拒绝该报价。" : "反报价已发送。", true);
        await loadPlayerView();
      } catch (error) { setPlayerTradeResult(error.message || "交易未提交", false); }
    });
  }


export async function submitPlayerTrade(button) {
    const peer = $("player-trade-target")?.value;
    if (!state.playerId || !peer) { setPlayerTradeResult("请选择交易对方", false); return; }
    await withPending(button, async () => {
      try {
        const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades`, {
          recipient_id: peer,
          give: parseBundle($("player-trade-give")?.value || ""),
          want: parseBundle($("player-trade-want")?.value || ""),
        });
        if (result.status !== "accepted") throw new Error(describeRejection(result, "交易未提交"));
        setPlayerTradeResult("报价已发送，等待对方回应。", true);
        await loadPlayerView();
      } catch (error) { setPlayerTradeResult(error.message || "交易未提交", false); }
    });
  }


export async function submitPlayerAction(action) {
    if (!state.playerId || state.advancing) return;
    setPlayerResult("行动已提交，Platform 正在校验…", true);
    try {
      const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/actions`, { behavior_ref: action.ref, parameters: action.parameters && !Array.isArray(action.parameters) ? action.parameters : {}, target: action.target && typeof action.target === "object" ? action.target : {}, reason: "Player selected this action from the web client.", confidence: 1 });
      if (result.status !== "accepted") throw new Error(result.message || result.errors?.join("; ") || "行动未被接受");
      await loadPlayerView();
      if (!state.runtime?.running) {
        await advancePlayerAction(`${action.choice || action.name || "行动"} 已开始，`);
      } else {
        setPlayerResult(`${action.choice || action.name || "行动"} 已开始；世界会自动推进。`, true);
      }
    } catch (error) { setPlayerResult(error.message || "行动未提交", false); }
  }


export function playerProvider() {
    return $("runtime-provider")?.value || state.runtime?.provider || null;
  }


export async function advancePlayerAction(prefix = "") {
    if (!state.playerId || state.advancing || state.runtime?.running) return;
    state.advancing = true;
    let llmErrors = 0;
    let stopReason = "";
    let npcDecisions = 0;
    try {
      const active = state.playerData?.active_task || {};
      if (!active.action && !active.bt) return;
      const remaining = Number(active.remaining_minutes || 0);
      const task = await runWaitTask(remaining + 1);
      await loadPlayerView();
      const worldTime = state.playerData?.world_time?.label || "";
      setPlayerResult(`${prefix}同步推进中${worldTime ? ` · ${worldTime}` : ""}${remaining ? ` · 还需约 ${remaining} 分钟` : ""}`, true);
      const report = task?.result?.report || {};
      llmErrors += Number(report.llm_errors || 0);
      npcDecisions += Number(report.decisions || 0);
      stopReason = report.stop_reason || stopReason;
      if (state.playerData?.active_task?.action || state.playerData?.active_task?.bt) {
        setPlayerResult("行动尚未完成，请再点“推进到完成”继续。", false);
      } else if (llmErrors) {
        setPlayerResult(`行动已执行完成；NPC 决策 ${npcDecisions} 次，其中 ${llmErrors} 次失败（${stopReason || "模型错误"}）。请检查模型配置。`, false);
      } else {
        const worldTime = state.playerData?.world_time?.label || "";
        setPlayerResult(`行动已执行完成${worldTime ? `，世界时间 ${worldTime}` : ""}；NPC 决策 ${npcDecisions} 次。`, true);
      }
    } catch (error) {
      setPlayerResult(error.message || "无法推进世界", false);
    } finally {
      state.advancing = false;
    }
  }


export async function runWaitTask(minutes) {
    const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/wait`, {
      minutes: Math.max(1, Math.min(480, Number(minutes || 1))),
      provider: playerProvider(),
    });
    if (!result.task?.task_id) throw new Error(result.message || "无法推进世界");
    let task = result.task;
    while (task && !["completed", "failed", "cancelled"].includes(task.state)) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      const response = await request(`/api/v1/player/${encodeURIComponent(state.playerId)}/tasks/${encodeURIComponent(task.task_id)}`);
      task = response.task || task;
    }
    if (task?.state === "failed") throw new Error(task.error || "无法推进世界");
    return task;
  }


export async function playerWait() {
    if (!state.playerId) return;
    if (state.runtime?.running) { setPlayerResult("世界正在自动推进，无需手动等待。", true); return; }
    try { await runWaitTask(1); await loadPlayerView(); setPlayerResult("世界向前推进了一分钟。", true); }
    catch (error) { setPlayerResult(error.message || "无法推进世界", false); }
  }


export async function sendPlayerDialogue(button) {
    const target = $("player-dialogue-target")?.value; const message = $("player-dialogue-message")?.value.trim();
    if (!state.playerId || !target || !message) { setPlayerResult("请选择附近对象并输入消息", false); return; }
    await withPending(button || $("player-send-dialogue"), async () => {
      try { const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/dialogue`, { listener_id: target, topic: "general", message }); if (result.status !== "accepted") throw new Error(describeRejection(result, "对话未提交")); $("player-dialogue-message").value = ""; setPlayerResult("对话已经进入世界事件流。", true); await loadPlayerView(); }
      catch (error) { setPlayerResult(error.message || "对话未提交", false); }
    });
  }


export async function sendDialogue(button) {
    const speakerId = $("dialogue-speaker").value; const listenerId = $("dialogue-listener").value; const message = $("dialogue-message").value.trim();
    if (!speakerId || !listenerId || !message) { setInteractionResult("请选择说话者、听者并输入消息", false); return; }
    await withPending(button || $("send-dialogue"), async () => {
      try { const result = await write("/api/v1/operator/dialogue", { speaker_id: speakerId, listener_id: listenerId, topic: $("dialogue-topic").value.trim() || "general", message }); setInteractionResult(result.status === "accepted" ? "对话已提交并写入 Platform 事件流" : JSON.stringify(result), result.status === "accepted"); if (result.status === "accepted") { $("dialogue-message").value = ""; await refreshRuntime(true); } }
      catch (error) { setInteractionResult(error.message || "对话未提交", false); }
    });
  }


export async function publishWorld(openAfter, button) {
    const parsed = parseDraft();
    if (parsed.error) {
      renderDiagnostics({ status: "rejected", errors: [parsed.error] });
      setInstanceResult(parsed.error, false);
      return;
    }
    const output = $("publish-output").value.trim();
    const instanceId = $("publish-instance").value.trim();
    if (!output || !instanceId) {
      setInstanceResult("请填写世界包目录和实例 ID", false);
      return;
    }
    if (sourcePackageName() && output === sourcePackageName()) {
      setInstanceResult("这是当前正在编辑的来源世界包，请换一个目录名，避免覆盖它", false);
      return;
    }
    if (!(await ensureOperator())) return;
    await withPending(button, async () => {
      setInstanceResult(openAfter ? "正在生成世界包并创建实例…" : "正在生成世界包…", true);
      try {
        const result = await write("/api/v1/authoring/build-world", {
          template: parsed.value,
          output,
          overwrite: $("publish-overwrite").checked,
          instance_id: openAfter ? instanceId : undefined,
          source_path: state.sourcePath || undefined,
        });
        renderDiagnostics(result);
        if (result.status !== "passed") {
          setInstanceResult((result.errors || result.diagnostics || ["生成失败"]).map((item) => (typeof item === "object" ? item.message : item)).join("；"), false);
          return;
        }
        if (!openAfter) {
          $("authoring-source").textContent = `已生成：${result.package}`;
          setInstanceResult(`世界包已写入 ${result.package}`, true);
          return;
        }
        const activation = await write("/api/v1/instances/activate", { instance_id: instanceId });
        if (activation.status !== "accepted") {
          setInstanceResult(`世界已生成，但切换失败：${activation.message || activation.failed_check || "未知原因"}`, false);
          return;
        }
        setInstanceResult(`已创建 ${instanceId}，正在打开…`, true);
        window.setTimeout(() => window.location.reload(), 600);
      } catch (error) {
        setInstanceResult(error.message || "发布失败", false);
      }
    });
  }


export function parseBundle(text) {
    const result = {};
    const raw = String(text || "").trim();
    if (!raw) return result;
    for (const token of raw.split(",")) {
      const item = token.trim();
      if (!item) continue;
      const [name, quantity] = item.split("=").map((part) => part.trim());
      const value = Number(quantity);
      if (!name || !Number.isFinite(value) || value <= 0) throw new Error("交易数量格式应为 item=qty，例如 bread=2");
      result[name] = value;
    }
    return result;
  }


export async function runConsole(label, fn, { operator = true } = {}) {
    if (operator && !(await ensureOperator())) return;
    setConsoleResult(`${label} …`);
    consoleNotice("", true);
    try {
      const result = await fn();
      setConsoleResult(result);
      return result;
    } catch (error) {
      const message = error.message || "命令失败";
      setConsoleResult({ status: "error", message, code: error.code || null });
      consoleNotice(message, false);
      return null;
    }
  }


export async function downloadSnapshot(snapshotId, button) {
    if (!snapshotId) return;
    if (!(await ensureOperator())) return;
    await withPending(button, async () => {
      const sessionToken = state.operatorToken || state.playerToken || state.observerToken || state.sessionToken;
      const response = await fetch(`/api/v1/operator/snapshots/${encodeURIComponent(snapshotId)}/download`, {
        headers: { ...(sessionToken ? { "x-sim2-session": sessionToken } : {}) },
      });
      if (!response.ok) {
        let message = `下载失败 (${response.status})`;
        try {
          const value = await response.json();
          message = value.message || message;
        } catch (error) { /* non-JSON error body */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${snapshotId}.sqlite3`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    }).catch((error) => consoleNotice(error.message || "下载失败", false));
  }


export async function refreshConsoleInstances() {
    const result = await runConsole("刷新实例与快照", async () => {
      const [instances, snapshots] = await Promise.all([request("/api/v1/instances"), request("/api/v1/operator/snapshots")]);
      return { instances: instances.instances, snapshots: snapshots.snapshots };
    });
    if (result) {
      const box = $("console-instances");
      if (box) {
        box.replaceChildren();
        for (const item of result.instances || []) box.append(make("div", "", `${item.instance_id} · ${item.world_id || ""} · minute ${item.world_minute ?? "—"}${item.active ? " · active" : ""}`));
        for (const item of result.snapshots || []) {
          const row = make("div", "snapshot-row");
          row.append(make("span", "", `snapshot ${item.snapshot_id} · ${(item.size / 1024).toFixed(0)} KB`));
          const download = make("button", "text-button", "下载");
          download.type = "button";
          download.addEventListener("click", () => downloadSnapshot(item.snapshot_id, download));
          row.append(download);
          box.append(row);
        }
        if (!((result.instances || []).length || (result.snapshots || []).length)) box.append(make("div", "empty-state", "暂无实例或快照"));
      }
      await loadInstances();
    }
  }
