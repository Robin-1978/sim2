import { request, write } from "./api.js";
import { renderAll } from "./app.js";
import { $, OPERATOR_KEY, PLAYER_KEY, describeError, el, setOnline, state, storageGet, storageSet, toast, withPending, worldIsOpen } from "./core.js";
import { openActionDialog, renderTask } from "./render_actions.js";
import { maybeCelebrateEvents } from "./celebration.js";
import { openCreatePanel, renderEntry, renderEntryInstances } from "./render_entry.js";
import { SKIP_EVENTS, bundleText, closeCharacter, closePlayerSheet, nearbyParticipants, parseBundle, personName, renderFeed, renderMap, showMapDialog, updateCharacterSheet } from "./render_panels.js";


export async function loadEntryContext() {
    const results = await Promise.allSettled([
      request("/api/v1/world"),
      request("/api/v1/player/entry"),
      request("/api/v1/instances"),
      request("/api/v1/runtime"),
      request("/api/v1/worlds"),
    ]);
    const [world, entry, instances, runtime, worlds] = results;
    if (world.status === "fulfilled") state.world = world.value;
    if (entry.status === "fulfilled") state.entry = entry.value;
    if (instances.status === "fulfilled") {
      state.instances = Array.isArray(instances.value.instances) ? instances.value.instances : [];
      state.activeDb = instances.value.active_db || "";
    }
    if (runtime.status === "fulfilled") state.runtime = runtime.value;
    if (worlds.status === "fulfilled") {
      state.worlds = Array.isArray(worlds.value.worlds) ? worlds.value.worlds : [];
    }
    renderEntry();
    renderRuntimeStatus();
  }


export function openWorldHub(message = "") {
    $("game").classList.add("hidden");
    $("mobile-action-bar").classList.add("hidden");
    closePlayerSheet();
    closeCharacter();
    $("entry-screen").classList.remove("hidden");
    $("entry-error").textContent = message || "";
    $("entry-return").classList.toggle("hidden", !state.playerId);
    renderEntry();
  }


export function showEntry(message = "") {
    state.playerId = null;
    state.playerToken = null;
    state.view = null;
    storageSet(PLAYER_KEY, null);
    openWorldHub(message);
  }


export function returnToGame() {
    if (!state.playerId) {
      toast("先选择一个角色进入世界。", "error");
      return;
    }
    enterGame();
    if (!state.view) loadView().catch(() => {});
  }


export function enterGame() {
    $("entry-screen").classList.add("hidden");
    $("game").classList.remove("hidden");
    $("entry-error").textContent = "";
  }


export async function submitEntry(button, forcedPlayerId = null) {
    const resumingId = String(forcedPlayerId || "").trim();
    const name = resumingId ? "" : String($("entry-name").value || "").trim();
    const originId = resumingId ? "" : state.selectedOrigin;
    $("entry-error").textContent = "";
    if (!resumingId && !name) {
      $("entry-error").textContent = "请输入角色名。";
      return;
    }
    await withPending(button, async () => {
      try {
        const payload = resumingId
          ? { player_id: resumingId }
          : { origin_id: originId, name };
        const result = await write("/api/v1/player/enter", payload);
        if (result.status !== "accepted") throw new Error(result.message || "无法进入世界");
        state.playerId = result.player_id || resumingId;
        state.playerName = result.name || name || state.playerId;
        state.playerToken = result.session_token || null;
        restartStream();
        state.entry = {
          ...(state.entry || {}),
          players: [{ id: state.playerId, name: state.playerName, status: "idle" }],
          can_create_player: false,
        };
        storageSet(PLAYER_KEY, { id: state.playerId, token: state.playerToken, name: state.playerName });
        enterGame();
        toast(result.resumed ? `已恢复角色 ${state.playerName}。` : `欢迎来到${state.world?.title || "这个世界"}，${state.playerName}。`, "ok");
        await Promise.all([loadView(), loadRuntime()]);
        await Promise.allSettled([loadFeed(), loadDialogue(), loadTrades()]);
      } catch (error) {
        const message = error?.code === "player_limit"
          ? "这个实例已经有一个玩家角色。请回到世界大厅点击“继续游戏”。"
          : describeError(error, "无法进入世界");
        $("entry-error").textContent = message;
        toast(message, "error");
      }
    });
  }


export async function activateWorldInstance(instanceId, button = null) {
    if (!instanceId) return;
    if (!state.operatorToken) {
      openCreatePanel();
      $("entry-admin").open = true;
      $("new-instance-error").textContent = "切换已有世界需要先在下方“管理权限”解锁。";
      return;
    }
    await withPending(button, async () => {
      try {
        const result = await write("/api/v1/instances/activate", { instance_id: instanceId });
        if (result.status !== "accepted") throw new Error(result.message || "切换失败");
        storageSet(PLAYER_KEY, null);
        window.location.reload();
      } catch (error) {
        const message = describeError(error, "切换世界失败");
        openCreatePanel();
        $("new-instance-error").textContent = message;
        toast(message, "error");
      }
    });
  }


export async function deleteWorldInstance(instanceId, button = null) {
    if (!instanceId) return;
    if (!state.operatorToken) {
      openCreatePanel();
      $("entry-admin").open = true;
      $("new-instance-error").textContent = "删除存档需要先在“管理权限”解锁。";
      return;
    }
    if (!window.confirm(`删除存档 ${instanceId}？这会同时删除该实例的存档和快照，无法撤销。`)) return;
    await withPending(button, async () => {
      try {
        const result = await write("/api/v1/instances/delete", { instance_id: instanceId });
        if (result.status !== "accepted") throw new Error(result.message || "删除失败");
        toast(`已删除存档 ${instanceId}。`, "ok");
        await loadEntryContext();
      } catch (error) {
        toast(describeError(error, "删除存档失败"), "error");
      }
    });
  }


export async function createWorldInstance(button) {
    const packageId = state.selectedWorldPackage;
    const instanceId = String($("new-instance-id").value || "").trim();
    $("new-instance-error").textContent = "";
    if (!packageId) return ($("new-instance-error").textContent = "请选择一个世界模板。");
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(instanceId)) {
      $("new-instance-error").textContent = "实例 ID 需要 1-64 个字母、数字、下划线或连字符，并且以字母或数字开头。";
      return;
    }
    await withPending(button, async () => {
      try {
        const result = await write("/api/v1/instances/init", { package_id: packageId, instance_id: instanceId, activate: true });
        if (!["passed", "accepted"].includes(String(result.status || ""))) {
          throw new Error(result.message || (result.errors || []).join("；") || "创建失败");
        }
        if (result.activation && result.activation.status !== "accepted") {
          throw new Error(result.activation.message || "世界已创建，但自动打开失败。请从世界实例列表重试。");
        }
        toast(`世界 ${instanceId} 已创建，正在打开…`, "ok");
        state.activeDb = result.db_path || state.activeDb;
        storageSet(PLAYER_KEY, null);
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        if (error.status === 403) {
          openCreatePanel();
          $("entry-admin").open = true;
          $("new-instance-error").textContent = "创建成功需要一个已解锁的管理会话。请展开“管理权限”填入启动服务器时显示的口令。";
        } else {
          const message = describeError(error, "创建世界失败");
          $("new-instance-error").textContent = message;
          toast(message, "error");
        }
      }
    });
  }


export async function restorePlayer() {
    const saved = storageGet(PLAYER_KEY, null);
    if (!saved?.id || !saved?.token || !worldIsOpen()) return false;
    state.playerId = saved.id;
    state.playerName = saved.name || saved.id;
    state.playerToken = saved.token;
    try {
      await loadView();
      enterGame();
      await Promise.allSettled([loadFeed(), loadDialogue(), loadTrades(), loadRuntime()]);
      return true;
    } catch (error) {
      state.playerId = null;
      state.playerToken = null;
      state.view = null;
      storageSet(PLAYER_KEY, null);
      return false;
    }
  }


  /* ---------- View ---------- */

export function isSessionError(error) {
    return Number(error?.status) === 401 || Number(error?.status) === 403;
  }


export async function recoverPlayerSession() {
    const saved = storageGet(PLAYER_KEY, null);
    if (!saved?.id) return false;
    try {
      // A server restart drops in-memory sessions; the player row is durable.
      // Re-enter by id to get a fresh session without creating a new character.
      const result = await write("/api/v1/player/enter", { player_id: saved.id });
      if (result.status !== "accepted" || !result.session_token) return false;
      state.playerId = String(result.player_id || saved.id);
      state.playerName = String(result.name || saved.name || state.playerId);
      state.playerToken = String(result.session_token);
      storageSet(PLAYER_KEY, { id: state.playerId, token: state.playerToken, name: state.playerName });
      restartStream();
      return true;
    } catch (error) {
      return false;
    }
  }


function expirePlayerSession() {
    state.playerId = null;
    state.playerToken = null;
    state.view = null;
    storageSet(PLAYER_KEY, null);
    showEntry("角色会话已失效，请重新进入。");
  }


async function handleWaitFailure(error, fallback) {
    if (!isSessionError(error)) {
      toast(describeError(error, fallback), "error");
      return;
    }
    if (await recoverPlayerSession()) {
      toast("角色会话已刷新；刚才的推进任务已中断，请再点一次。", "error", 7000);
      await loadView().catch(() => {});
      return;
    }
    expirePlayerSession();
  }


export function startStream() {
    if (state.stream || typeof EventSource === "undefined") return;
    let source;
    let lastViewRefresh = 0;
    try {
      source = new EventSource(`/api/v1/stream?after=${state.streamCursor || 0}&session_token=${encodeURIComponent(state.playerToken || state.operatorToken || "")}`);
    } catch (error) {
      return;
    }
    state.stream = source;
    source.addEventListener("ready", (event) => {
      state.streamConnected = true;
      state.streamFailures = 0;
      try { state.streamCursor = Math.max(Number(state.streamCursor || 0), Number(JSON.parse(event.data || "{}").cursor || 0)); } catch (error) { /* ignore */ }
    });
    source.addEventListener("player.view.changed", (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data || "{}"); } catch (error) { return; }
      const revision = Number(payload.revision || 0);
      if (revision && revision <= Number(state.view?.revision || 0)) return;
      if (document.hidden) return;
      const now = Date.now();
      if (now - lastViewRefresh < (state.advancing ? 1000 : 250)) return;
      lastViewRefresh = now;
      loadView().catch(() => {});
    });
    source.addEventListener("task.progress", (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data || "{}"); } catch (error) { return; }
      if (payload.data?.player_id && payload.data.player_id !== state.playerId) return;
      state.taskProgress = payload.data?.task || null;
      renderTask();
    });
    source.addEventListener("task.completed", (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data || "{}"); } catch (error) { return; }
      if (payload.data?.player_id && payload.data.player_id !== state.playerId) return;
      const waiter = state.taskWaiters.get(payload.data?.task?.task_id);
      if (waiter) {
        state.taskWaiters.delete(payload.data.task.task_id);
        waiter(payload.data.task);
      }
    });
    source.addEventListener("dialogue", (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data || "{}"); } catch (error) { return; }
      const exchanges = Array.isArray(payload.data?.exchanges) ? payload.data.exchanges : [];
      if (!exchanges.some((item) => item.speaker_id === state.playerId || item.listener_id === state.playerId)) return;
      const replyArrived = exchanges.some(
        (item) => item.listener_id === state.playerId && item.speaker_id !== state.playerId,
      );
      if (replyArrived) {
        state.dialogueTyping = false;
        if (state.dialogueReplyTimer) window.clearTimeout(state.dialogueReplyTimer);
        state.dialogueReplyTimer = null;
      }
      loadDialogue(state.selectedCharacter?.id || state.dialogueTypingTarget || "").catch(() => {});
    });
    source.addEventListener("dialogue.append", (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data || "{}"); } catch (error) { return; }
      if (payload.data?.player_id && payload.data.player_id !== state.playerId) return;
      state.dialogueTyping = false;
      if (state.dialogueReplyTimer) window.clearTimeout(state.dialogueReplyTimer);
      state.dialogueReplyTimer = null;
      if (payload.data?.status === "failed") toast("NPC 回复暂不可用。", "error");
      loadDialogue(state.selectedCharacter?.id || state.dialogueTypingTarget || "").catch(() => {});
    });
    source.addEventListener("runtime", (event) => {
      try {
        state.runtime = JSON.parse(event.data || "{}").data || state.runtime;
        renderRuntimeStatus();
      } catch (error) { /* polling fallback */ }
    });
    source.addEventListener("feed.append", (event) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        const events = Array.isArray(payload.data?.events) ? payload.data.events : [];
        if (typeof mergeFeedEvents === "function") mergeFeedEvents(events);
        maybeCelebrateEvents(events);
      } catch (error) { /* polling fallback */ }
    });
    source.addEventListener("trade.changed", () => {
      if (!document.hidden) loadTrades().catch(() => {});
    });
    source.addEventListener("dialogue.append", () => {
      if (!document.hidden) loadDialogue().catch(() => {});
    });
    source.addEventListener("chapter.changed", () => {
      if (!document.hidden && !state.advancing) loadView().catch(() => {});
    });
    source.onerror = () => {
      state.streamConnected = false;
      state.streamFailures = (state.streamFailures || 0) + 1;
      if (state.streamFailures >= 3 && state.stream) {
        try { state.stream.close(); } catch (error) { /* ignore */ }
        state.stream = null;
      }
    };
  }


export function restartStream() {
    if (state.stream) {
      try { state.stream.close(); } catch (error) { /* ignore */ }
      state.stream = null;
    }
    state.streamConnected = false;
    state.streamFailures = 0;
    startStream();
  }


export async function loadView() {
    if (!state.playerId) return null;
    let view;
    try {
      view = await request(`/api/v1/player/${encodeURIComponent(state.playerId)}/view`);
    } catch (error) {
      if (!isSessionError(error)) throw error;
      if (!await recoverPlayerSession()) {
        expirePlayerSession();
        throw error;
      }
      view = await request(`/api/v1/player/${encodeURIComponent(state.playerId)}/view`);
    }
    if (view.status !== "ok") throw new Error(view.message || "无法读取角色状态");
    state.view = view;
    if (view.world_time) {
      const offset = (Number(view.world_time.day || 1) - 1) * 1440 + Number(view.world_time.minute_of_day || 0) - Number(view.world_time.minute || 0);
      if (Number.isFinite(offset)) state.clockOffset = offset;
    }
    if (view.player?.name) {
      state.playerName = String(view.player.name);
      const saved = storageGet(PLAYER_KEY, null);
      if (saved?.token) storageSet(PLAYER_KEY, { ...saved, id: state.playerId, name: state.playerName });
    }
    syncTaskProgress(view.active_task || {});
    renderAll();
    return view;
  }


export function syncTaskProgress(task) {
    const active = Boolean(task?.bt || task?.action);
    const key = active ? `${task.bt || ""}|${task.action || ""}` : "";
    const remaining = Number(task?.remaining_minutes || 0);
    if (!active) {
      state.taskKey = "";
      state.taskTotal = 0;
      return;
    }
    if (key !== state.taskKey) {
      state.taskKey = key;
      state.taskTotal = Math.max(1, remaining);
    }
    if (remaining > state.taskTotal) state.taskTotal = remaining;
  }


export async function beginAction(action, button) {
    if (state.busy) return;
    let parameters = action.parameters && !Array.isArray(action.parameters) ? { ...action.parameters } : {};
    let target = action.target && typeof action.target === "object" ? action.target : {};
    if (action.target_required || (Array.isArray(action.parameters) && action.parameters.length)) {
      const choice = await openActionDialog(action);
      if (!choice) return;
      parameters = { ...parameters, ...(choice.parameters || {}) };
      target = choice.target || target;
    }
    await submitAction(action, { parameters, target }, button);
  }


export async function submitAction(action, extra = {}, button = null) {
    if (!state.playerId || state.busy) return;
    state.busy = true;
    const label = action.choice || action.name || "行动";
    await withPending(button, async () => {
      try {
        const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/actions`, {
          behavior_ref: action.ref,
          parameters: extra.parameters || {},
          target: extra.target || {},
          reason: "玩家从游戏界面选择了这个行动。",
          confidence: 1,
        });
        if (result.status !== "accepted") throw new Error(result.message || "行动未被接受");
        state.taskKey = "";
        state.taskTotal = Math.max(1, Number(result.remaining_minutes || parseMinutes(action.time) || 1));
        toast(`已开始：${label}`, "ok");
        await loadView();
        if (state.view?.active_task && window.matchMedia("(min-width: 861px)").matches) {
          $("task-panel").scrollIntoView({ block: "center", behavior: "smooth" });
        }
        if (!state.runtime?.running) {
          await advanceTask(`${label}：`);
        }
      } catch (error) {
        toast(describeError(error, "行动未提交"), "error", 6500);
      } finally {
        state.busy = false;
        renderTask();
      }
    });
  }


export function parseMinutes(text) {
    const match = String(text || "").match(/(\d+)\s*(?:minutes?|分钟)/i);
    return match ? Number(match[1]) : 0;
  }


export async function waitPath() {
    return `/api/v1/player/${encodeURIComponent(state.playerId)}/wait`;
  }


export async function taskPath(taskId) {
    return `/api/v1/player/${encodeURIComponent(state.playerId)}/tasks/${encodeURIComponent(taskId)}`;
  }


export async function startWaitTask(minutes) {
    const result = await write(await waitPath(), {
      minutes: Math.max(1, Math.min(480, Number(minutes || 1))),
      provider: selectedProvider(),
    });
    if (!result.task?.task_id) throw new Error(result.message || "无法推进世界");
    return result.task;
  }


export async function awaitWaitTask(task, { onProgress } = {}) {
    let current = task;
    while (current && !["completed", "failed", "cancelled"].includes(current.state)) {
      onProgress?.(current);
      await new Promise((resolve) => window.setTimeout(resolve, state.streamConnected ? 500 : 1000));
      try {
        const response = await request(await taskPath(current.task_id));
        current = response.task || current;
      } catch (error) {
        const status = Number(error?.status || 0);
        if (status >= 400 && status < 500) throw error;
      }
    }
    onProgress?.(current);
    if (current?.state === "failed") throw new Error(current.error || "推进世界失败");
    return current;
  }


export async function advanceTask(prefix = "") {
    if (!state.playerId || state.advancing) return;
    state.advancing = true;
    renderTask();
    try {
      const active = state.view?.active_task || {};
      if (!active.bt && !active.action) return;
      const remaining = Number(active.remaining_minutes || 0);
      const started = await startWaitTask(remaining + 1);
      const task = await awaitWaitTask(started, {
        onProgress: (progress) => {
          state.taskProgress = progress;
          renderTask();
        },
      });
      await loadView();
      const report = task?.result?.report || {};
      const decisions = Number(report.decisions || 0);
      const errors = Number(report.llm_errors || 0);
      const stopReason = String(report.stop_reason || "");
      const stillActive = Boolean(state.view?.active_task?.bt || state.view?.active_task?.action);
      const worldTime = state.view?.world_time?.label ? `${state.view.world_time.day} 日 ${state.view.world_time.time_of_day}` : "";
      if (stillActive) {
        toast("行动还没有结束；可以再点一次“推进到完成”。", "error", 6000);
      } else if (errors) {
        toast(`行动完成，但同步推进中有 ${errors} 次 NPC 决策失败${stopReason ? `（${stopReason}）` : ""}；这 ${decisions} 次决策里请检查模型配置。`, "error", 8000);
      } else {
        toast(`${prefix}行动完成${worldTime ? ` · ${worldTime}` : ""}${decisions ? ` · NPC 决策 ${decisions} 次` : ""}`, "ok", 6000);
      }
      await Promise.allSettled([loadFeed(), loadTrades()]);
    } catch (error) {
      await handleWaitFailure(error, "无法推进世界");
    } finally {
      state.taskProgress = null;
      state.advancing = false;
      renderTask();
    }
  }


export async function waitOneMinute(button) {
    if (!state.playerId || state.advancing) return;
    await withPending(button, async () => {
      try {
        const started = await startWaitTask(1);
        const task = await awaitWaitTask(started);
        await loadView();
        const decisions = Number(task?.result?.report?.decisions || 0);
        toast(`世界向前推进了一分钟${decisions ? `，NPC 决策 ${decisions} 次` : ""}。`, "ok");
        await Promise.allSettled([loadFeed(), loadDialogue()]);
      } catch (error) {
        await handleWaitFailure(error, "无法推进世界");
      }
    });
  }


export async function waitFor(minutes, button) {
    if (!state.playerId || state.advancing) return;
    const amount = Math.max(1, Math.min(480, Number(minutes || 60)));
    const bar = $("time-progress");
    const fill = bar?.firstElementChild;
    state.advancing = true;
    bar?.classList.remove("hidden");
    try {
      await withPending(button, async () => {
        try {
          const started = await startWaitTask(amount);
          const task = await awaitWaitTask(started, {
            onProgress: (progress) => {
              state.taskProgress = progress;
              const done = Number(progress?.completed_minutes || 0);
              const total = Math.max(1, Number(progress?.requested_minutes || amount));
              if (fill) fill.style.width = `${Math.min(100, (done / total) * 100)}%`;
            },
          });
          await loadView();
          const report = task?.result?.report || {};
          const advanced = Math.max(0, Number(report.end_minute || 0) - Number(report.start_minute || 0)) || Number(task?.completed_minutes || 0) || amount;
          const decisions = Number(report.decisions || 0);
          toast(`世界推进了 ${advanced} 分钟${decisions ? `，居民做了 ${decisions} 次决策` : ""}。`, "ok");
          await Promise.allSettled([loadFeed(), loadDialogue()]);
        } catch (error) {
          await handleWaitFailure(error, "无法推进世界");
        }
      });
    } finally {
      state.advancing = false;
      bar?.classList.add("hidden");
      if (fill) fill.style.width = "0%";
    }
  }


export function selectedProvider() {
    const value = String($("settings-provider")?.value || "").trim();
    return value || state.runtime?.provider || null;
  }


  /* ---------- Goals / world properties ---------- */

export function actionForRef(ref) {
    const actions = Array.isArray(state.view?.actions) ? state.view.actions : [];
    return actions.find((action) => action.ref === ref) || null;
  }


export async function loadDialogue(characterId = state.selectedCharacter?.id || "") {
    const root = $("dialogue-list");
    if (!root) return;
    try {
      const response = await request("/api/v1/observer/dialogue?limit=80");
      const exchanges = (response.exchanges || [])
        .filter((item) => item.speaker_id === state.playerId || item.listener_id === state.playerId)
        .filter((item) => !characterId || item.speaker_id === characterId || item.listener_id === characterId)
        .slice(-30);
      root.replaceChildren();
      const typing = state.dialogueTyping && (!characterId || state.dialogueTypingTarget === characterId);
      if (!exchanges.length && !typing) {
        const who = characterId ? personName(characterId) : "任何人";
        root.append(el("p", "dialogue-empty", `还没有和 ${who} 的对话。先开口吧。`));
        return;
      }
      for (const item of exchanges) {
        const mine = item.speaker_id === state.playerId;
        const bubble = el("div", `dialogue-bubble ${mine ? "mine" : "theirs"}`);
        const speaker = mine ? "你" : (item.speaker || personName(item.speaker_id));
        bubble.append(el("strong", "", speaker));
        if (item.topic && item.topic !== "general") bubble.append(el("span", "dialogue-topic", item.topic));
        bubble.append(el("p", "", item.message || ""));
        root.append(bubble);
      }
      if (typing) {
        const bubble = el("div", "dialogue-bubble theirs typing");
        bubble.append(el("strong", "", characterId ? personName(characterId) : "对方"));
        bubble.append(el("p", "", "正在输入…"));
        root.append(bubble);
      }
      root.scrollTop = root.scrollHeight;
    } catch (error) {
      root.replaceChildren(el("p", "dialogue-empty", "对话记录暂不可用。"));
    }
  }


export async function sendDialogue(form) {
    const target = $("talk-target")?.value;
    const message = String($("talk-message")?.value || "").trim();
    if (!target) return toast("附近没有可交谈的人。", "error");
    if (!message) return toast("先说点什么。", "error");
    const button = $("talk-send");
    await withPending(button, async () => {
      state.dialogueTyping = true;
      state.dialogueTypingTarget = target;
      if (state.dialogueReplyTimer) window.clearTimeout(state.dialogueReplyTimer);
      state.dialogueReplyTimer = window.setTimeout(() => {
        if (!state.dialogueTyping) return;
        state.dialogueTyping = false;
        state.dialogueReplyTimer = null;
        loadDialogue(target).catch(() => {});
        toast("NPC 回复暂不可用。", "error");
      }, 30000);
      await loadDialogue(target);
      try {
        const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/dialogue`, {
          listener_id: target,
          topic: "general",
          message,
          provider: selectedProvider() || undefined,
        });
        if (result.status !== "accepted") throw new Error(result.message || "对话未发送");
        $("talk-message").value = "";
        const reply = result.reply || {};
        if (reply.status === "rejected") {
          state.dialogueTyping = false;
          if (state.dialogueReplyTimer) window.clearTimeout(state.dialogueReplyTimer);
          state.dialogueReplyTimer = null;
        }
        toast(reply.status === "pending" ? "消息已发送，对方正在回复…" : "对话已发送。", reply.status === "rejected" ? "error" : "ok");
        await Promise.allSettled([loadView(), loadFeed()]);
        const selected = nearbyParticipants().find((item) => item.id === target);
        if (selected && state.selectedCharacter?.id === target) updateCharacterSheet(selected);
        await loadDialogue(target);
      } catch (error) {
        state.dialogueTyping = false;
        if (state.dialogueReplyTimer) window.clearTimeout(state.dialogueReplyTimer);
        state.dialogueReplyTimer = null;
        await loadDialogue(target).catch(() => {});
        toast(describeError(error, "对话未发送"), "error");
      }
    });
  }


export async function loadTrades() {
    const root = $("trade-list");
    if (!root || !state.playerId) return;
    try {
      const response = await request(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades?include_closed=0`);
      const offers = (response.offers || []).filter((item) => item.status === "open").slice(0, 12);
      root.replaceChildren();
      if (!offers.length) {
        root.append(el("p", "trade-empty", "暂无待处理的交易报价。"));
        return;
      }
      for (const offer of offers) {
        const incoming = offer.recipient_id === state.playerId;
        const peerId = incoming ? offer.offerer_id : offer.recipient_id;
        const peer = (state.view?.nearby_participants || []).find((item) => item.id === peerId);
        const card = el("article", "trade-card");
        card.append(el("strong", "", incoming ? `来自 ${peer?.name || peerId}` : `发给 ${peer?.name || peerId}（等待回应）`));
        card.append(el("small", "", incoming
          ? `你得到 ${bundleText(offer.give)} · 你付出 ${bundleText(offer.want)} · 第 ${offer.round || 1} 轮`
          : `你付出 ${bundleText(offer.give)} · 你想要 ${bundleText(offer.want)} · 第 ${offer.round || 1} 轮`));
        if (incoming) {
          const actions = el("div", "trade-card-actions");
          const accept = el("button", "button ghost", "接受");
          accept.type = "button";
          accept.addEventListener("click", () => answerOffer("accept", offer, accept));
          const reject = el("button", "button ghost", "拒绝");
          reject.type = "button";
          reject.addEventListener("click", () => answerOffer("reject", offer, reject));
          actions.append(accept, reject);
          card.append(actions);
        }
        root.append(card);
      }
    } catch (error) {
      root.replaceChildren(el("p", "trade-empty", "交易列表暂不可用。"));
    }
  }


export async function answerOffer(kind, offer, button) {
    await withPending(button, async () => {
      try {
        const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades/${encodeURIComponent(offer.offer_id)}/${kind}`, {});
        if (result.status !== "accepted") throw new Error(result.message || "交易未完成");
        toast(kind === "accept" ? "交易已完成。" : "已拒绝报价。", "ok");
        await Promise.allSettled([loadView(), loadTrades(), loadFeed()]);
      } catch (error) {
        toast(describeError(error, "交易未完成"), "error");
      }
    });
  }


export async function sendTrade(form) {
    const target = $("trade-target")?.value;
    const give = parseBundle($("trade-give")?.value || "");
    const want = parseBundle($("trade-want")?.value || "");
    if (!target) return toast("附近没有可交易的人。", "error");
    if (!give || !want) return toast("用 item=数量 的格式填写给出和想要的物品。", "error");
    const button = $("trade-send");
    await withPending(button, async () => {
      try {
        const result = await write(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades`, {
          recipient_id: target,
          give,
          want,
        });
        if (result.status !== "accepted") throw new Error(result.message || "交易未提交");
        $("trade-give").value = "";
        $("trade-want").value = "";
        toast("报价已发出，等待对方回应。", "ok");
        await loadTrades();
      } catch (error) {
        toast(describeError(error, "交易未提交"), "error");
      }
    });
  }


export async function loadFeed(force = false) {
    try {
      const response = await request("/api/v1/observer/timeline?limit=80&after=0&latest=1");
      const all = Array.isArray(response.events) ? response.events : [];
      const events = all.filter((event) => {
        const type = String(event.event_type || "");
        if (SKIP_EVENTS.has(type) || type.startsWith("evolution.") || type.startsWith("runtime.")) return false;
        if (type === "behavior.failed") {
          const actorId = String(event.actor_id || event.activity?.actor_id || "");
          if (actorId && actorId !== state.playerId) return false;
        }
        return true;
      });
      state.feedEvents = events.slice().reverse();
      state.agentEvents = all
        .filter((event) => {
          const activity = event.activity || {};
          return activity.kind && ["behavior", "world.change", "story"].includes(activity.kind);
        })
        .slice(-24)
        .reverse();
      renderFeed();
    } catch (error) {
      if (force) toast("世界动态暂不可用。", "error");
    }
  }


export async function openMap() {
    if (!state.playerId) return;
    state.map = state.view?.map || state.map;
    if (state.map) {
      renderMap();
      showMapDialog();
    }
    try {
      const map = await request(`/api/v1/map?viewer_id=${encodeURIComponent(state.playerId)}&observer=0`);
      state.map = map;
      renderMap();
      showMapDialog();
    } catch (error) {
      if (!state.map) toast(describeError(error, "地图暂不可用"), "error");
    }
  }


export function travelToNode(node) {
    const travelAction = (state.view?.actions || []).find(
      (entry) => entry.kind === "travel" && (entry.destination_id || entry.parameters?.location_id) === node.id,
    );
    if (!travelAction) return;
    const dialog = $("map-dialog");
    if (dialog.open) dialog.close();
    beginAction(travelAction);
  }


  /* ---------- Settings / runtime ---------- */

export function renderProviderSelect() {
    const select = $("settings-provider");
    if (!select) return;
    const runtime = state.runtime || {};
    const names = Array.isArray(runtime.available_providers) ? runtime.available_providers : [];
    let stored = null;
    try { stored = localStorage.getItem("sim2.game.provider"); } catch (error) { stored = null; }
    const preferred = stored === null ? "" : stored === "default" ? "" : stored;
    const previous = select.value || preferred;
    select.replaceChildren(new Option("默认（服务端配置）", ""));
    for (const name of names) select.append(new Option(name, name));
    if (runtime.llm_configured === false) {
      select.replaceChildren(new Option("未配置模型（仅推进时间）", ""));
      select.disabled = true;
      return;
    }
    select.disabled = false;
    let chosen = previous;
    if (stored === null && names.includes("remote")) chosen = "remote";
    if (chosen && [...select.options].some((option) => option.value === chosen)) select.value = chosen;
  }


export function renderRuntimeStatus() {
    const runtime = state.runtime || {};
    renderProviderSelect();
    const running = Boolean(runtime.running);
    const pill = $("runtime-pill");
    if (pill) {
      pill.textContent = running ? "自动运行中" : "手动世界";
      pill.className = `pill${running ? " live" : ""}`;
    }
    for (const button of document.querySelectorAll("[data-wait-minutes]")) {
      button.disabled = running;
    }
    const detail = $("runtime-detail");
    if (detail) {
      if (running) {
        detail.textContent = `自动运行中 · 从第 ${runtime.start_minute ?? "?"} 分钟到第 ${runtime.target_minute ?? "?"} 分钟 · 模型 ${runtime.provider || "默认"}`;
      } else if (runtime.llm_configured === false) {
        detail.textContent = "未配置模型：世界时间和你的长行动仍会推进，NPC 暂时不会自主决策。配置 ~/.sim2/llm.json 后即可启用完整 Agent。";
      } else {
        detail.textContent = `手动世界 · 第 ${runtime.world_minute ?? state.view?.world_time?.minute ?? 0} 分钟${runtime.last_error ? ` · 上次错误：${runtime.last_error}` : ""}`;
      }
    }
    renderTask();
  }


export async function loadRuntime() {
    try {
      state.runtime = await request("/api/v1/runtime");
      renderRuntimeStatus();
    } catch (error) {
      setOnline(false);
    }
  }


export async function refreshAll() {
    if (!state.playerId) return;
    try {
      await Promise.all([loadView(), loadRuntime()]);
      await Promise.allSettled([loadFeed(), loadDialogue(), loadTrades()]);
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) toast(describeError(error, "刷新失败"), "error");
    }
  }


export async function loadInstances() {
    try {
      const response = await request("/api/v1/instances");
      state.instances = response.instances || [];
      state.activeDb = response.active_db || "";
      renderInstanceSelects();
      renderEntryInstances();
    } catch (error) {
      toast(describeError(error, "无法读取实例列表"), "error");
    }
  }


export function renderInstanceSelects() {
    const select = $("settings-instance");
    if (!select) return;
    select.replaceChildren();
    if (!state.instances.length) {
      select.append(new Option("还没有世界存档", ""));
      return;
    }
    for (const item of state.instances) {
      select.append(new Option(`${item.instance_id}${item.active ? "（当前）" : ""} · ${item.world_id || "?"}`, item.instance_id));
      if (item.active) select.value = item.instance_id;
    }
  }


export async function exchangeOperatorToken(raw) {
    const token = String(raw || "").trim();
    if (!token) throw new Error("请输入 Operator Token。");
    const result = await write("/api/v1/session", { role: "operator", operator_token: token });
    if (result.status !== "ok" || !result.session_token) throw new Error(result.message || "解锁失败");
    state.operatorToken = result.session_token;
    try { sessionStorage.setItem(OPERATOR_KEY, state.operatorToken); } catch (error) { /* ignore */ }
    return state.operatorToken;
  }


export async function unlockOperator() {
    const raw = String($("operator-token").value || "").trim();
    try {
      await exchangeOperatorToken(raw);
      $("operator-token").value = "";
      toast("管理写入已解锁。", "ok");
      await loadRuntime();
    } catch (error) {
      toast(describeError(error, "解锁失败"), "error");
    }
  }


export async function unlockOperatorFromEntry() {
    const raw = String($("entry-operator-token")?.value || "").trim();
    try {
      await exchangeOperatorToken(raw);
      if ($("entry-operator-token")) $("entry-operator-token").value = "";
      toast("管理写入已解锁，可以切换或创建世界了。", "ok");
      $("new-instance-error").textContent = "";
      await loadEntryContext();
    } catch (error) {
      $("new-instance-error").textContent = describeError(error, "解锁失败");
    }
  }


export async function activateInstance() {
    const select = $("settings-instance");
    const instanceId = select?.value;
    if (!instanceId) return;
    if (!state.operatorToken) return toast("切换实例需要先解锁管理。", "error");
    try {
      const result = await write("/api/v1/instances/activate", { instance_id: instanceId });
      if (result.status !== "accepted") throw new Error(result.message || "切换失败");
      storageSet(PLAYER_KEY, null);
      window.location.reload();
    } catch (error) {
      toast(describeError(error, "切换实例失败"), "error");
    }
  }


export async function runtimeCommand(command, body = {}) {
    if (!state.operatorToken) return toast("自动运行需要先解锁管理。", "error");
    try {
      const result = await write(`/api/v1/runtime/${command}`, body);
      if (result.status === "rejected") throw new Error(result.message || "运行时操作被拒绝");
      toast(command === "start" ? "自动运行已开始。" : "运行时已更新。", "ok");
      await loadRuntime();
    } catch (error) {
      toast(describeError(error, "运行时操作失败"), "error");
    }
  }


export function openSettings() {
    $("settings-drawer").classList.remove("hidden");
    renderInstanceSelects();
    renderRuntimeStatus();
  }


export function closeSettings() {
    $("settings-drawer").classList.add("hidden");
  }


  /* ---------- World editor handoff ---------- */

export function adminUrl(viewId = "") {
    const params = new URLSearchParams();
    if (viewId) params.set("view", viewId);
    if (state.operatorToken) params.set("operator", state.operatorToken);
    const query = params.toString();
    return `/admin.html${query ? `#${query}` : ""}`;
  }


export function openAdmin(viewId = "") {
    const win = window.open(adminUrl(viewId), "_blank");
    if (win) {
      try { win.opener = null; } catch (error) { /* cross-window hardening */ }
    } else {
      toast("浏览器阻止了弹出窗口；请允许本站打开新标签后重试。", "error");
    }
  }


export function openEditor() {
    const overlay = $("editor-overlay");
    const frame = $("editor-frame");
    if (!overlay || !frame) return;
    overlay.classList.remove("hidden");
    document.body.classList.add("editor-open");
    frame.src = adminUrl("authoring-view");
  }


export async function closeEditor() {
    $("editor-overlay").classList.add("hidden");
    document.body.classList.remove("editor-open");
    $("editor-frame").src = "about:blank";
    try {
      const instances = await request("/api/v1/instances");
      const activeDb = instances.active_db || "";
      if (activeDb !== state.activeDb) {
        window.location.reload();
        return;
      }
      await loadEntryContext();
      if (state.playerId) await loadView().catch(() => {});
    } catch (error) {
      /* closing the editor must never trap the player */
    }
  }
