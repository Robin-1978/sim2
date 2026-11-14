(() => {
  "use strict";

  const state = {
    after: 0,
    includeTicks: false,
    events: new Map(),
    schema: null,
    template: null,
    sourcePath: null,
    editorMode: "guided",
    draftRevision: 1,
    poll: null,
    lastObserver: null,
  };

  const $ = (id) => document.getElementById(id);
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "content-type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw new Error(`服务端返回了 ${response.status} ${type || "未知内容"}`);
    }
    const value = await response.json();
    if (!response.ok) throw new Error(value.message || `请求失败 (${response.status})`);
    return value;
  }

  function setConnection(online, text = online ? "已连接" : "连接中断") {
    $("connection-dot").className = `status-dot ${online ? "" : "offline"}`;
    $("connection-label").textContent = text;
  }

  function showNotice(message) {
    const notice = $("runtime-notice");
    notice.textContent = message || "";
    notice.classList.toggle("hidden", !message);
  }

  function renderMetrics(status, observer) {
    $("world-clock").textContent = status.world_minute === undefined ? "—" : `minute ${status.world_minute}`;
    $("metric-status").textContent = status.status || "—";
    $("metric-agents").textContent = String((observer.agents || []).length);
    $("metric-locations").textContent = String((observer.entity_counts || {}).location || 0);
    $("metric-cursor").textContent = String(state.after);
  }

  function renderCharacters(observer) {
    const list = $("character-list");
    list.replaceChildren();
    const agents = observer.agents || [];
    if (!agents.length) { list.append(make("div", "empty-state", "暂无角色投影")); return; }
    for (const agent of agents) {
      const card = make("div", "character-card");
      const main = make("div");
      main.append(make("strong", "", agent.name || agent.id));
      const behavior = agent.active_behavior || agent.status || "idle";
      main.append(make("small", "", `${agent.location || agent.location_id || "未知地点"} · ${behavior}`));
      const side = make("div", "character-state");
      side.textContent = agent.remaining_minutes ? `${agent.remaining_minutes} min` : (agent.status || "idle");
      card.append(main, side);
      list.append(card);
    }
  }

  function renderDialogueTargets(observer) {
    const agents = observer.agents || [];
    for (const id of ["dialogue-speaker", "dialogue-listener"]) {
      const select = $(id); const previous = select.value; select.replaceChildren();
      for (const agent of agents) {
        const option = document.createElement("option"); option.value = agent.id; option.textContent = `${agent.name || agent.id} · ${agent.location || "unknown"}`; select.append(option);
      }
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    }
  }

  function positionNodes(nodes) {
    const result = new Map();
    const count = Math.max(1, nodes.length);
    nodes.forEach((node, index) => {
      const authored = node.position && Number.isFinite(Number(node.position.x)) && Number.isFinite(Number(node.position.y));
      const angle = (Math.PI * 2 * index) / count - Math.PI / 2;
      result.set(node.id, authored ? { x: 15 + Math.max(0, Math.min(70, Number(node.position.x))), y: 16 + Math.max(0, Math.min(65, Number(node.position.y))) } : { x: 50 + Math.cos(angle) * 31, y: 50 + Math.sin(angle) * 31 });
    });
    return result;
  }

  function renderMap(view) {
    const canvas = $("map-canvas");
    const routes = $("map-routes");
    canvas.replaceChildren(); routes.replaceChildren();
    const nodes = view.nodes || [];
    const positions = positionNodes(nodes);
    const labels = new Map(nodes.map((node) => [node.id, node.name || node.id]));
    for (const edge of view.edges || []) {
      const from = positions.get(edge.from), to = positions.get(edge.to);
      if (!from || !to) continue;
      const line = make("span", "map-edge");
      const dx = (to.x - from.x) * 2.25, dy = (to.y - from.y) * 3.25;
      line.style.left = `${from.x}%`; line.style.top = `${from.y}%`;
      line.style.width = `${Math.sqrt(dx * dx + dy * dy)}%`;
      line.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`;
      canvas.append(line);
      const chip = make("span", "route-chip", `${labels.get(edge.from) || edge.from} ↔ ${labels.get(edge.to) || edge.to}`);
      routes.append(chip);
    }
    for (const node of nodes) {
      const point = positions.get(node.id); if (!point) continue;
      const item = make("div", "map-node");
      item.style.left = `${point.x}%`; item.style.top = `${point.y}%`;
      item.append(make("strong", "", node.name || node.id));
      const occupants = (node.occupants || []).length;
      item.append(make("small", "", occupants ? `${occupants} occupant${occupants > 1 ? "s" : ""}` : (node.safe ? "safe" : "visible")));
      canvas.append(item);
    }
    $("map-summary").textContent = `${nodes.length} locations · ${(view.edges || []).length} routes`;
    if (!nodes.length) canvas.append(make("div", "empty-state", "没有可见地点"));
  }

  function renderTimeline() {
    const list = $("timeline-list");
    list.replaceChildren();
    const events = [...state.events.values()].sort((a, b) => (a.event_id || 0) - (b.event_id || 0)).slice(-80).reverse();
    if (!events.length) { list.append(make("div", "empty-state", "等待世界事件…")); return; }
    for (const event of events) {
      const item = make("article", "timeline-item");
      item.append(make("div", "timeline-minute", `#${event.world_minute ?? "—"}`));
      const body = make("div");
      body.append(make("p", "", event.narrative || event.summary || event.event_type || "world event"));
      body.append(make("small", "", `${event.event_type || "event"} · event ${event.event_id}`));
      item.append(body); list.append(item);
    }
  }

  async function renderEvolution() {
    const list = $("evolution-list"); list.replaceChildren();
    try {
      const response = await request("/evolution/tasks");
      const tasks = response.tasks || [];
      if (!tasks.length) { list.append(make("div", "empty-state", "暂无待处理任务")); return; }
      for (const task of tasks.slice(0, 12)) {
        const item = make("div", "evolution-item");
        item.append(make("strong", "", `${task.role || "world"} · ${task.task_type || "task"}`));
        item.append(make("small", "", `due ${task.due_minute ?? "—"} · ${task.status || "ready"}`));
        list.append(item);
      }
    } catch (error) { list.append(make("div", "empty-state", "演化队列暂时不可用")); }
  }

  async function refreshRuntime(full = false) {
    try {
      const [status, observer, map, timeline] = await Promise.all([
        request("/status"), request("/observer?limit=30"), request("/map?observer=1"),
        request(`/timeline?limit=80&after=${full ? 0 : state.after}&ticks=${state.includeTicks ? 1 : 0}`),
      ]);
      if (full) { state.events.clear(); state.after = 0; }
      for (const event of timeline.events || []) state.events.set(event.event_id, event);
      state.after = Math.max(state.after, Number(timeline.next_after_event_id || 0));
      state.lastObserver = observer;
      renderMetrics(status, observer); renderCharacters(observer); renderDialogueTargets(observer); renderMap(map); renderTimeline();
      await renderEvolution();
      setConnection(true, "已连接"); showNotice(""); $("last-update").textContent = `updated ${new Date().toLocaleTimeString()}`;
    } catch (error) { setConnection(false); showNotice(error.message || "无法读取世界投影"); }
  }

  async function control(path) {
    try { await request(path, { method: "POST", body: JSON.stringify({ reason: "web" }) }); await refreshRuntime(true); }
    catch (error) { showNotice(error.message || "命令未提交"); }
  }

  async function enterPlayer() {
    const playerId = $("player-id").value.trim(); const originId = $("player-origin").value.trim();
    if (!playerId || !originId) { setInteractionResult("需要玩家 ID 和起点 ID", false); return; }
    try { const result = await request("/players", { method: "POST", body: JSON.stringify({ player_id: playerId, origin_id: originId, name: $("player-name").value.trim() || undefined }) }); setInteractionResult(result.status === "accepted" ? "玩家已进入世界" : JSON.stringify(result), result.status === "accepted"); await refreshRuntime(true); }
    catch (error) { setInteractionResult(error.message || "玩家进入失败", false); }
  }

  async function sendDialogue() {
    const speakerId = $("dialogue-speaker").value; const listenerId = $("dialogue-listener").value; const message = $("dialogue-message").value.trim();
    if (!speakerId || !listenerId || !message) { setInteractionResult("请选择说话者、听者并输入消息", false); return; }
    try { const result = await request("/dialogue", { method: "POST", body: JSON.stringify({ speaker_id: speakerId, listener_id: listenerId, topic: $("dialogue-topic").value.trim() || "general", message }) }); setInteractionResult(result.status === "accepted" ? "对话已提交并写入 Platform 事件流" : JSON.stringify(result), result.status === "accepted"); if (result.status === "accepted") { $("dialogue-message").value = ""; await refreshRuntime(true); } }
    catch (error) { setInteractionResult(error.message || "对话未提交", false); }
  }

  function setInteractionResult(message, ok) { const target = $("interaction-result"); target.textContent = message || ""; target.className = `interaction-result ${ok ? "ok" : "error"}`; }

  function defaultTemplate() {
    return { template_version: "draft-1", world: { id: "new_world", title: "未命名世界", premise: "", locale: "zh-CN", properties: {} }, attributes: [], locations: [], resources: [], objects: [], factions: [], agents: [], capabilities: [], actions: [], behavior_trees: [], recipes: [], relationships: [], rules: {}, evolution: {}, story: {}, player_template: {}, initial_events: [], prompts: {} };
  }

  const collectionSections = ["attributes", "locations", "resources", "objects", "factions", "agents", "capabilities", "actions", "behavior_trees", "recipes", "relationships", "initial_events"];
  const objectSections = ["rules", "evolution", "story", "player_template", "prompts"];

  function setDraft(value, { increment = false, sourcePath = undefined, clearSource = false } = {}) {
    state.template = JSON.parse(JSON.stringify(value || defaultTemplate()));
    if (clearSource) state.sourcePath = null;
    else if (sourcePath !== undefined) state.sourcePath = sourcePath;
    $("template-editor").value = JSON.stringify(state.template, null, 2);
    syncQuickFields(state.template);
    renderGuidedEditor(state.template);
    if (increment) state.draftRevision += 1;
    $("draft-revision").textContent = `revision ${state.draftRevision}`;
  }

  function renderGuidedEditor(value) {
    const root = $("guided-editor");
    if (!root) return;
    root.replaceChildren();
    const world = value.world || {};
    const worldSection = document.createElement("details"); worldSection.className = "guided-section"; worldSection.open = true;
    const worldSummary = document.createElement("summary"); worldSummary.textContent = "World · 世界基本信息"; worldSection.append(worldSummary);
    const worldBody = make("div", "guided-body"); const fields = make("div", "guided-fields");
    for (const key of ["id", "title", "premise", "genre", "locale", "time_unit", "start_world_minute"]) {
      const label = make("label", "", key); const input = document.createElement("input"); input.dataset.worldField = key; input.value = world[key] ?? ""; label.append(input); fields.append(label);
    }
    worldBody.append(fields); worldSection.append(worldBody); root.append(worldSection);
    for (const section of collectionSections) {
      if (!Array.isArray(value[section])) continue;
      const details = document.createElement("details"); details.className = "guided-section"; details.dataset.collectionSection = section;
      const summary = document.createElement("summary"); summary.textContent = `${section} · ${value[section].length} items`; details.append(summary);
      const body = make("div", "guided-body"); body.append(make("p", "guided-hint", "每个条目是一个可单独校验的定义；复杂字段保持原结构。"));
      value[section].forEach((item, index) => {
        const row = make("div", "collection-item"); row.dataset.index = String(index);
        const head = make("div", "collection-item-head"); head.append(make("strong", "", item.id || item.name || `${section} ${index + 1}`));
        const remove = make("button", "text-button", "移除"); remove.type = "button"; remove.addEventListener("click", () => { row.remove(); }); head.append(remove); row.append(head);
        const editor = document.createElement("textarea"); editor.dataset.collectionItem = section; editor.value = JSON.stringify(item, null, 2); row.append(editor); body.append(row);
      });
      const add = make("button", "button ghost collection-add", `添加 ${section} 条目`); add.type = "button"; add.addEventListener("click", () => {
        const row = make("div", "collection-item"); const head = make("div", "collection-item-head"); head.append(make("strong", "", "new item")); const remove = make("button", "text-button", "移除"); remove.type = "button"; remove.addEventListener("click", () => row.remove()); head.append(remove); row.append(head); const editor = document.createElement("textarea"); editor.dataset.collectionItem = section; editor.value = JSON.stringify({ id: `new_${section}_${body.querySelectorAll("[data-collection-item]").length + 1}` }, null, 2); row.append(editor); body.insertBefore(row, add);
      }); body.append(add); details.append(body); root.append(details);
    }
    for (const section of objectSections) {
      if (value[section] === undefined) continue;
      const details = document.createElement("details"); details.className = "guided-section"; details.dataset.objectSection = section;
      const summary = document.createElement("summary"); summary.textContent = `${section} · advanced object`; details.append(summary);
      const body = make("div", "guided-body guided-object"); const editor = document.createElement("textarea"); editor.dataset.objectEditor = section; editor.value = JSON.stringify(value[section] || {}, null, 2); body.append(editor); details.append(body); root.append(details);
    }
  }

  function applyGuidedEditor() {
    const result = JSON.parse(JSON.stringify(state.template || defaultTemplate()));
    result.world = { ...(result.world || {}) };
    document.querySelectorAll("[data-world-field]").forEach((input) => { result.world[input.dataset.worldField] = input.value; });
    for (const section of collectionSections) {
      const editors = [...document.querySelectorAll(`[data-collection-item="${section}"]`)];
      if (!editors.length && Array.isArray(result[section])) result[section] = [];
      const parsed = [];
      for (const editor of editors) {
        try { parsed.push(JSON.parse(editor.value)); }
        catch (error) { throw new Error(`${section} 条目 JSON 错误：${error.message}`); }
      }
      if (editors.length || Array.isArray(result[section])) result[section] = parsed;
    }
    for (const section of objectSections) {
      const editor = document.querySelector(`[data-object-editor="${section}"]`); if (!editor) continue;
      try { result[section] = JSON.parse(editor.value || "{}"); }
      catch (error) { throw new Error(`${section} JSON 错误：${error.message}`); }
    }
    return result;
  }

  function parseDraft() {
    try { return { value: state.editorMode === "guided" ? applyGuidedEditor() : JSON.parse($("template-editor").value), error: null }; }
    catch (error) { return { value: null, error: `JSON 格式错误：${error.message}` }; }
  }

  function syncQuickFields(value) {
    const world = value && typeof value.world === "object" ? value.world : {};
    $("quick-world-id").value = world.id || "";
    $("quick-world-title").value = world.title || "";
    $("quick-world-locale").value = world.locale || "";
  }

  function renderSchema() {
    const list = $("schema-list"); list.replaceChildren();
    const schema = state.schema || {}; const properties = schema.properties || {}; const required = new Set(schema.required || []);
    for (const [key, value] of Object.entries(properties)) {
      const item = make("button", `schema-item ${required.has(key) ? "required" : ""}`, `${key} · ${value.type || "value"}`);
      item.type = "button"; item.addEventListener("click", () => {
        const editor = $("template-editor"); editor.focus(); editor.setSelectionRange(0, 0);
        editor.scrollTop = Math.max(0, editor.value.indexOf(`"${key}"`) * 0.5);
      });
      list.append(item);
    }
  }

  function renderDiagnostics(result, extra = null) {
    const box = $("diagnostics"); box.replaceChildren();
    const passed = result && result.status === "passed";
    const summary = make("div", `diagnostic-summary ${passed ? "" : "error"}`, passed ? "✓ 当前草稿通过编译检查" : "! 当前草稿需要修正"); box.append(summary);
    for (const error of (result && (result.diagnostics || result.errors)) || []) {
      const message = typeof error === "object" ? `${error.path || "template"}: ${error.message || "invalid"}` : error;
      box.append(make("div", "diagnostic-line", message));
    }
    for (const warning of (result && result.warnings) || []) box.append(make("div", "diagnostic-line warning", `兼容提示：${warning}`));
    if (extra) {
      const smoke = extra.smoke;
      if (smoke) box.append(make("div", "diagnostic-line", `smoke ${smoke.status} · ${smoke.minutes} minute · ${smoke.event_types.join(", ") || "no events"}`));
      if (extra.files && !extra.package_format) box.append(make("div", "diagnostic-line", `${extra.files.length} package files ready`));
    }
    if (passed && !(result.errors || []).length && !extra) box.append(make("div", "diagnostic-line", result.description ? `${result.description.world_id || "world"} · configuration is executable` : "结构、引用和执行性检查通过"));
  }

  async function compileDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    state.draftRevision += 1; $("draft-revision").textContent = `revision ${state.draftRevision}`;
    $("draft-dot").className = "status-dot busy"; $("draft-status").textContent = "编译中";
    try { const result = await request("/authoring/compile", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision }) }); renderDiagnostics(result); $("draft-dot").className = `status-dot ${result.status === "passed" ? "" : "offline"}`; $("draft-status").textContent = result.status === "passed" ? "已通过检查" : "有待修正项"; }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-dot").className = "status-dot offline"; $("draft-status").textContent = "编译失败"; }
  }

  async function smokeDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    $("draft-status").textContent = "smoke 运行中"; $("draft-dot").className = "status-dot busy";
    try { const result = await request("/authoring/smoke", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision, minutes: 3 }) }); renderDiagnostics(result, result); $("draft-dot").className = `status-dot ${result.status === "passed" ? "" : "offline"}`; $("draft-status").textContent = result.status === "passed" ? "smoke 通过" : "smoke 失败"; }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-dot").className = "status-dot offline"; }
  }

  async function exportDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    try { const result = await request("/authoring/export", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision }) }); if (result.status !== "passed") { renderDiagnostics(result); return; } const blob = new Blob([JSON.stringify(result.files, null, 2)], { type: "application/json" }); const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${result.world_id || "world"}-package-files.json`; link.click(); URL.revokeObjectURL(link.href); renderDiagnostics(result); }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); }
  }

  function switchView(viewId) {
    document.querySelectorAll(".mode-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === viewId));
    document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active-view", view.id === viewId));
  }

  function setEditorMode(mode) {
    if (mode === "raw") {
      const parsed = state.editorMode === "guided" ? parseDraft() : { value: JSON.parse($("template-editor").value), error: null };
      if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
      state.template = parsed.value; $("template-editor").value = JSON.stringify(state.template, null, 2);
    } else {
      try { state.template = JSON.parse($("template-editor").value); renderGuidedEditor(state.template); syncQuickFields(state.template); }
      catch (error) { renderDiagnostics({ status: "rejected", errors: [`JSON 格式错误：${error.message}`] }); return; }
    }
    state.editorMode = mode; $("guided-editor").classList.toggle("hidden", mode !== "guided"); $("template-editor").classList.toggle("hidden", mode !== "raw"); $("guided-mode").classList.toggle("active", mode === "guided"); $("raw-mode").classList.toggle("active", mode === "raw");
  }

  async function openPackage(path = "") {
    try {
      const result = await request("/authoring/open", { method: "POST", body: JSON.stringify(path ? { path } : {}) });
      if (result.status !== "passed") { renderDiagnostics(result); $("draft-status").textContent = "打开失败"; return; }
      setDraft(result.template, { sourcePath: result.source_path }); $("draft-status").textContent = `已打开 ${result.description?.world_id || "world"}`; $("draft-dot").className = "status-dot"; renderDiagnostics({ status: "passed", description: result.description });
    } catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-status").textContent = "打开失败"; }
  }

  async function buildWorldFromBrief() {
    const brief = $("builder-brief").value.trim(); if (!brief) { renderDiagnostics({ status: "rejected", errors: ["请先输入世界简述"] }); return; }
    $("draft-status").textContent = "WorldBuilder 运行中"; $("draft-dot").className = "status-dot busy";
    try { const result = await request("/authoring/build", { method: "POST", body: JSON.stringify({ brief }) }); if (result.status !== "passed") { renderDiagnostics(result); $("draft-status").textContent = "起草失败"; $("draft-dot").className = "status-dot offline"; return; } setDraft(result.template, { increment: true, clearSource: true }); $("draft-status").textContent = `已生成草稿 · ${result.audit?.iterations || "?"} 轮`; $("draft-dot").className = "status-dot"; renderDiagnostics({ status: "passed", description: { world_id: result.template?.world?.id || "draft" } }); }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-status").textContent = "起草失败"; $("draft-dot").className = "status-dot offline"; }
  }

  async function init() {
    const initial = defaultTemplate();
    setDraft(initial);
    setEditorMode("guided");
    document.querySelectorAll(".mode-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
    $("refresh-button").addEventListener("click", () => refreshRuntime(true)); $("pause-button").addEventListener("click", () => control("/pause")); $("step-button").addEventListener("click", () => control("/step")); $("toggle-ticks").addEventListener("click", () => { state.includeTicks = !state.includeTicks; $("toggle-ticks").textContent = state.includeTicks ? "隐藏 tick" : "显示 tick"; refreshRuntime(true); });
    $("enter-player").addEventListener("click", enterPlayer); $("send-dialogue").addEventListener("click", sendDialogue);
    $("new-draft").addEventListener("click", () => setDraft(defaultTemplate(), { increment: true, clearSource: true })); $("open-current").addEventListener("click", () => openPackage()); $("open-path").addEventListener("click", () => openPackage($("authoring-path").value.trim())); $("format-draft").addEventListener("click", () => { const parsed = parseDraft(); if (!parsed.error) setDraft(parsed.value); else renderDiagnostics({ status: "rejected", errors: [parsed.error] }); }); $("apply-quick-fields").addEventListener("click", () => { const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; } parsed.value.world = { ...(parsed.value.world || {}), id: $("quick-world-id").value.trim(), title: $("quick-world-title").value.trim(), locale: $("quick-world-locale").value.trim() || "en" }; setDraft(parsed.value, { increment: true }); renderDiagnostics(null); }); $("guided-mode").addEventListener("click", () => setEditorMode("guided")); $("raw-mode").addEventListener("click", () => setEditorMode("raw")); $("build-world").addEventListener("click", buildWorldFromBrief); $("compile-draft").addEventListener("click", compileDraft); $("smoke-draft").addEventListener("click", smokeDraft); $("export-draft").addEventListener("click", exportDraft);
    try { const response = await request("/authoring/schema"); state.schema = response.schema; renderSchema(); } catch (error) { $("schema-list").replaceChildren(make("div", "empty-state", "Schema 暂不可用")); }
    await refreshRuntime(true); state.poll = window.setInterval(() => { if ($("runtime-view").classList.contains("active-view")) refreshRuntime(false); }, 4000);
  }

  window.addEventListener("DOMContentLoaded", init);
})();

