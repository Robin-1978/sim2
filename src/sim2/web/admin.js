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
    lastMap: null,
    selectedLocationId: null,
    selectedAgentId: null,
    packages: [],
    playerId: null,
    playerData: null,
    playerEntry: null,
    observerToken: null,
    operatorToken: null,
    playerToken: null,
    sessionToken: null,
    runtime: null,
    instances: [],
    activeDb: null,
    operatorResolve: null,
    stream: null,
    streamFailures: 0,
    advancing: false,
    expandedCollections: new Set(),
    openItem: null,
    draftDirty: false,
    catalogRequest: 0,
  };

  const $ = (id) => document.getElementById(id);
  const OPERATOR_KEY = "sim2.game.operator";
  const LEGACY_OPERATOR_KEY = "sim2_operator_token";
  const VIEW_ALIASES = {
    play: "play-view",
    runtime: "runtime-view",
    observer: "runtime-view",
    authoring: "authoring-view",
    editor: "authoring-view",
    design: "authoring-view",
    console: "console-view",
  };
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  async function request(path, options = {}) {
    const usedOperatorToken = Boolean(state.operatorToken);
    const sessionToken = state.operatorToken || state.playerToken || state.observerToken || state.sessionToken;
    const method = String(options.method || "GET").toUpperCase();
    let response;
    try {
      response = await fetch(path, {
        ...options,
        // Spread options first: per-call headers (Idempotency-Key) must extend,
        // never replace, the session and content-type headers.
        headers: { "content-type": "application/json", ...(sessionToken ? { "x-sim2-session": sessionToken } : {}), ...(options.headers || {}) },
      });
    } catch (error) {
      if (typeof console !== "undefined" && console.error) console.error(`[sim2] ${method} ${path} 网络错误`, error);
      throw error;
    }
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw new Error(`服务端返回了 ${response.status} ${type || "未知内容"}`);
    }
    const value = await response.json();
    if (!response.ok) {
      if (typeof console !== "undefined" && console.error) {
        console.error(`[sim2] ${method} ${path} -> ${response.status} ${value.message || ""}`, {
          failed_check: value.failed_check || value.status || null,
          session: sessionToken ? "present" : "missing",
        });
      }
      const error = new Error(value.message || `请求失败 (${response.status})`);
      error.status = response.status;
      error.code = value.failed_check || value.status || null;
      if (usedOperatorToken && response.status === 403 && path !== "/session") {
        clearOperatorSession();
        openOperatorDialog(true);
      }
      throw error;
    }
    return value;
  }

  function newIdempotencyKey() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return `web-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e12).toString(36)}`;
  }

  async function write(path, body = {}) {
    // POST with an idempotency key; retries once on network failure with the same key.
    const key = newIdempotencyKey();
    const attempt = () => request(path, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ ...body, idempotency_key: key }) });
    try {
      return await attempt();
    } catch (error) {
      if (error instanceof TypeError) return await attempt();
      throw error;
    }
  }

  async function withPending(button, fn) {
    if (!button) return fn();
    const wasDisabled = button.disabled;
    button.disabled = true;
    try { return await fn(); }
    finally { button.disabled = wasDisabled; }
  }

  function mergeTimelineEvents(events) {
    let advanced = false;
    for (const event of events || []) {
      if (event == null || event.event_id == null) continue;
      if (!state.events.has(event.event_id)) advanced = true;
      state.events.set(event.event_id, event);
      state.after = Math.max(state.after, Number(event.event_id || state.after));
    }
    if (advanced) renderTimeline();
  }

  function startStream() {
    if (state.stream || typeof EventSource === "undefined") return;
    let source;
    try {
      source = new EventSource(`/events/stream?after=${state.after || 0}${state.includeTicks ? "&ticks=1" : ""}`);
    } catch (error) { return; }
    state.stream = source;
    state.streamFailures = 0;
    source.addEventListener("timeline", (event) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        mergeTimelineEvents(payload.events);
        if (payload.next_after_event_id) state.after = Math.max(state.after, Number(payload.next_after_event_id));
        setConnection(true, "实时连接");
      } catch (error) { /* keep polling as the fallback */ }
    });
    source.addEventListener("runtime", (event) => {
      try {
        const runtime = JSON.parse(event.data || "{}");
        renderRuntime(runtime);
        if (runtime.world_minute !== undefined) $("world-clock").textContent = `minute ${runtime.world_minute}`;
        $("last-update").textContent = `updated ${new Date().toLocaleTimeString()}`;
        setConnection(true, "实时连接");
      } catch (error) { /* keep polling as the fallback */ }
    });
    source.addEventListener("dialogue", () => { renderNpcDialogue(); });
    source.addEventListener("decisions", () => { renderNpcDecisions(); });
    source.onerror = () => {
      state.streamFailures = (state.streamFailures || 0) + 1;
      if (state.streamFailures >= 3 && state.stream) {
        try { state.stream.close(); } catch (error) { /* ignore */ }
        state.stream = null;
      }
    };
  }

  function setOperatorState(unlocked) {
    $("operator-unlock").textContent = unlocked ? "管理已解锁" : "解锁管理";
    $("operator-unlock").classList.toggle("unlocked", unlocked);
  }

  function readStoredOperatorToken() {
    try {
      return sessionStorage.getItem(OPERATOR_KEY) || sessionStorage.getItem(LEGACY_OPERATOR_KEY);
    } catch (error) {
      return null;
    }
  }

  function rememberOperatorToken(token) {
    // Storage may be blocked (hardened browser/policy). The session is
    // already valid server-side; never let a storage failure strand the dialog.
    try {
      sessionStorage.setItem(OPERATOR_KEY, token);
      sessionStorage.setItem(LEGACY_OPERATOR_KEY, token);
    } catch (error) { /* memory-only session for this tab */ }
  }

  function forgetOperatorToken() {
    try {
      sessionStorage.removeItem(OPERATOR_KEY);
      sessionStorage.removeItem(LEGACY_OPERATOR_KEY);
    } catch (error) { /* ignore */ }
  }

  function readHandoff() {
    const raw = String(location.hash || "").replace(/^#/, "");
    if (!raw) return { token: null, view: null };
    if (!raw.includes("=")) return { token: null, view: raw };
    const params = new URLSearchParams(raw);
    const token = params.get("operator");
    const view = params.get("view");
    if (token || view) {
      // Keep the view so an in-editor reload (create/open instance) returns to
      // the same mode, but never keep the session token in browser history.
      const next = view ? `#view=${encodeURIComponent(view)}` : "";
      try { history.replaceState(null, "", `${location.pathname}${location.search}${next}`); }
      catch (error) { /* history can be restricted */ }
    }
    return { token, view };
  }

  function resolveView(view) {
    if (!view) return "";
    const key = String(view).trim().toLowerCase();
    if (key.endsWith("-view")) return key;
    return VIEW_ALIASES[key] || "";
  }

  function storageWritable() {
    try {
      sessionStorage.setItem("sim2_probe", "1");
      sessionStorage.removeItem("sim2_probe");
      return true;
    } catch (error) { return false; }
  }

  function clearOperatorSession() {
    state.operatorToken = null;
    forgetOperatorToken();
    setOperatorState(false);
  }

  function openOperatorDialog(force = false) {
    if (state.operatorToken && !force) return Promise.resolve(true);
    if ($("operator-dialog").open) return Promise.resolve(false);
    if (typeof $("operator-dialog").showModal === "function") {
      $("operator-dialog").showModal();
      const input = $("operator-token");
      // Autofill + Enter submits the form; focus here so paste/keyboard flow lands in the input.
      if (input && typeof input.focus === "function") { input.focus(); input.select(); }
    }
    return new Promise((resolve) => { state.operatorResolve = resolve; });
  }

  async function unlockOperator(rawToken) {
    const token = String(rawToken || "").trim();
    if (!token) throw new Error("请输入 Operator Token");
    const result = await request("/session", { method: "POST", body: JSON.stringify({ role: "operator", operator_token: token }) });
    if (result.status !== "ok" || result.role !== "operator") throw new Error(result.message || "解锁失败");
    state.operatorToken = result.session_token;
    rememberOperatorToken(result.session_token);
    setOperatorState(true);
    loadPackageCatalog();
    const resolve = state.operatorResolve;
    state.operatorResolve = null;
    if (resolve) resolve(true);
    $("operator-dialog").close();
    return true;
  }

  async function ensureOperator() {
    if (state.operatorToken) return true;
    return openOperatorDialog();
  }

  function operatorAction(handler) {
    return async (...args) => {
      if (!(await ensureOperator())) return;
      try { return await handler(...args); }
      catch (error) {
        if (/operator|session|forbidden|权限/i.test(error.message || "")) {
          clearOperatorSession();
          openOperatorDialog(true);
        }
        throw error;
      }
    };
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

  function renderMetrics(status, observer, runtime = {}) {
    $("world-clock").textContent = status.world_minute === undefined ? "—" : `minute ${status.world_minute}`;
    $("metric-status").textContent = runtime.state || status.status || "—";
    $("metric-agents").textContent = String((observer.agents || []).length);
    $("metric-locations").textContent = String((observer.entity_counts || {}).location || 0);
    $("metric-cursor").textContent = String(state.after);
  }

  function renderRuntime(runtime = {}) {
    state.runtime = runtime;
    const stateName = runtime.state || "stopped";
    const labels = { stopped: "已停止", starting: "启动中", running: "自动运行", pausing: "正在暂停", paused: "已暂停", stopping: "正在停止", failed: "运行失败", cooldown: "模型冷却", paused_budget: "预算暂停" };
    $("runtime-state").textContent = labels[stateName] || stateName;
    $("runtime-state").className = `runtime-state ${stateName}`;
    $("runtime-console-state").textContent = labels[stateName] || stateName;
    const minute = runtime.world_minute ?? 0;
    const target = runtime.target_minute;
    const targetText = target == null ? "持续运行" : `目标 minute ${target}`;
    $("runtime-console-detail").textContent = runtime.running
      ? `正在运行 · ${targetText} · ${runtime.provider || "角色默认模型"}`
      : (runtime.stop_reason || (stateName === "paused" ? "世界已暂停，可以继续或单步。" : "选择预算后开始世界自动演化。"));
    const start = runtime.start_minute ?? minute;
    const pct = target && target > start ? Math.max(0, Math.min(100, ((minute - start) / (target - start)) * 100)) : (runtime.running ? 8 : 0);
    $("runtime-progress-bar").style.width = `${pct}%`;
    $("runtime-usage").textContent = `minute ${minute} · 本 run ${runtime.llm_calls_this_run ?? 0}/${runtime.max_llm_calls ?? "∞"} calls · ${runtime.prompt_tokens ?? 0} prompt / ${runtime.completion_tokens ?? 0} completion tokens`;
    const usageNode = $("runtime-usage");
    usageNode.classList.remove("usage-warn", "usage-over");
    if (runtime.max_llm_calls) {
      const ratio = Number(runtime.llm_calls_this_run || 0) / Number(runtime.max_llm_calls);
      if (ratio >= 1) usageNode.classList.add("usage-over");
      else if (ratio >= 0.8) usageNode.classList.add("usage-warn");
    }
    const history = runtime.history || [];
    $("runtime-history-count").textContent = String(history.length);
    const historyList = $("runtime-history-list");
    if (historyList) {
      historyList.replaceChildren();
      if (!history.length) historyList.append(make("div", "empty-state", "尚无已完成的运行段。"));
      for (const item of history.slice(0, 10)) {
        const row = make("div", "run-segment");
        row.append(make("strong", "", `${item.run_id ? String(item.run_id).slice(0, 8) : "?"} · ${item.state || "done"}`));
        row.append(document.createTextNode(
          ` · minute ${item.start_minute ?? "?"}→${item.end_minute ?? "?"}（+${item.minutes_advanced ?? 0}）`
          + ` · ${item.llm_calls ?? 0} calls · ${item.prompt_tokens ?? 0}/${item.completion_tokens ?? 0} tokens`
          + (item.stop_reason ? ` · ${item.stop_reason}` : "")
        ));
        historyList.append(row);
      }
    }
    $("runtime-error").textContent = runtime.last_error || "";
    const provider = $("runtime-provider");
    const previous = provider.value;
    provider.replaceChildren(new Option("默认角色模型", ""));
    for (const name of runtime.available_providers || []) provider.append(new Option(name, name));
    if ([...provider.options].some((item) => item.value === previous)) provider.value = previous;
    const running = Boolean(runtime.running);
    $("start-button").disabled = running;
    $("pause-button").disabled = !running;
    $("resume-button").disabled = running || !runtime.run_id;
    $("stop-button").disabled = !runtime.run_id || runtime.state === "stopped";
    $("step-button").disabled = running;
  }

  function renderInstances(payload) {
    state.instances = payload.instances || [];
    state.activeDb = payload.active_db || null;
    const select = $("instance-select");
    select.replaceChildren();
    for (const item of state.instances) {
      const label = `${item.world_id || item.instance_id} · ${item.instance_id} · day ${Math.floor((item.world_minute || 0) / 1440) + 1}`;
      select.append(new Option(label, item.db_path, false, item.active));
    }
    if (!state.instances.length) select.append(new Option("暂无实例", ""));
  }

  async function loadInstances() {
    try { renderInstances(await request("/instances")); }
    catch (error) { showNotice(error.message || "无法读取实例列表"); }
  }

  async function activateInstance(dbPath) {
    if (!dbPath || dbPath === state.activeDb) return;
    if (!(await ensureOperator())) {
      $("instance-select").value = state.activeDb || "";
      return;
    }
    try {
      const result = await write("/instances/activate", { db_path: dbPath });
      if (result.status !== "accepted") throw new Error(result.message || "实例切换失败");
      window.location.reload();
    } catch (error) {
      $("instance-select").value = state.activeDb || "";
      throw error;
    }
  }

  async function startRuntime() {
    if (!(await ensureOperator())) return;
    const result = await write("/runtime/start", { provider: $("runtime-provider").value || null, max_world_minutes: Number($("runtime-minutes").value || 1440), max_llm_calls: Number($("runtime-llm-calls").value || 120) });
    if (result.status !== "accepted") throw new Error(result.message || result.failed_check || "自动运行未启动");
    renderRuntime(result); await refreshRuntime(true);
  }

  async function runtimeCommand(path, body = {}) {
    if (!(await ensureOperator())) return;
    const result = await write(path, body);
    if (result.status === "rejected") throw new Error(result.message || result.failed_check || "运行命令被拒绝");
    renderRuntime(result.runtime || result); await refreshRuntime(false);
    return result;
  }

  function renderCharacters(observer) {
    const list = $("character-list");
    list.replaceChildren();
    const agents = observer.agents || [];
    if (!agents.length) { list.append(make("div", "empty-state", "暂无角色投影")); return; }
    for (const agent of agents) {
      const card = make("div", "character-card");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.addEventListener("click", () => { state.selectedAgentId = agent.id; state.selectedLocationId = agent.location_id; renderInspector(); });
      card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); card.click(); } });
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
    const authored = nodes.filter((node) => node.position && Number.isFinite(Number(node.position.x)) && Number.isFinite(Number(node.position.y)));
    const xs = authored.map((node) => Number(node.position.x));
    const ys = authored.map((node) => Number(node.position.y));
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const rangeX = Math.max(1e-9, maxX - minX), rangeY = Math.max(1e-9, maxY - minY);
    nodes.forEach((node, index) => {
      const hasPosition = authored.includes(node);
      const angle = (Math.PI * 2 * index) / count - Math.PI / 2;
      if (hasPosition && authored.length > 1) {
        result.set(node.id, { x: 14 + ((Number(node.position.x) - minX) / rangeX) * 72, y: 14 + ((Number(node.position.y) - minY) / rangeY) * 70 });
      } else {
        result.set(node.id, { x: 50 + Math.cos(angle) * 32, y: 50 + Math.sin(angle) * 32 });
      }
    });
    // Authored coordinates may legitimately be identical. Apply a small
    // deterministic separation pass so a compact or imported map remains
    // legible without changing the source coordinates in SQLite.
    const points = [...result.entries()];
    for (let pass = 0; pass < 8; pass += 1) {
      for (let left = 0; left < points.length; left += 1) {
        for (let right = left + 1; right < points.length; right += 1) {
          const a = points[left][1], b = points[right][1];
          let dx = b.x - a.x, dy = b.y - a.y;
          if (Math.abs(dx) < 12 && Math.abs(dy) < 11) {
            if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) { dx = (right - left) % 2 ? 1 : -1; dy = 1; }
            const distance = Math.max(1, Math.hypot(dx, dy));
            const pushX = ((12 - Math.abs(dx)) / distance) * Math.sign(dx || 1) * 0.5;
            const pushY = ((11 - Math.abs(dy)) / distance) * Math.sign(dy || 1) * 0.5;
            a.x = Math.max(8, Math.min(92, a.x - pushX)); b.x = Math.max(8, Math.min(92, b.x + pushX));
            a.y = Math.max(8, Math.min(92, a.y - pushY)); b.y = Math.max(8, Math.min(92, b.y + pushY));
          }
        }
      }
    }
    return result;
  }

  function renderMap(view) {
    state.lastMap = view;
    const canvas = $("map-canvas");
    const routes = $("map-routes");
    canvas.replaceChildren(); routes.replaceChildren();
    const nodes = view.nodes || [];
    if (!state.selectedLocationId || !nodes.some((node) => node.id === state.selectedLocationId)) state.selectedLocationId = nodes[0]?.id || null;
    const positions = positionNodes(nodes);
    const labels = new Map(nodes.map((node) => [node.id, node.name || node.id]));
    for (const edge of view.edges || []) {
      const from = positions.get(edge.from), to = positions.get(edge.to);
      if (!from || !to) continue;
      const line = make("span", "map-edge");
      const width = canvas.clientWidth || 600, height = canvas.clientHeight || 325;
      const dx = ((to.x - from.x) / 100) * width, dy = ((to.y - from.y) / 100) * height;
      line.style.left = `${from.x}%`; line.style.top = `${from.y}%`;
      line.style.width = `${Math.sqrt(dx * dx + dy * dy)}px`;
      line.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`;
      canvas.append(line);
      const detail = Number.isFinite(Number(edge.distance))
        ? ` · ${Number(edge.distance).toLocaleString()} distance · ${Number(edge.minutes || 0).toLocaleString()} min`
        : "";
      const chip = make("span", "route-chip", `${labels.get(edge.from) || edge.from} ↔ ${labels.get(edge.to) || edge.to}${detail}`);
      routes.append(chip);
    }
    for (const node of nodes) {
      const point = positions.get(node.id); if (!point) continue;
      const item = make("div", "map-node");
      item.style.left = `${point.x}%`; item.style.top = `${point.y}%`;
      item.classList.toggle("selected", node.id === state.selectedLocationId);
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-label", `查看 ${node.name || node.id}`);
      item.addEventListener("click", () => { state.selectedLocationId = node.id; state.selectedAgentId = null; renderMap(state.lastMap); renderInspector(); });
      item.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); item.click(); } });
      item.append(make("strong", "", node.name || node.id));
      const occupants = (node.occupants || []).length;
      item.append(make("small", "", occupants ? `${occupants} occupant${occupants > 1 ? "s" : ""}` : (node.safe ? "safe" : "visible")));
      canvas.append(item);
    }
    $("map-summary").textContent = `${nodes.length} locations · ${(view.edges || []).length} routes`;
    $("map-selection").textContent = state.selectedLocationId ? (labels.get(state.selectedLocationId) || state.selectedLocationId) : "未选择地点";
    if (!nodes.length) canvas.append(make("div", "empty-state", "没有可见地点"));
    renderInspector();
  }

  function renderInspector() {
    const box = $("scene-inspector");
    if (!box) return;
    box.replaceChildren();
    const observer = state.lastObserver || {};
    const map = state.lastMap || {};
    const nodes = map.nodes || [];
    const agents = observer.agents || [];
    if (state.selectedAgentId) {
      const agent = agents.find((item) => item.id === state.selectedAgentId);
      if (agent) {
        const head = make("div", "inspector-hero");
        head.append(make("div", "avatar", (agent.name || agent.id).slice(0, 1)));
        const title = make("div"); title.append(make("strong", "inspector-title", agent.name || agent.id)); title.append(make("small", "inspector-muted", `${agent.role || "agent"} · ${agent.location || "未知地点"}`)); head.append(title); box.append(head);
        const status = make("div", "inspector-status"); status.append(make("span", "status-pill", agent.status || "idle")); if (agent.active_behavior) status.append(make("span", "inspector-muted", agent.active_behavior)); box.append(status);
        const grid = make("div", "inspector-stats"); grid.append(stat("剩余时间", agent.remaining_minutes ? `${agent.remaining_minutes} min` : "—")); grid.append(stat("地点", agent.location || "—")); box.append(grid);
        const attributes = Array.isArray(agent.attributes)
          ? agent.attributes
          : Object.entries(agent.attributes || {}).map(([id, value]) => ({ id, label: id, value }));
        if (attributes.length) {
          const section = make("div", "inspector-section"); section.append(make("h3", "", "状态"));
          for (const attribute of attributes.slice(0, 8)) {
            const row = make("div", "attribute-row");
            row.append(make("span", "inspector-muted", attribute.label || attribute.id || "attribute"));
            const unit = attribute.unit ? ` ${attribute.unit}` : "";
            row.append(make("strong", "", `${displayValue(attribute.value)}${unit}`));
            section.append(row);
          }
          box.append(section);
        }
        const back = make("button", "text-button inspector-back", "返回地点"); back.type = "button"; back.addEventListener("click", () => { state.selectedAgentId = null; renderInspector(); }); box.append(back);
        return;
      }
      state.selectedAgentId = null;
    }
    const node = nodes.find((item) => item.id === state.selectedLocationId) || nodes[0];
    if (!node) { box.append(make("div", "empty-state", "没有可见地点")); return; }
    const hero = make("div", "inspector-hero"); hero.append(make("div", "location-glyph", "⌖")); const title = make("div"); title.append(make("strong", "inspector-title", node.name || node.id)); title.append(make("small", "inspector-muted", node.safe ? "安全地点" : "需要保持警戒")); hero.append(title); box.append(hero);
    const tags = make("div", "tag-list"); for (const tag of node.tags || []) tags.append(make("span", "tag", tag)); box.append(tags);
    const occupants = (node.occupants || []).map((item) => agents.find((agent) => agent.id === item.id) || item);
    const people = make("div", "inspector-section"); people.append(make("h3", "", `在场角色 · ${occupants.length}`));
    if (!occupants.length) people.append(make("p", "inspector-muted", "当前没有公开角色在这里。"));
    for (const agent of occupants) { const row = make("button", "occupant-row", ""); row.type = "button"; row.append(make("span", "avatar mini", (agent.name || agent.id).slice(0, 1))); const copy = make("span"); copy.append(make("strong", "", agent.name || agent.id)); copy.append(make("small", "inspector-muted", agent.active_behavior || agent.status || "idle")); row.append(copy); row.addEventListener("click", () => { state.selectedAgentId = agent.id; renderInspector(); }); people.append(row); }
    box.append(people);
    const routes = make("div", "inspector-section"); routes.append(make("h3", "", "可达路线"));
    const routeIds = node.routes || [];
    if (!routeIds.length) routes.append(make("p", "inspector-muted", "没有已配置的路线。"));
    for (const id of routeIds) { const target = nodes.find((item) => item.id === id); const edge = (map.edges || []).find((item) => new Set([item.from, item.to]).has(node.id) && new Set([item.from, item.to]).has(id)); const row = make("button", "route-row", ""); row.type = "button"; row.append(make("span", "", target?.name || id)); row.append(make("small", "inspector-muted", edge ? `${edge.distance} distance · ${edge.minutes} min` : "route")); row.addEventListener("click", () => { state.selectedLocationId = id; state.selectedAgentId = null; renderMap(state.lastMap); }); routes.append(row); }
    box.append(routes);
  }

  function stat(label, value) { const node = make("div", "inspector-stat"); node.append(make("span", "inspector-muted", label)); node.append(make("strong", "", value)); return node; }

  function displayValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    if (typeof value === "boolean") return value ? "是" : "否";
    if (Array.isArray(value)) return value.map(displayValue).join(", ");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key}: ${displayValue(item)}`).join(" · ");
    return String(value);
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

  async function renderNpcDialogue() {
    const list = $("npc-dialogue-list"); if (!list) return;
    list.replaceChildren();
    try {
      const response = await request("/observer/dialogue?limit=20");
      const exchanges = response.exchanges || [];
      if (!exchanges.length) { list.append(make("div", "empty-state", "暂无对话")); return; }
      for (const item of [...exchanges].reverse()) {
        const row = make("article", "dialogue-item");
        const head = make("p", "");
        head.append(make("span", "dialogue-speaker", item.speaker || item.speaker_id || "?"));
        head.append(document.createTextNode(` → ${item.listener || item.listener_id || "小镇"} · ${item.topic || "general"} · #${item.world_minute ?? "—"}`));
        row.append(head);
        row.append(make("p", "", item.message || "(无内容)"));
        list.append(row);
      }
    } catch (error) { list.append(make("div", "empty-state", "对话暂时不可用")); }
  }

  async function renderNpcDecisions() {
    const list = $("npc-decisions-list"); if (!list) return;
    list.replaceChildren();
    try {
      const response = await request("/observer/decisions?limit=20");
      const decisions = response.decisions || [];
      if (!decisions.length) { list.append(make("div", "empty-state", "暂无决策")); return; }
      const verbs = { "behavior.started": "开始", "behavior.completed": "完成", "behavior.failed": "失败", "behavior.interrupted": "中断" };
      for (const item of decisions) {
        const row = make("article", "decision-item");
        const head = make("p", "");
        head.append(make("span", "dialogue-speaker", item.actor || item.actor_id || "?"));
        head.append(document.createTextNode(` ${verbs[item.event_type] || item.event_type} ${item.behavior || "行动"} · #${item.world_minute ?? "—"}`));
        row.append(head);
        if (item.detail) row.append(make("small", "", item.detail));
        if (item.event_id != null) {
          const toggle = make("button", "text-button", "因果链");
          toggle.type = "button";
          toggle.setAttribute("aria-expanded", "false");
          toggle.addEventListener("click", () => toggleCausal(row, toggle, item.event_id));
          row.append(toggle);
        }
        list.append(row);
      }
    } catch (error) { list.append(make("div", "empty-state", "决策暂时不可用")); }
  }

  async function toggleCausal(row, toggle, eventId) {
    const open = row.querySelector(".causal-box");
    if (open) { open.remove(); toggle.setAttribute("aria-expanded", "false"); toggle.textContent = "因果链"; return; }
    toggle.disabled = true;
    try {
      const result = await request(`/events/related?event_id=${encodeURIComponent(eventId)}`);
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

  async function refreshRuntime(full = false) {
    try {
      const [status, observer, map, timeline, runtime] = await Promise.all([
        request("/status"), request("/observer?limit=30"), request("/map?observer=1"),
        request(`/timeline?limit=80&after=${full ? 0 : state.after}&ticks=${state.includeTicks ? 1 : 0}`),
        request("/runtime"),
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

  async function control(path) {
    try { await runtimeCommand(path, { reason: "web" }); }
    catch (error) { showNotice(error.message || "命令未提交"); }
  }

  async function enterPlayer() {
    const playerId = $("play-player-id")?.value.trim();
    const originId = $("play-player-origin")?.value;
    const name = $("play-player-name")?.value.trim();
    const resultBox = $("play-notice") || $("interaction-result");
    if (!playerId) { setPlayerResult("需要填写玩家 ID", false); return; }
    try {
      const result = await write("/players", { player_id: playerId, origin_id: originId || undefined, name: name || undefined });
      if (result.status !== "accepted") throw new Error(result.message || "玩家进入失败");
      state.playerId = playerId;
      state.playerToken = result.session_token || state.playerToken;
      state.sessionToken = state.playerToken || state.observerToken;
      localStorage.setItem("sim2_player_id", playerId);
      if (result.session_token) localStorage.setItem("sim2_player_token", result.session_token);
      await loadPlayerView();
      switchView("play-view");
      if (result.resumed) {
        setPlayerResult(result.migrated ? `已恢复角色 ${result.name || playerId}，并补齐了模板能力与物资。` : `已恢复角色 ${result.name || playerId}。`, true);
      } else {
        setPlayerResult("你已经进入世界。先看看周围，再选择下一步。", true);
      }
    } catch (error) { setPlayerResult(error.message || "玩家进入失败", false); }
  }

  async function loadPlayerEntry() {
    try {
      const entry = await request("/player/entry");
      state.playerEntry = entry;
      $("play-world-id").textContent = entry.world_id || "world";
      $("player-entry-copy").textContent = `从 ${entry.entry_location?.name || "入口地点"} 开始；已有玩家 ID 会恢复该角色并补齐模板能力。`;
      const select = $("play-player-origin"); select.replaceChildren();
      for (const origin of entry.origins || []) select.append(new Option(origin.id, origin.id));
      if (entry.origins?.length) { select.value = entry.origins[0].id; updateOriginPreview(); }
    } catch (error) { setPlayerResult(error.message || "无法读取世界入口", false); }
  }

  function updateOriginPreview() {
    const id = $("play-player-origin")?.value;
    const origin = (state.playerEntry?.origins || []).find((item) => item.id === id);
    $("origin-preview").textContent = origin?.goal ? `起始目标：${origin.goal}` : "选择一个出身后开始。";
  }

  async function loadPlayerView() {
    if (!state.playerId) return;
    try {
      const data = await request(`/player/view?player_id=${encodeURIComponent(state.playerId)}`);
      if (data.status !== "ok") throw new Error(data.message || "无法读取玩家状态");
      state.playerData = data;
      renderPlayerWorld(data);
      $("player-setup").classList.add("hidden");
      $("player-world").classList.remove("hidden");
      $("play-world-time").textContent = data.world_time?.label || "—";
      return true;
    } catch (error) { setPlayerResult(error.message || "玩家状态暂不可用", false); return false; }
  }

  function renderPlayerWorld(data) {
    const player = data.player || {};
    const name = player.name || state.playerId || "玩家";
    $("player-name-label").textContent = name;
    $("player-avatar").textContent = name.slice(0, 1);
    $("player-location-label").textContent = player.location || "未知地点";
    $("player-goal-label").textContent = player.goal || "探索这个世界";
    renderNeedBars(player.attributes || []);
    renderPlayerMap(data.map || {});
    renderPlayerActions(data.actions || [], data.active_task || {}, data.unavailable_actions || []);
    renderPlayerFeed(data.recent_outcomes || []);
    const participants = (data.nearby_participants || data.nearby_entities || []).filter((entity) =>
      ["agent", "player"].includes(entity.type || entity.entity_type)
    );
    const targets = $("player-dialogue-target"); targets.replaceChildren();
    for (const entity of participants) {
      if (!entity.id) continue;
      targets.append(new Option(entity.name || entity.id, entity.id));
    }
    if (!targets.options.length) targets.append(new Option("附近没有可交谈对象", ""));
    const tradeTargets = $("player-trade-target");
    if (tradeTargets) {
      const previous = tradeTargets.value;
      tradeTargets.replaceChildren();
      for (const entity of participants) {
        if (!entity.id || entity.id === state.playerId) continue;
        tradeTargets.append(new Option(entity.name || entity.id, entity.id));
      }
      if (!tradeTargets.options.length) tradeTargets.append(new Option("附近没有可交易对象", ""));
      else if ([...tradeTargets.options].some((option) => option.value === previous)) tradeTargets.value = previous;
    }
    renderPlayerTrades();
  }

  function setPlayerTradeResult(message, ok) {
    const notice = $("player-trade-result");
    if (!notice) return;
    notice.textContent = message || "";
    notice.className = `interaction-result ${ok ? "ok" : "error"}`;
  }

  function bundleText(bundle) {
    if (!bundle || typeof bundle !== "object") return "—";
    const parts = Object.entries(bundle).map(([key, value]) => `${key}×${value}`);
    return parts.length ? parts.join("，") : "—";
  }

  async function renderPlayerTrades() {
    const incoming = $("player-trade-incoming");
    const outgoing = $("player-trade-outgoing");
    if (!incoming || !outgoing || !state.playerId) return;
    incoming.replaceChildren(); outgoing.replaceChildren();
    try {
      const response = await request(`/trade/offers?agent_id=${encodeURIComponent(state.playerId)}&include_closed=0`);
      const offers = (response.offers || []).filter((item) => item.status === "open").slice(0, 10);
      if (!offers.length) incoming.append(make("div", "empty-state", "暂无待处理的交易。"));
      for (const offer of offers) {
        const mine = offer.recipient_id === state.playerId;
        const card = make("article", "trade-card");
        const peer = mine ? offer.offerer_id : offer.recipient_id;
        card.append(make("strong", "", mine ? `来自 ${peer} 的报价` : `发给 ${peer} 的报价（等待对方）`));
        card.append(make("small", "", `给出 ${bundleText(offer.give)} · 想要 ${bundleText(offer.want)} · 第 ${offer.round || 1} 轮`));
        if (mine) {
          const actions = make("div", "trade-card-actions");
          const accept = make("button", "button ghost", "接受");
          accept.type = "button";
          accept.addEventListener("click", () => actOnPlayerOffer("accept", offer, accept));
          const counter = make("button", "button ghost", "用表单反报价");
          counter.type = "button";
          counter.title = "使用下方给出/想要输入框的内容反报价";
          counter.addEventListener("click", () => actOnPlayerOffer("counter", offer, counter));
          const reject = make("button", "button ghost", "拒绝");
          reject.type = "button";
          reject.addEventListener("click", () => actOnPlayerOffer("reject", offer, reject));
          actions.append(accept, counter, reject);
          card.append(actions);
        }
        (mine ? incoming : outgoing).append(card);
      }
      if (!outgoing.children.length) outgoing.append(make("div", "empty-state", "没有发出的待定报价。"));
    } catch (error) {
      incoming.append(make("div", "empty-state", `交易暂不可用：${error.message || "未知错误"}`));
    }
  }

  async function actOnPlayerOffer(kind, offer, button) {
    if (!state.playerId) return;
    await withPending(button, async () => {
      try {
        let result;
        if (kind === "accept") {
          result = await write("/trade/accept", { recipient_id: state.playerId, offer_id: offer.offer_id });
        } else if (kind === "reject") {
          result = await write("/trade/reject", { recipient_id: state.playerId, offer_id: offer.offer_id });
        } else {
          const give = parseBundle($("player-trade-give")?.value || "");
          const want = parseBundle($("player-trade-want")?.value || "");
          result = await write("/trade/counter", { counterparty_id: state.playerId, offer_id: offer.offer_id, give, want });
        }
        if (result.status !== "accepted") throw new Error(describeRejection(result, "交易未提交"));
        setPlayerTradeResult(kind === "accept" ? "交易已完成。" : kind === "reject" ? "已拒绝该报价。" : "反报价已发送。", true);
        await loadPlayerView();
      } catch (error) { setPlayerTradeResult(error.message || "交易未提交", false); }
    });
  }

  async function submitPlayerTrade(button) {
    const peer = $("player-trade-target")?.value;
    if (!state.playerId || !peer) { setPlayerTradeResult("请选择交易对方", false); return; }
    await withPending(button, async () => {
      try {
        const result = await write("/trade/offers", {
          offerer_id: state.playerId,
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

  function renderNeedBars(attributes) {
    const root = $("player-need-bars"); root.replaceChildren();
    const needs = (attributes || []).filter((item) => item.kind === "need").slice(0, 4);
    for (const item of needs) {
      const value = Number(item.value || 0); const row = make("div", "need-bar");
      const label = make("div", "need-bar-label"); label.append(make("span", "", item.label || item.id), make("span", "", `${Math.round(value)}`));
      const track = make("div", "need-bar-track"); const fill = make("div", "need-bar-fill"); fill.style.width = `${Math.max(0, Math.min(100, value))}%`; track.append(fill); row.append(label, track); root.append(row);
    }
  }

  function renderPlayerMap(view) {
    const canvas = $("player-map-canvas"); canvas.replaceChildren();
    const nodes = view.nodes || []; const positions = positionNodes(nodes); const player = state.playerData?.player || {};
    for (const edge of view.edges || []) {
      const from = positions.get(edge.from), to = positions.get(edge.to); if (!from || !to) continue;
      const line = make("span", "map-edge"); const width = canvas.clientWidth || 600, height = canvas.clientHeight || 390;
      const dx = ((to.x - from.x) / 100) * width, dy = ((to.y - from.y) / 100) * height;
      line.style.left = `${from.x}%`; line.style.top = `${from.y}%`; line.style.width = `${Math.hypot(dx, dy)}px`; line.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`; canvas.append(line);
    }
    for (const node of nodes) {
      const point = positions.get(node.id); if (!point) continue;
      const item = make("div", "map-node player-map-node"); item.style.left = `${point.x}%`; item.style.top = `${point.y}%`; item.classList.toggle("selected", node.id === player.location_id);
      item.append(make("strong", "", node.name || node.id)); item.append(make("small", "", node.id === player.location_id ? "你在这里" : (node.safe ? "安全" : "可探索"))); canvas.append(item);
    }
    $("player-map-summary").textContent = `${nodes.length} 个已知地点`;
    const current = nodes.find((item) => item.id === player.location_id);
    const summary = $("player-location-summary"); summary.replaceChildren();
    if (current) { summary.append(make("strong", "", `${current.name || current.id} · ${current.safe ? "安全" : "保持警戒"}`)); for (const tag of current.tags || []) summary.append(make("span", "tag", tag)); }
  }

  function renderPlayerActions(actions, activeTask = {}, unavailable = []) {
    const root = $("player-actions"); root.replaceChildren();
    const appendUnavailable = () => {
      if (!unavailable.length) return;
      const details = document.createElement("details");
      details.className = "unavailable-actions";
      const summary = document.createElement("summary");
      summary.textContent = `未显示的行动（条件未满足，${unavailable.length}）`;
      details.append(summary);
      for (const item of unavailable.slice(0, 10)) {
        const row = make("div", "unavailable-action");
        row.append(make("strong", "", item.label || item.ref || "行动"));
        row.append(make("small", "", (item.reasons || []).join("；") || "当前条件不满足"));
        details.append(row);
      }
      root.append(details);
    };
    if (activeTask.action || activeTask.bt) {
      const running = make("div", "active-task-card"); running.append(make("strong", "", `正在执行：${activeTask.action || activeTask.bt}`)); running.append(make("p", "", `还需要 ${activeTask.remaining_minutes ?? 0} 分钟。行为完成后，Platform 会记录结果并刷新下一步选择。`)); root.append(running);
      if (!state.runtime?.running) { const wait = make("button", "button ghost player-wait", "等待一分钟"); wait.type = "button"; wait.addEventListener("click", playerWait); root.append(wait); }
      if (!state.runtime?.running) { const advance = make("button", "button primary", "推进到完成"); advance.type = "button"; advance.addEventListener("click", () => advancePlayerAction()); root.append(advance); }
      else root.append(make("p", "empty-state", "世界正在自动推进。"));
      if (!state.runtime?.running) root.append(make("p", "empty-state", "手动推进使用与自动运行相同的世界 tick：NPC 也会在同一分钟决策并行动（会调用 LLM），不是只推进玩家。"));
      if (!state.runtime?.running) root.append(make("p", "empty-state", `同步推进模型：${playerProvider() || "默认角色模型（可在“观察世界”选择 remote/DeepSeek）"}`));
      return;
    }
    if (!actions.length) { root.append(make("div", "empty-state", "暂时没有可执行行动；看看地图或稍后再来。")); appendUnavailable(); return; }
    for (const action of actions) {
      const card = make("article", "action-card"); const head = make("div", "action-card-head"); head.append(make("strong", "action-card-title", action.choice || action.name || "行动")); head.append(make("span", "action-card-time", action.time || "Platform 计算时间")); card.append(head);
      card.append(make("p", "", action.summary || action.purpose || "按照当前世界规则执行一个行动。"));
      const meta = make("div", "action-card-meta"); for (const value of [...(action.cost || []), ...(action.impact || []).slice(0, 2)]) meta.append(make("span", "action-chip", value)); card.append(meta);
      const button = make("button", "button ghost", action.kind === "travel" ? "出发" : "执行行动"); button.type = "button"; button.addEventListener("click", () => submitPlayerAction(action)); card.append(button); root.append(card);
    }
    appendUnavailable();
    if (!state.runtime?.running) { const wait = make("button", "button ghost player-wait", "等待一分钟"); wait.type = "button"; wait.addEventListener("click", playerWait); root.append(wait); }
    else root.append(make("p", "empty-state", "世界正在自动推进，你仍然可以提交当前行动。"));
  }

  async function submitPlayerAction(action) {
    if (!state.playerId || state.advancing) return;
    setPlayerResult("行动已提交，Platform 正在校验…", true);
    try {
      const result = await write("/player/act", { player_id: state.playerId, behavior_ref: action.ref, parameters: action.parameters && !Array.isArray(action.parameters) ? action.parameters : {}, target: action.target && typeof action.target === "object" ? action.target : {}, reason: "Player selected this action from the web client.", confidence: 1 });
      if (result.status !== "accepted") throw new Error(result.message || result.errors?.join("; ") || "行动未被接受");
      await loadPlayerView();
      if (!state.runtime?.running) {
        await advancePlayerAction(`${action.choice || action.name || "行动"} 已开始，`);
      } else {
        setPlayerResult(`${action.choice || action.name || "行动"} 已开始；世界会自动推进。`, true);
      }
    } catch (error) { setPlayerResult(error.message || "行动未提交", false); }
  }

  function playerProvider() {
    return $("runtime-provider")?.value || state.runtime?.provider || null;
  }

  async function advancePlayerAction(prefix = "") {
    if (!state.playerId || state.advancing || state.runtime?.running) return;
    state.advancing = true;
    let llmErrors = 0;
    let stopReason = "";
    let npcDecisions = 0;
    try {
      for (let i = 0; i < 40; i += 1) {
        const task = state.playerData?.active_task || {};
        if (!task.action && !task.bt) break;
        const remaining = Number(task.remaining_minutes || 0);
        const minutes = Math.max(1, Math.min(remaining + 1, 10));
        const result = await write("/player/wait", { player_id: state.playerId, minutes, provider: playerProvider() });
        if (result.status !== "accepted") throw new Error(result.message || "无法推进世界");
        llmErrors += Number(result.report?.llm_errors || 0);
        npcDecisions += Number(result.report?.decisions || 0);
        stopReason = result.report?.stop_reason || stopReason;
        await loadPlayerView();
        const worldTime = state.playerData?.world_time?.label || "";
        setPlayerResult(`${prefix}同步推进中${worldTime ? ` · ${worldTime}` : ""}${remaining ? ` · 还需约 ${remaining} 分钟` : ""}`, true);
      }
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

  async function playerWait() {
    if (!state.playerId) return;
    if (state.runtime?.running) { setPlayerResult("世界正在自动推进，无需手动等待。", true); return; }
    try { await write("/player/wait", { player_id: state.playerId, minutes: 1, provider: playerProvider() }); await loadPlayerView(); setPlayerResult("世界向前推进了一分钟。", true); }
    catch (error) { setPlayerResult(error.message || "无法推进世界", false); }
  }

  async function sendPlayerDialogue(button) {
    const target = $("player-dialogue-target")?.value; const message = $("player-dialogue-message")?.value.trim();
    if (!state.playerId || !target || !message) { setPlayerResult("请选择附近对象并输入消息", false); return; }
    await withPending(button || $("player-send-dialogue"), async () => {
      try { const result = await write("/dialogue", { speaker_id: state.playerId, listener_id: target, topic: "general", message }); if (result.status !== "accepted") throw new Error(describeRejection(result, "对话未提交")); $("player-dialogue-message").value = ""; setPlayerResult("对话已经进入世界事件流。", true); await loadPlayerView(); }
      catch (error) { setPlayerResult(error.message || "对话未提交", false); }
    });
  }

  function renderPlayerFeed(outcomes) {
    const root = $("player-feed"); root.replaceChildren();
    if (!outcomes.length) { root.append(make("div", "empty-state", "还没有新的结果。")); return; }
    for (const outcome of outcomes) { const item = make("div", "player-feed-item"); item.append(make("strong", "", outcome.result || outcome.summary || outcome.behavior_choice || "行动结果")); item.append(make("small", "", `${outcome.event_type || "world event"} · minute ${outcome.world_minute ?? "—"}`)); root.append(item); }
  }

  function setPlayerResult(message, ok) { const notice = $("play-notice"); notice.textContent = message || ""; notice.className = `notice ${ok ? "notice-ok" : ""}`; notice.classList.toggle("hidden", !message); }

  function describeRejection(result, fallback) {
    if (!result || typeof result !== "object") return fallback;
    const parts = [result.message || fallback];
    if (result.missing) parts.push(`缺失: ${result.missing}`);
    if (Array.isArray(result.allowed_values) && result.allowed_values.length) parts.push(`可用: ${result.allowed_values.slice(0, 12).join(", ")}`);
    return parts.join(" ");
  }

  async function sendDialogue(button) {
    const speakerId = $("dialogue-speaker").value; const listenerId = $("dialogue-listener").value; const message = $("dialogue-message").value.trim();
    if (!speakerId || !listenerId || !message) { setInteractionResult("请选择说话者、听者并输入消息", false); return; }
    await withPending(button || $("send-dialogue"), async () => {
      try { const result = await write("/dialogue", { speaker_id: speakerId, listener_id: listenerId, topic: $("dialogue-topic").value.trim() || "general", message }); setInteractionResult(result.status === "accepted" ? "对话已提交并写入 Platform 事件流" : JSON.stringify(result), result.status === "accepted"); if (result.status === "accepted") { $("dialogue-message").value = ""; await refreshRuntime(true); } }
      catch (error) { setInteractionResult(error.message || "对话未提交", false); }
    });
  }

  function setInteractionResult(message, ok) { const target = $("interaction-result"); target.textContent = message || ""; target.className = `interaction-result ${ok ? "ok" : "error"}`; }

  function defaultTemplate() {
    return { template_version: "draft-1", world: { id: "new_world", title: "", premise: "", locale: "zh-CN", properties: {} }, attributes: [], locations: [], resources: [], objects: [], factions: [], agents: [], capabilities: [], actions: [], behavior_trees: [], recipes: [], relationships: [], rules: {}, evolution: {}, story: {}, player_template: {}, initial_events: [], prompts: {} };
  }

  const listValue = (value) => (Array.isArray(value) ? value : []);
  const cloneValue = (value) => JSON.parse(JSON.stringify(value));
  const listText = (value) => listValue(value).map((item) => String(item)).join("，");
  const parseListText = (text) => String(text || "").split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);

  function uniqueId(items, base) {
    const used = new Set(listValue(items).map((item) => String(item && item.id ? item.id : "")));
    const wanted = String(base || "new_item");
    if (!used.has(wanted)) return wanted;
    let index = 2;
    while (used.has(`${wanted}_${index}`)) index += 1;
    return `${wanted}_${index}`;
  }

  const COLLECTION_GUIDE = {
    locations: {
      label: "地点",
      hint: "角色会在这里移动、采集和相遇。至少写两个地点，并用路线连起来。",
      emptyHint: "先加一个入口地点，再加一个可以前往的地方。",
      addLabel: "添加地点",
      fields: [
        { key: "name", label: "名字", placeholder: "例如：河桥" },
        { key: "id", label: "ID", mono: true, hint: "英文或拼音，规则用它引用" },
        { key: "routes", label: "通往", type: "list", options: (template) => listValue(template.locations).map((item) => item.id), hint: "填其他地点的 ID，用逗号分隔" },
        { key: "tags", label: "标签", type: "list", hint: "例如：入口，河边" },
        { key: "public", label: "公开地点", type: "bool" },
        { key: "safe", label: "安全地点", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.locations, "new_location"), name: "新地点", public: true, safe: false, tags: [], routes: [] }),
      title: (item, index) => item.name || item.id || `地点 ${index + 1}`,
      meta: (item) => (listValue(item.routes).length ? `${listValue(item.routes).length} 条路线` : "还没有路线"),
    },
    agents: {
      label: "人物",
      hint: "居民和 NPC。名字、身份和目标决定他们怎样行动。",
      emptyHint: "世界需要有人行动。先加一位居民。",
      addLabel: "添加人物",
      fields: [
        { key: "name", label: "名字", placeholder: "例如：林泊" },
        { key: "id", label: "ID", mono: true, hint: "英文或拼音，对话和事件用它引用" },
        { key: "role", label: "身份", placeholder: "例如：村里的守望者" },
        { key: "ultimate_goal", label: "长期目标", type: "textarea", wide: true, placeholder: "这个人最想守住或改变什么？" },
        { key: "goal_tags", label: "目标标签", type: "list", hint: "用于世界趋势统计，可留空" },
        { key: "faction_ids", label: "所属组织", type: "list", options: (template) => listValue(template.factions).map((item) => item.id), hint: "填组织 ID，用逗号分隔" },
      ],
      create: (template) => ({
        id: uniqueId(template.agents, "new_resident"),
        name: "新居民",
        role: "resident",
        ultimate_goal: "",
        traits: {},
        beliefs: [],
        needs: {},
        capabilities: {},
        initial_bt_ids: listValue(template.behavior_trees).length ? [template.behavior_trees[0].id] : [],
        initial_state: {},
        inventory: {},
      }),
      title: (item, index) => item.name || item.id || `人物 ${index + 1}`,
      meta: (item) => item.role || "居民",
    },
    objects: {
      label: "物品",
      hint: "可以被找到、修复或使用的物件。",
      emptyHint: "没有物品也可以玩；加一件会让世界更具体。",
      addLabel: "添加物品",
      fields: [
        { key: "name", label: "名字", placeholder: "例如：旧钥匙" },
        { key: "id", label: "ID", mono: true },
        { key: "type", label: "类型", placeholder: "例如：artifact" },
        { key: "location_id", label: "所在地点", options: (template) => listValue(template.locations).map((item) => item.id) },
        { key: "tags", label: "标签", type: "list" },
        { key: "public", label: "公开可见", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.objects, "new_object"), type: "artifact", name: "新物品", public: true, tags: [], initial_state: {} }),
      title: (item, index) => item.name || item.id || `物品 ${index + 1}`,
      meta: (item) => item.type || "",
    },
    resources: {
      label: "公共资源",
      hint: "村庄、据点或世界的共享库存。",
      emptyHint: "资源用于公共储备、短缺和交易。",
      addLabel: "添加资源",
      fields: [
        { key: "id", label: "资源 ID", mono: true },
        { key: "type", label: "类型", placeholder: "例如：shared_stock" },
        { key: "location_id", label: "所在地点", options: (template) => listValue(template.locations).map((item) => item.id) },
        { key: "amount", label: "初始数量", type: "number" },
      ],
      create: (template) => ({ id: uniqueId(template.resources, "new_resource"), type: "shared_stock", location_id: listValue(template.locations)[0]?.id || "", amount: 100 }),
      title: (item, index) => item.id || `资源 ${index + 1}`,
      meta: (item) => [item.amount !== undefined ? `${item.amount}` : "", item.location_id].filter((value) => value !== undefined && value !== "").join(" · "),
    },
    factions: {
      label: "组织",
      hint: "有名字、领袖和规矩的群体。",
      emptyHint: "组织会让角色之间的关系更有结构。",
      addLabel: "添加组织",
      fields: [
        { key: "name", label: "名字" },
        { key: "id", label: "ID", mono: true },
        { key: "leader_id", label: "领袖", options: (template) => listValue(template.agents).map((item) => item.id) },
        { key: "norms", label: "规矩", type: "list", hint: "用逗号分隔" },
        { key: "public", label: "公开组织", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.factions, "new_faction"), name: "新组织", public: true, leader_id: "", norms: [] }),
      title: (item, index) => item.name || item.id || `组织 ${index + 1}`,
      meta: (item) => (listValue(item.norms).length ? `${listValue(item.norms).length} 条规矩` : ""),
    },
    actions: {
      label: "行动",
      hint: "角色能主动做的事。新手建议复制一个现有行动再修改，避免漏掉必填字段。",
      emptyHint: "当前模板还没有行动。从已验证模板复制一个世界会自带基础行动。",
      addLabel: "复制一个行动",
      clone: true,
      fields: [
        { key: "description", label: "描述", type: "textarea", wide: true, placeholder: "玩家会看到的一句话" },
        { key: "id", label: "ID", mono: true },
        { key: "preconditions", label: "前置条件", type: "list", hint: "例如：actor.mobile" },
      ],
      create: (template) => {
        const last = listValue(template.actions).slice(-1)[0];
        if (!last) return null;
        return { ...cloneValue(last), id: uniqueId(template.actions, `${last.id || "action"}_copy`) };
      },
      title: (item, index) => item.description || item.id || `行动 ${index + 1}`,
      meta: () => "",
    },
    recipes: {
      label: "配方",
      hint: "把材料变成物品。输入、输出和时长决定制作节奏。",
      emptyHint: "当前模板还没有配方。复制一个现有配方最容易上手。",
      addLabel: "复制一个配方",
      clone: true,
      fields: [
        { key: "name", label: "名字" },
        { key: "id", label: "ID", mono: true },
        { key: "duration_minutes", label: "制作分钟", type: "number" },
        { key: "station_tags", label: "需要的工作台", type: "list", hint: "例如：kitchen，fireplace" },
      ],
      create: (template) => {
        const last = listValue(template.recipes).slice(-1)[0];
        if (!last) return null;
        return { ...cloneValue(last), id: uniqueId(template.recipes, `${last.id || "recipe"}_copy`) };
      },
      title: (item, index) => item.name || item.id || `配方 ${index + 1}`,
      meta: (item) => (item.duration_minutes ? `${item.duration_minutes} 分钟` : ""),
    },
    behavior_trees: {
      label: "行为树",
      hint: "NPC 自动行动的步骤。新手保留模板自带的结构，只改名字和预计时长。",
      emptyHint: "当前模板还没有行为树。建议从已验证模板复制一个世界。",
      addLabel: "复制一棵行为树",
      clone: true,
      fields: [
        { key: "summary", label: "摘要", type: "textarea", wide: true },
        { key: "id", label: "ID", mono: true },
        { key: "estimated_minutes", label: "预计分钟", type: "number" },
        { key: "entry_conditions", label: "进入条件", type: "list" },
      ],
      create: (template) => {
        const last = listValue(template.behavior_trees).slice(-1)[0];
        if (!last) return null;
        return { ...cloneValue(last), id: uniqueId(template.behavior_trees, `${last.id || "behavior"}_copy`) };
      },
      title: (item, index) => item.summary || item.id || `行为树 ${index + 1}`,
      meta: () => "",
    },
    capabilities: {
      label: "能力",
      hint: "可以随行动增长的能力值。",
      emptyHint: "能力用于制作、研究和采集的成功率。",
      addLabel: "添加能力",
      fields: [
        { key: "name", label: "名字" },
        { key: "id", label: "ID", mono: true },
        { key: "uses", label: "用于哪些行动", type: "list", options: (template) => listValue(template.actions).map((item) => item.id) },
        { key: "xp_rule", label: "经验规则", placeholder: "例如：one_successful_recipe" },
      ],
      create: (template) => ({ id: uniqueId(template.capabilities, "new_capability"), name: "新能力", value_path: `agent.capabilities.new_capability`, uses: [], xp_rule: "one_successful_action" }),
      title: (item, index) => item.name || item.id || `能力 ${index + 1}`,
      meta: (item) => (listValue(item.uses).length ? `${listValue(item.uses).length} 个行动` : ""),
    },
    attributes: {
      label: "属性",
      hint: "生命、精力等可变化的数值。",
      emptyHint: "属性定义角色的状态上限和初始值。",
      addLabel: "添加属性",
      fields: [
        { key: "label", label: "显示名" },
        { key: "id", label: "ID", mono: true },
        { key: "kind", label: "类型", placeholder: "state 或 need" },
        { key: "min", label: "最小值", type: "number" },
        { key: "max", label: "最大值", type: "number" },
        { key: "default", label: "初始值", type: "number" },
        { key: "prompt", label: "说明", type: "textarea", wide: true, hint: "给模型看的含义说明" },
        { key: "vital", label: "关键属性", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.attributes, "new_attribute"), kind: "state", label: "新属性", unit: "points", min: 0, max: 100, default: 50, prompt: "" }),
      title: (item, index) => item.label || item.id || `属性 ${index + 1}`,
      meta: (item) => item.kind || "",
    },
    relationships: {
      label: "初始关系",
      hint: "角色之间一开始就存在的信任或敌意。",
      emptyHint: "关系会影响对话、交易和结盟。",
      addLabel: "添加关系",
      fields: [
        { key: "from", label: "从", options: (template) => listValue(template.agents).map((item) => item.id) },
        { key: "to", label: "到", options: (template) => listValue(template.agents).map((item) => item.id) },
        { key: "type", label: "关系", placeholder: "例如：trust" },
        { key: "value", label: "数值", type: "number" },
      ],
      create: (template) => ({ from: listValue(template.agents)[0]?.id || "", to: listValue(template.agents)[1]?.id || "", type: "trust", value: 0.5 }),
      title: (item, index) => [item.from, item.to].filter(Boolean).join(" → ") || `关系 ${index + 1}`,
      meta: (item) => [item.type, item.value].filter((value) => value !== undefined && value !== "").join(" · "),
    },
    initial_events: {
      label: "开局事件",
      hint: "世界开始时发生的公开事件。",
      emptyHint: "开局事件会写进世界日志，适合交代背景。",
      addLabel: "添加事件",
      fields: [
        { key: "id", label: "ID", mono: true },
        { key: "type", label: "事件类型", placeholder: "例如：story.note" },
        { key: "world_minute", label: "第几分钟", type: "number" },
      ],
      create: (template) => ({ id: uniqueId(template.initial_events, "new_event"), type: "story.note", world_minute: 0, payload: {} }),
      title: (item, index) => item.type || item.id || `事件 ${index + 1}`,
      meta: (item) => (item.world_minute !== undefined ? `第 ${item.world_minute} 分钟` : ""),
    },
  };

  const WRITE_SECTIONS = [
    { id: "world", no: "01", title: "世界概览", hint: "玩家第一眼看到的内容。先写清楚这里是什么地方。", collections: [] },
    { id: "home", no: "02", title: "地点", hint: "角色会在这里移动、采集和相遇。至少两个地点，并用路线连起来。", collections: ["locations"] },
    { id: "people", no: "03", title: "人物", hint: "居民和 NPC。名字、身份和目标决定他们怎样行动。", collections: ["agents"] },
    { id: "things", no: "04", title: "物品与组织", hint: "让世界更具体的物件、公共资源和群体。全部可选。", collections: ["objects", "resources", "factions"] },
    { id: "verbs", no: "05", title: "行动与配方", hint: "玩家和 NPC 能主动做的事。复制现有条目最稳妥。", collections: ["actions", "recipes"] },
  ];
  const EXTRA_COLLECTIONS = ["behavior_trees", "capabilities", "attributes", "relationships", "initial_events"];
  const OBJECT_SECTIONS = ["rules", "evolution", "story", "prompts"];
  const OBJECT_LABELS = { rules: "世界规则", evolution: "演化节奏", story: "故事生成", prompts: "提示词" };

  function setDraft(value, { increment = false, sourcePath = undefined, clearSource = false } = {}) {
    state.template = cloneValue(value || defaultTemplate());
    state.expandedCollections = new Set();
    state.openItem = null;
    if (clearSource) state.sourcePath = null;
    else if (sourcePath !== undefined) state.sourcePath = sourcePath;
    $("template-editor").value = JSON.stringify(state.template, null, 2);
    if (increment) state.draftRevision += 1;
    $("draft-revision").textContent = `revision ${state.draftRevision}`;
    $("authoring-source").textContent = state.sourcePath ? `来源：${state.sourcePath}` : "未保存的草稿";
    renderGuidedEditor();
    for (const field of [$("publish-output"), $("publish-instance")]) {
      if (field) delete field.dataset.touched;
    }
    fillPublishDefaults(true);
    document.querySelector(".write-rail")?.scrollTo({ top: 0 });
  }

  function worldField(key, labelText, kind, options = {}) {
    const label = make("label", `write-field${options.wide ? " wide" : ""}`);
    label.append(make("span", "write-field-label", labelText));
    const input = kind === "textarea" ? document.createElement("textarea") : document.createElement("input");
    if (kind === "number") input.type = "number";
    if (options.mono) input.classList.add("mono");
    input.value = state.template.world[key] ?? "";
    if (options.placeholder) input.placeholder = options.placeholder;
    input.addEventListener("input", () => {
      state.template.world[key] = kind === "number" ? Number(input.value || 0) : input.value;
      markDraftDirty();
    });
    label.append(input);
    if (options.hint) label.append(make("small", "write-field-hint", options.hint));
    return label;
  }

  function renderWorldSection() {
    const template = state.template;
    template.world = template.world && typeof template.world === "object" ? template.world : {};
    const section = make("section", "write-section write-world");
    section.id = "write-world";
    section.append(writeSectionHeader("01", "世界概览", "玩家进入世界时最先看到的内容。先写清楚这里是什么地方。"));

    const card = make("div", "write-card");
    const grid = make("div", "write-world-grid");
    grid.append(worldField("title", "世界名字", "input", { wide: true, placeholder: "给你的世界起个名字" }));
    grid.append(worldField("premise", "一句话设定", "textarea", { wide: true, placeholder: "这里的人为什么需要彼此？他们正面临什么？" }));
    grid.append(worldField("id", "世界 ID", "input", { mono: true, placeholder: "my_world", hint: "英文或下划线；决定默认的包目录名" }));
    grid.append(worldField("locale", "语言", "input", { placeholder: "zh-CN" }));
    card.append(grid);

    const more = document.createElement("details");
    more.className = "write-inline-more";
    more.append(make("summary", "", "更多世界设置"));
    const moreGrid = make("div", "write-world-grid");
    moreGrid.append(worldField("genre", "类型", "input", { placeholder: "自定义类型，可留空" }));
    moreGrid.append(worldField("start_world_minute", "开局分钟", "number", { placeholder: "0" }));
    moreGrid.append(worldField("time_unit", "时间单位", "input", { placeholder: "minute" }));
    more.append(moreGrid);
    card.append(more);
    section.append(card);
    section.append(renderPlayerEntry());
    return section;
  }

  function renderPlayerEntry() {
    const template = state.template;
    template.player_template = template.player_template && typeof template.player_template === "object" ? template.player_template : {};
    const player = template.player_template;
    const wrap = make("div", "write-card write-entry-card");
    wrap.id = "write-player-entry";
    const head = make("div", "write-entry-head");
    head.append(make("span", "eyebrow", "玩家入口"));
    head.append(make("strong", "", "玩家从哪开始"));
    head.append(make("p", "hint", "入口地点决定玩家第一次出现的位置；出身决定可选的开局目标。"));
    wrap.append(head);

    const grid = make("div", "write-world-grid");
    const selectLabel = make("label", "write-field");
    selectLabel.append(make("span", "write-field-label", "入口地点"));
    const select = document.createElement("select");
    select.append(new Option("选择地点", ""));
    for (const location of listValue(template.locations)) select.append(new Option(location.name || location.id, location.id));
    select.value = player.entry_location_id || "";
    select.disabled = !listValue(template.locations).length;
    select.addEventListener("change", () => {
      player.entry_location_id = select.value;
      markDraftDirty();
    });
    selectLabel.append(select);
    if (!listValue(template.locations).length) selectLabel.append(make("small", "write-field-hint", "先添加地点，才能设置入口。"));
    grid.append(selectLabel);
    wrap.append(grid);

    const origins = Array.isArray(player.origins) ? player.origins : [];
    const list = make("div", "write-origin-list");
    origins.forEach((origin, index) => {
      const row = make("div", "write-origin");
      const idInput = document.createElement("input");
      idInput.value = origin.id || "";
      idInput.placeholder = "出身 ID";
      idInput.addEventListener("input", () => { origin.id = idInput.value; markDraftDirty(); });
      const goalInput = document.createElement("input");
      goalInput.value = origin.starting_goal || "";
      goalInput.placeholder = "开局目标，例如：找到一位愿意收徒的工匠";
      goalInput.addEventListener("input", () => { origin.starting_goal = goalInput.value; markDraftDirty(); });
      const remove = make("button", "text-button", "移除");
      remove.type = "button";
      remove.addEventListener("click", () => {
        origins.splice(index, 1);
        markDraftDirty();
        renderGuidedEditor();
      });
      row.append(idInput, goalInput, remove);
      list.append(row);
    });
    if (!origins.length) list.append(make("p", "write-empty-note", "还没有出身。至少添加一个，玩家才能进入世界。"));
    wrap.append(list);

    const add = make("button", "button ghost small", "添加出身");
    add.type = "button";
    add.addEventListener("click", () => {
      if (!Array.isArray(player.origins)) player.origins = [];
      player.origins.push({ id: uniqueId(player.origins, "traveler"), traits: {}, starting_goal: "" });
      markDraftDirty();
      renderGuidedEditor();
      document.getElementById("write-player-entry")?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    wrap.append(add);
    return wrap;
  }

  function writeSectionHeader(no, title, hint) {
    const head = make("div", "write-section-head");
    head.append(make("span", "write-section-no", no));
    const copy = make("div", "write-section-copy");
    copy.append(make("h3", "", title));
    copy.append(make("p", "hint", hint));
    head.append(copy);
    return head;
  }

  function renderCollectionSection(meta) {
    const section = make("section", "write-section");
    section.id = `write-section-${meta.id}`;
    section.append(writeSectionHeader(meta.no, meta.title, meta.hint));
    for (const collection of meta.collections) section.append(renderCollectionBlock(collection, meta.collections.length > 1));
    return section;
  }

  function renderCollectionBlock(collection, showSubtitle = false) {
    const guide = COLLECTION_GUIDE[collection];
    const items = Array.isArray(state.template[collection]) ? state.template[collection] : null;
    if (!items || !guide) return make("div", "hidden");
    const block = make("div", "write-collection");
    block.id = `write-collection-${collection}`;

    const bar = make("div", "write-collection-bar");
    const count = make("div", "write-collection-count");
    if (showSubtitle) count.append(make("strong", "", guide.label));
    count.append(make("span", "write-count", `${items.length} 个`));
    bar.append(count);
    const add = make("button", "button ghost small", guide.addLabel);
    add.type = "button";
    add.disabled = Boolean(guide.clone) && !items.length;
    add.addEventListener("click", () => addCollectionItem(collection));
    bar.append(add);
    block.append(bar);

    if (showSubtitle) block.append(make("p", "write-collection-hint hint", guide.hint));

    const list = make("div", "write-items");
    if (!items.length) {
      const empty = make("div", "write-empty");
      empty.append(make("strong", "", `还没有${guide.label}。`));
      empty.append(make("p", "hint", guide.emptyHint));
      if (guide.clone) {
        const pick = make("button", "text-button", "从已验证模板开始");
        pick.type = "button";
        pick.addEventListener("click", openStartTools);
        empty.append(pick);
      }
      list.append(empty);
    }
    for (const field of guide.fields) {
      const options = typeof field.options === "function" ? field.options(state.template) : [];
      if (!options.length) continue;
      const datalist = document.createElement("datalist");
      datalist.id = `write-options-${collection}-${field.key}`;
      for (const value of options) datalist.append(new Option(String(value), String(value)));
      list.append(datalist);
    }
    const expanded = state.expandedCollections.has(collection);
    const limit = expanded ? items.length : Math.min(6, items.length);
    items.slice(0, limit).forEach((item, index) => list.append(renderCollectionItem(collection, item, index)));
    block.append(list);
    if (items.length > 6) {
      const more = make("button", "text-button wide write-show-more", expanded ? "收起" : `显示全部 ${items.length} 个${guide.label}`);
      more.type = "button";
      more.addEventListener("click", () => {
        if (expanded) state.expandedCollections.delete(collection);
        else state.expandedCollections.add(collection);
        renderGuidedEditor();
      });
      block.append(more);
    }
    return block;
  }

  function renderCollectionItem(collection, item, index) {
    const guide = COLLECTION_GUIDE[collection];
    const card = make("article", "write-item");
    card.dataset.section = collection;
    card.dataset.index = String(index);
    const initiallyOpen = Boolean(state.openItem && state.openItem.section === collection && state.openItem.index === index);
    if (initiallyOpen) card.classList.add("open");

    const head = make("button", "write-item-head");
    head.type = "button";
    const name = make("strong", "write-item-name", guide.title(item, index));
    const id = make("span", "write-item-id", item.id || "");
    const meta = make("span", "write-item-meta", guide.meta(item) || "");
    const action = make("span", "write-item-action", initiallyOpen ? "收起" : "编辑");
    head.append(name);
    if (item.id && item.id !== name.textContent) head.append(id);
    head.append(meta, action);
    head.addEventListener("click", () => {
      card.classList.toggle("open");
      action.textContent = card.classList.contains("open") ? "收起" : "编辑";
    });
    card.append(head);

    const body = make("div", "write-item-body");
    const grid = make("div", "write-fields");
    for (const field of guide.fields) grid.append(renderItemField(collection, item, field));
    body.append(grid);

    const guidedKeys = new Set(guide.fields.map((field) => field.key));
    const rest = {};
    for (const [key, value] of Object.entries(item)) {
      if (!guidedKeys.has(key)) rest[key] = value;
    }
    if (Object.keys(rest).length) {
      const more = document.createElement("details");
      more.className = "write-item-more";
      more.append(make("summary", "", "更多字段（不常用）"));
      more.append(make("p", "hint", "这里保留模板的完整结构。修改后立即写回草稿。"));
      const editor = document.createElement("textarea");
      editor.value = JSON.stringify(rest, null, 2);
      const error = make("p", "write-field-error", "");
      const known = new Set(Object.keys(rest));
      editor.addEventListener("input", () => {
        try {
          const parsed = JSON.parse(editor.value || "{}");
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("需要一个 JSON 对象");
          for (const key of known) delete item[key];
          Object.assign(item, parsed);
          known.clear();
          for (const key of Object.keys(parsed)) known.add(key);
          error.textContent = "";
          markDraftDirty();
        } catch (rawError) {
          error.textContent = `JSON 错误：${rawError.message}`;
        }
      });
      more.append(editor, error);
      body.append(more);
    }

    const footer = make("div", "write-item-footer");
    const remove = make("button", "text-button", "移除");
    remove.type = "button";
    remove.addEventListener("click", () => removeCollectionItem(collection, index));
    footer.append(remove);
    body.append(footer);
    card.append(body);
    return card;
  }

  function renderItemField(collection, item, field) {
    const label = make("label", `write-field${field.wide ? " wide" : ""}${field.type === "bool" ? " write-checkbox" : ""}`);
    label.append(make("span", "write-field-label", field.label));
    let input;
    if (field.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = 2;
      input.value = item[field.key] ?? "";
    } else if (field.type === "list") {
      input = document.createElement("input");
      input.value = listText(item[field.key]);
    } else if (field.type === "number") {
      input = document.createElement("input");
      input.type = "number";
      input.value = item[field.key] ?? 0;
    } else if (field.type === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(item[field.key]);
    } else {
      input = document.createElement("input");
      input.value = item[field.key] ?? "";
    }
    if (field.mono) input.classList.add("mono");
    if (field.placeholder) input.placeholder = field.placeholder;
    if (field.options) {
      const id = `write-options-${collection}-${field.key}`;
      if (document.getElementById(id)) input.setAttribute("list", id);
    }
    const apply = () => {
      if (field.type === "bool") item[field.key] = input.checked;
      else if (field.type === "list") item[field.key] = parseListText(input.value);
      else if (field.type === "number") item[field.key] = Number(input.value || 0);
      else item[field.key] = input.value;
      const card = label.closest(".write-item");
      const name = card?.querySelector(".write-item-name");
      if (name) name.textContent = COLLECTION_GUIDE[collection].title(item, Number(card.dataset.index || 0));
      markDraftDirty();
    };
    input.addEventListener(field.type === "bool" ? "change" : "input", apply);
    label.append(input);
    if (field.hint) label.append(make("small", "write-field-hint", field.hint));
    return label;
  }

  function addCollectionItem(collection) {
    const guide = COLLECTION_GUIDE[collection];
    const items = state.template[collection];
    if (!guide || !Array.isArray(items)) return;
    const item = guide.create(state.template);
    if (!item) return;
    items.push(item);
    state.expandedCollections.add(collection);
    state.draftRevision += 1;
    $("draft-revision").textContent = `revision ${state.draftRevision}`;
    state.openItem = { section: collection, index: items.length - 1 };
    markDraftDirty();
    renderGuidedEditor();
    const card = document.querySelector(`.write-item[data-section="${collection}"][data-index="${items.length - 1}"]`);
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function removeCollectionItem(collection, index) {
    const items = state.template[collection];
    const guide = COLLECTION_GUIDE[collection];
    if (!guide || !Array.isArray(items) || !items[index]) return;
    if (!window.confirm(`移除「${guide.title(items[index], index)}」？这个操作只影响草稿。`)) return;
    items.splice(index, 1);
    state.draftRevision += 1;
    $("draft-revision").textContent = `revision ${state.draftRevision}`;
    markDraftDirty();
    renderGuidedEditor();
  }

  function renderAdvancedGroup() {
    const details = document.createElement("details");
    details.className = "write-advanced";
    details.id = "write-advanced";
    details.append(make("summary", "", "更多内容：行为树、能力、属性、关系、开局事件、规则"));
    const body = make("div", "write-advanced-body");
    body.append(make("p", "hint", "这些结构通常由模板提供。新手可以只看不改；需要时在这里补充。"));
    for (const collection of EXTRA_COLLECTIONS) body.append(renderCollectionBlock(collection, true));
    for (const section of OBJECT_SECTIONS) body.append(renderObjectSection(section));
    details.append(body);
    return details;
  }

  function renderObjectSection(section) {
    const details = document.createElement("details");
    details.className = "write-object-card";
    details.id = `write-collection-${section}`;
    details.append(make("summary", "", OBJECT_LABELS[section] || section));
    const editor = document.createElement("textarea");
    editor.value = JSON.stringify(state.template[section] || {}, null, 2);
    const error = make("p", "write-field-error", "");
    editor.addEventListener("input", () => {
      try {
        const parsed = JSON.parse(editor.value || "{}");
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("需要一个 JSON 对象");
        state.template[section] = parsed;
        error.textContent = "";
        markDraftDirty();
      } catch (rawError) {
        error.textContent = `JSON 错误：${rawError.message}`;
      }
    });
    const body = make("div", "write-object-body");
    body.append(make("p", "hint", "对象结构，修改后立即写回草稿。"), editor, error);
    details.append(body);
    return details;
  }

  function renderGuidedEditor() {
    const root = $("guided-editor");
    if (!root) return;
    root.replaceChildren();
    if (!state.template) state.template = defaultTemplate();
    for (const meta of WRITE_SECTIONS) {
      root.append(meta.id === "world" ? renderWorldSection() : renderCollectionSection(meta));
    }
    root.append(renderAdvancedGroup());
    state.openItem = null;
    renderAuthoringState();
  }

  function readinessChecks() {
    const template = state.template || defaultTemplate();
    const world = template.world || {};
    const locations = listValue(template.locations);
    const locationIds = new Set(locations.map((item) => item.id));
    const routeTargets = locations.flatMap((item) => listValue(item.routes));
    const player = template.player_template || {};
    const origins = listValue(player.origins);
    const title = String(world.title || "").trim();
    const routesOk = routeTargets.length > 0 && routeTargets.every((id) => locationIds.has(id));
    const entryLocation = locations.find((item) => item.id === player.entry_location_id);
    const entryOk = Boolean(entryLocation) && origins.length > 0;
    return [
      { key: "title", label: "世界有名字", detail: "玩家第一眼看到的世界名。", ok: Boolean(title) && title !== "未命名世界", target: "write-world" },
      { key: "premise", label: "有一句话设定", detail: "用一句话说清这里发生什么。", ok: Boolean(String(world.premise || "").trim()), target: "write-world" },
      { key: "locations", label: "至少两个地点", detail: "一个落脚点，一个可以去的地方。", ok: locations.length >= 2, target: "write-collection-locations" },
      {
        key: "routes",
        label: "地点之间有路线",
        detail: routesOk
          ? `${routeTargets.length} 条路线，全部指向现有地点。`
          : (routeTargets.length ? "有路线指向不存在的地点，请检查地点 ID。" : "没有路线，角色无法移动。"),
        ok: routesOk,
        target: "write-collection-locations",
      },
      { key: "agents", label: "至少一位居民", detail: "世界需要有人行动。", ok: listValue(template.agents).length >= 1, target: "write-collection-agents" },
      { key: "actions", label: "至少一个行动", detail: "玩家需要有能做的事。", ok: listValue(template.actions).length >= 1, target: "write-collection-actions" },
      {
        key: "entry",
        label: "玩家入口有效",
        detail: entryOk
          ? `玩家会从「${entryLocation.name || entryLocation.id}」进入，共 ${origins.length} 个出身。`
          : (!locations.length ? "先加地点，再选择玩家入口。" : (!origins.length ? "至少添加一个出身。" : "入口地点需要在当前地点列表里。")),
        ok: entryOk,
        target: "write-player-entry",
      },
      { key: "bt", label: "至少一棵行为树", detail: "NPC 自动行动需要它；已验证模板通常自带。", ok: listValue(template.behavior_trees).length >= 1, target: "write-collection-behavior_trees" },
    ];
  }

  function renderAuthoringState() {
    if (!$("authoring-readiness")) return;
    const checks = readinessChecks();
    const byKey = Object.fromEntries(checks.map((check) => [check.key, check]));
    const progress = [
      { key: "world", done: byKey.title.ok && byKey.premise.ok },
      { key: "locations", done: byKey.locations.ok && byKey.routes.ok },
      { key: "agents", done: byKey.agents.ok },
      { key: "actions", done: byKey.actions.ok },
      { key: "entry", done: byKey.entry.ok },
    ];
    const doneCount = progress.filter((step) => step.done).length;
    $("authoring-progress-label").textContent = `${doneCount} / 5 项`;
    $("authoring-progress-note").textContent = doneCount === 5 ? "可以试玩了" : "按顺序补齐即可";
    $("authoring-progress-bar").style.width = `${(doneCount / 5) * 100}%`;
    for (const button of document.querySelectorAll("[data-authoring-step]")) {
      const step = progress.find((item) => item.key === button.dataset.authoringStep);
      button.classList.toggle("done", Boolean(step && step.done));
      const stateNode = button.querySelector(".write-step-state");
      if (stateNode) stateNode.textContent = step && step.done ? "●" : "○";
    }

    const next = checks.find((check) => !check.ok);
    const nextTitle = $("authoring-next-title");
    const nextDetail = $("authoring-next-detail");
    const nextButton = $("authoring-next-go");
    if (next) {
      nextTitle.textContent = `还差：${next.label}`;
      nextDetail.textContent = next.detail;
      nextButton.textContent = "去填写";
      nextButton.dataset.target = next.target;
    } else {
      nextTitle.textContent = "全部就绪";
      nextDetail.textContent = "可以运行完整检查，或直接生成并打开游戏。";
      nextButton.textContent = "运行完整检查";
      nextButton.dataset.target = "compile";
    }

    const readiness = $("authoring-readiness");
    readiness.replaceChildren();
    const passedCount = checks.filter((check) => check.ok).length;
    const score = make("div", "readiness-score");
    score.append(make("strong", "", `${passedCount} / ${checks.length}`));
    score.append(make("span", "", "项就绪"));
    readiness.append(score);
    const rows = make("div", "readiness-list");
    for (const check of checks) {
      const row = make("button", `readiness-item ${check.ok ? "ok" : "todo"}`);
      row.type = "button";
      row.append(make("span", "readiness-mark", check.ok ? "✓" : "○"));
      const copy = make("span", "readiness-copy");
      copy.append(make("strong", "", check.label));
      copy.append(make("small", "", check.detail));
      row.append(copy);
      if (!check.ok) row.append(make("span", "readiness-go", "去填写"));
      row.addEventListener("click", () => gotoAuthoring(check.target));
      rows.append(row);
    }
    if (passedCount === checks.length) {
      readiness.append(make("p", "readiness-done", "全部就绪，可以发布。"));
      const details = document.createElement("details");
      details.className = "readiness-details";
      details.append(make("summary", "", "查看检查项"));
      details.append(rows);
      readiness.append(details);
    } else {
      readiness.append(rows);
    }

    const strip = $("start-strip");
    if (strip) {
      const empty = !listValue(state.template?.locations).length
        && !listValue(state.template?.agents).length
        && !listValue(state.template?.actions).length;
      strip.classList.toggle("hidden", !empty);
    }
    fillPublishDefaults(false);
  }

  function gotoAuthoring(target) {
    if (!target) return;
    if (target === "compile") {
      $("compile-draft").click();
      return;
    }
    const node = document.getElementById(target);
    if (!node) return;
    const details = node.closest("details");
    if (details) details.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
    if (node.classList.contains("write-item")) node.classList.add("open");
  }

  function markDraftDirty() {
    state.draftDirty = true;
    const status = $("draft-status");
    if (status) status.textContent = "草稿有修改";
    const dot = $("draft-dot");
    if (dot) dot.className = "status-dot busy";
    renderAuthoringState();
  }

  function sourcePackageName() {
    const source = String(state.sourcePath || "").replace(/[\\/]+$/, "");
    if (!source) return "";
    return source.split(/[\\/]/).filter(Boolean).pop() || "";
  }

  function fillPublishDefaults(force = false) {
    const output = $("publish-output");
    const instance = $("publish-instance");
    if (!output || !instance) return;
    const worldId = String(state.template?.world?.id || "").trim();
    const sourceName = sourcePackageName();
    let fallback = worldId && worldId !== "new_world" ? worldId : "";
    if (fallback && sourceName && fallback === sourceName) fallback = `${fallback}_draft`;
    if (force || !output.dataset.touched) output.value = fallback;
    const instanceBase = output.dataset.touched ? output.value.trim() : fallback;
    if (force || !instance.dataset.touched) instance.value = instanceBase ? `${instanceBase}_01` : "";
  }

  function parseDraft() {
    try {
      if (state.editorMode === "guided") return { value: cloneValue(state.template || defaultTemplate()), error: null };
      return { value: JSON.parse($("template-editor").value), error: null };
    } catch (error) {
      return { value: null, error: `JSON 格式错误：${error.message}` };
    }
  }

  function renderSchema() {
    const list = $("schema-list");
    if (!list) return;
    list.replaceChildren();
    const schema = state.schema || {};
    const properties = schema.properties || {};
    const required = new Set(schema.required || []);
    for (const [key, value] of Object.entries(properties)) {
      const item = make("button", `schema-item ${required.has(key) ? "required" : ""}`, `${key} · ${value.type || "value"}`);
      item.type = "button";
      item.addEventListener("click", () => {
        if (state.editorMode !== "raw") setEditorMode("raw");
        const editor = $("template-editor");
        editor.focus();
        editor.setSelectionRange(0, 0);
        editor.scrollTop = Math.max(0, editor.value.indexOf(`"${key}"`) * 0.5);
      });
      list.append(item);
    }
  }

  function draftHasContent() {
    const template = state.template || {};
    const collections = ["locations", "agents", "actions", "objects", "resources", "factions", "recipes", "behavior_trees", "relationships", "initial_events"];
    return Boolean(
      String(template.world?.title || "").trim()
      || String(template.world?.premise || "").trim()
      || collections.some((key) => listValue(template[key]).length)
    );
  }

  function confirmDraftReplace(message = "当前草稿会被替换，继续？") {
    return !draftHasContent() || window.confirm(message);
  }

  function openStartTools() {
    const more = document.querySelector(".write-more");
    if (more) more.open = true;
    more?.scrollIntoView({ behavior: "smooth", block: "start" });
    window.setTimeout(() => $("start-package")?.focus(), 200);
  }

  async function copyTemplateFromPackage() {
    const selected = $("start-package")?.value || "";
    const pkg = listValue(state.packages).find((item) => item.id === selected);
    if (!pkg) {
      $("draft-status").textContent = "先选择一个模板";
      return;
    }
    if (!confirmDraftReplace("当前草稿会被模板替换，继续？")) return;
    await withPending($("start-copy"), async () => {
      try {
        const result = await request("/authoring/open", { method: "POST", body: JSON.stringify({ path: pkg.path }) });
        if (result.status !== "passed") {
          renderDiagnostics(result);
          $("draft-status").textContent = "打开失败";
          return;
        }
        setDraft(result.template, { sourcePath: result.source_path, increment: true });
        $("draft-status").textContent = `已复制 ${result.description?.world_id || pkg.id}`;
        $("draft-dot").className = "status-dot";
        renderDiagnostics({ status: "passed", description: result.description });
        const more = document.querySelector(".write-more");
        if (more) more.open = false;
        document.querySelector(".write-rail")?.scrollTo({ top: 0 });
        $("write-world")?.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (error) {
        renderDiagnostics({ status: "rejected", errors: [error.message] });
        $("draft-status").textContent = "复制失败";
      }
    });
  }

  function setInstanceResult(message, ok) {
    const box = $("instance-result");
    if (!box) return;
    box.textContent = message || "";
    box.className = `interaction-result ${ok ? "ok" : "error"}`;
  }

  async function publishWorld(openAfter, button) {
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
        const result = await write("/authoring/build-world", {
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
        const activation = await write("/instances/activate", { instance_id: instanceId });
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

  function renderDiagnostics(result, extra = null) {
    const box = $("diagnostics"); box.replaceChildren();
    const passed = result && result.status === "passed";
    const summary = make("div", `diagnostic-summary ${passed ? "" : "error"}`, passed ? "✓ 当前草稿通过编译检查" : "! 当前草稿需要修正"); box.append(summary);
    for (const error of (result && (result.diagnostics || result.errors)) || []) {
      const message = typeof error === "object" ? `${error.path || "template"}: ${error.message || "invalid"}` : error;
      box.append(make("div", "diagnostic-line", message));
    }
    for (const warning of (result && result.warnings) || []) {
      const text = String(warning).replace(
        /^Imported package passed package validation; (\d+) unified-template advisories are source-format specific\.$/,
        "已按导入包校验；$1 条模板格式提示不影响运行。",
      );
      box.append(make("div", "diagnostic-line warning", `兼容提示：${text}`));
    }
    if (extra) {
      const smoke = extra.smoke;
      if (smoke) box.append(make("div", "diagnostic-line", `试玩 ${smoke.status === "passed" ? "通过" : "未通过"} · ${smoke.minutes} 分钟 · ${(smoke.event_types || []).join("、") || "没有新事件"}`));
      if (extra.files && !extra.package_format) box.append(make("div", "diagnostic-line", `已生成 ${extra.files.length} 个世界包文件`));
    }
    if (passed && !(result.errors || []).length && !extra) box.append(make("div", "diagnostic-line", result.description ? `${result.description.world_id || "world"} · 配置可执行` : "结构、引用和执行性检查通过"));
  }

  async function compileDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    state.draftRevision += 1; $("draft-revision").textContent = `revision ${state.draftRevision}`;
    $("draft-dot").className = "status-dot busy"; $("draft-status").textContent = "编译中";
    try { const result = await request("/authoring/compile", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision }) }); renderDiagnostics(result); state.draftDirty = false; $("draft-dot").className = `status-dot ${result.status === "passed" ? "" : "offline"}`; $("draft-status").textContent = result.status === "passed" ? "已通过检查" : "有待修正项"; }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-dot").className = "status-dot offline"; $("draft-status").textContent = "编译失败"; }
  }

  async function smokeDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    $("draft-status").textContent = "试玩运行中"; $("draft-dot").className = "status-dot busy";
    try { const result = await request("/authoring/smoke", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision, minutes: 3 }) }); renderDiagnostics(result, result); $("draft-dot").className = `status-dot ${result.status === "passed" ? "" : "offline"}`; $("draft-status").textContent = result.status === "passed" ? "试玩通过" : "试玩失败"; }
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
      $("template-editor").value = JSON.stringify(state.template || defaultTemplate(), null, 2);
    } else {
      try {
        const parsed = JSON.parse($("template-editor").value);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("模板必须是一个 JSON 对象");
        state.template = parsed;
        renderGuidedEditor();
      } catch (error) {
        renderDiagnostics({ status: "rejected", errors: [`JSON 格式错误：${error.message}`] });
        return;
      }
    }
    state.editorMode = mode;
    $("guided-editor").classList.toggle("hidden", mode !== "guided");
    $("template-editor").classList.toggle("hidden", mode !== "raw");
    $("guided-mode").classList.toggle("active", mode === "guided");
    $("raw-mode").classList.toggle("active", mode === "raw");
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
    if (!confirmDraftReplace("AI 起草会替换当前草稿，继续？")) return;
    $("draft-status").textContent = "WorldBuilder 运行中"; $("draft-dot").className = "status-dot busy";
    try { const result = await request("/authoring/build", { method: "POST", body: JSON.stringify({ brief }) }); if (result.status !== "passed") { renderDiagnostics(result); $("draft-status").textContent = "起草失败"; $("draft-dot").className = "status-dot offline"; return; } setDraft(result.template, { increment: true, clearSource: true }); $("draft-status").textContent = `已生成草稿 · ${result.audit?.iterations || "?"} 轮`; $("draft-dot").className = "status-dot"; renderDiagnostics({ status: "passed", description: { world_id: result.template?.world?.id || "draft" } }); }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-status").textContent = "起草失败"; $("draft-dot").className = "status-dot offline"; }
  }

  function renderPackageCatalog(response, message = "没有可用模板") {
    const packages = response?.packages || [];
    state.packages = packages;
    const valid = packages.filter((item) => item.status === "passed");
    for (const id of ["package-select", "start-package"]) {
      const select = $(id);
      if (!select) continue;
      const previous = select.value;
      select.replaceChildren();
      if (!valid.length) {
        select.append(new Option(message, ""));
        continue;
      }
      for (const item of valid) select.append(new Option(`${item.name || item.id} · ${item.id}`, item.id));
      if (previous && valid.some((item) => item.id === previous)) select.value = previous;
    }
  }

  async function loadPackageCatalog() {
    state.catalogRequest += 1;
    const requestId = state.catalogRequest;
    try {
      const response = await request("/authoring/catalog");
      if (requestId !== state.catalogRequest) return;
      renderPackageCatalog(response);
    } catch (error) {
      if (requestId !== state.catalogRequest) return;
      if (!listValue(state.packages).length) renderPackageCatalog({ packages: [] }, "模板目录不可用");
    }
  }

  async function createNewInstance() {
    const packageId = $("package-select")?.value || "";
    const instanceId = $("instance-id")?.value.trim() || "";
    const resultBox = $("instance-result");
    if (!packageId || !instanceId) { resultBox.textContent = "请选择模板并填写实例 ID"; resultBox.className = "interaction-result error"; return; }
    if (!(await ensureOperator())) return;
    resultBox.textContent = "创建中…"; resultBox.className = "interaction-result";
    try {
      const result = await write("/instances/init", { package_id: packageId, instance_id: instanceId, activate: true });
      if (result.status !== "passed") throw new Error(result.message || (result.errors || ["创建失败"]).join("; "));
      resultBox.textContent = `已创建 ${result.instance_id}，正在打开…`;
      resultBox.className = "interaction-result ok";
      if (result.activation && result.activation.status !== "accepted") {
        throw new Error(result.activation.message || "实例已创建，但切换失败");
      }
      window.location.reload();
    } catch (error) { resultBox.textContent = error.message || "创建失败"; resultBox.className = "interaction-result error"; }
  }

  function setConsoleResult(value) {
    const box = $("console-result");
    if (!box) return;
    box.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  function consoleNotice(message, ok) {
    const notice = $("console-notice");
    if (!notice) return;
    notice.textContent = message || "";
    notice.className = `notice ${ok ? "notice-ok" : ""}`;
    notice.classList.toggle("hidden", !message);
  }

  function parseBundle(text) {
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

  async function runConsole(label, fn, { operator = true } = {}) {
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

  async function downloadSnapshot(snapshotId, button) {
    if (!snapshotId) return;
    if (!(await ensureOperator())) return;
    await withPending(button, async () => {
      const sessionToken = state.operatorToken || state.playerToken || state.observerToken || state.sessionToken;
      const response = await fetch(`/snapshots/download?snapshot_id=${encodeURIComponent(snapshotId)}`, {
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

  async function refreshConsoleInstances() {
    const result = await runConsole("刷新实例与快照", async () => {
      const [instances, snapshots] = await Promise.all([request("/instances"), request("/snapshots")]);
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

  async function init() {
    const handoff = readHandoff();
    const initial = defaultTemplate();
    setDraft(initial);
    setEditorMode("guided");
    document.querySelectorAll(".mode-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
    $("refresh-button").addEventListener("click", () => refreshRuntime(true));
    $("start-button").addEventListener("click", () => startRuntime().catch((error) => showNotice(error.message || "自动运行未启动")));
    $("pause-button").addEventListener("click", () => control("/runtime/pause"));
    $("resume-button").addEventListener("click", () => control("/runtime/resume"));
    $("stop-button").addEventListener("click", () => control("/runtime/stop"));
    $("step-button").addEventListener("click", () => control("/runtime/step"));
    $("toggle-ticks").addEventListener("click", () => { state.includeTicks = !state.includeTicks; $("toggle-ticks").textContent = state.includeTicks ? "隐藏 tick" : "显示 tick"; refreshRuntime(true); });
    $("dialogue-refresh").addEventListener("click", renderNpcDialogue);
    $("decisions-refresh").addEventListener("click", renderNpcDecisions);
    $("instance-select").addEventListener("change", (event) => activateInstance(event.target.value).catch((error) => showNotice(error.message || "实例切换失败")));
    $("operator-unlock").addEventListener("click", () => openOperatorDialog(true));
    $("back-to-game").addEventListener("click", () => {
      if (window.parent !== window) {
        try { window.parent.postMessage({ type: "sim2:close-editor" }, location.origin); }
        catch (error) { /* fall through to navigation */ }
        return;
      }
      window.location.href = "/";
    });
    // Handle the form submit (not the button click): Enter/autofill/mobile-go
    // submit the method="dialog" form natively, which would close the dialog
    // without unlocking. Centralizing here covers click and keyboard alike.
    $("operator-dialog").querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      unlockOperator($("operator-token").value).catch((error) => { $("operator-error").textContent = error.message || "解锁失败"; });
    });
    $("operator-cancel").addEventListener("click", () => $("operator-dialog").close());
    $("operator-dialog").addEventListener("close", () => { $("operator-token").value = ""; $("operator-error").textContent = ""; if (state.operatorResolve) state.operatorResolve(false); state.operatorResolve = null; });
    $("open-player").addEventListener("click", () => switchView("play-view"));
    $("play-enter").addEventListener("click", enterPlayer);
    $("play-player-origin").addEventListener("change", updateOriginPreview);
    $("player-refresh").addEventListener("click", loadPlayerView);
    $("player-send-dialogue").addEventListener("click", (event) => sendPlayerDialogue(event.currentTarget));
    $("player-send-trade").addEventListener("click", (event) => submitPlayerTrade(event.currentTarget));
    $("player-trades-refresh").addEventListener("click", renderPlayerTrades);
    $("player-leave").addEventListener("click", () => {
      state.playerId = null; state.playerData = null; state.playerToken = null; state.sessionToken = state.observerToken;
      localStorage.removeItem("sim2_player_id"); localStorage.removeItem("sim2_player_token");
      $("player-world").classList.add("hidden"); $("player-setup").classList.remove("hidden"); setPlayerResult("已退出当前角色。", true);
    });
    $("send-dialogue").addEventListener("click", (event) => sendDialogue(event.currentTarget));
    $("create-instance").addEventListener("click", () => createNewInstance().catch((error) => { $("instance-result").textContent = error.message || "创建失败"; $("instance-result").className = "interaction-result error"; }));
    $("new-draft").addEventListener("click", () => {
      if (!confirmDraftReplace("当前草稿会被清空，继续？")) return;
      setDraft(defaultTemplate(), { increment: true, clearSource: true });
    });
    $("open-current").addEventListener("click", operatorAction(() => {
      if (!confirmDraftReplace()) return;
      return openPackage();
    }));
    $("open-path").addEventListener("click", operatorAction(() => {
      if (!confirmDraftReplace()) return;
      return openPackage($("authoring-path").value.trim());
    }));
    $("format-draft").addEventListener("click", () => { const parsed = parseDraft(); if (!parsed.error) setDraft(parsed.value); else renderDiagnostics({ status: "rejected", errors: [parsed.error] }); });
    $("guided-mode").addEventListener("click", () => setEditorMode("guided"));
    $("raw-mode").addEventListener("click", () => setEditorMode("raw"));
    $("start-copy").addEventListener("click", operatorAction(copyTemplateFromPackage));
    document.querySelector(".write-more > summary")?.addEventListener("click", () => { loadPackageCatalog(); });
    $("start-strip-open").addEventListener("click", openStartTools);
    for (const button of document.querySelectorAll("[data-authoring-step]")) {
      button.addEventListener("click", () => {
        const target = {
          world: "write-world",
          locations: "write-collection-locations",
          agents: "write-collection-agents",
          actions: "write-collection-actions",
          entry: "write-player-entry",
        }[button.dataset.authoringStep];
        gotoAuthoring(target);
      });
    }
    $("authoring-next-go").addEventListener("click", () => gotoAuthoring($("authoring-next-go").dataset.target || "write-world"));
    $("publish-output").addEventListener("input", () => {
      $("publish-output").dataset.touched = "1";
      fillPublishDefaults(false);
    });
    $("publish-instance").addEventListener("input", () => { $("publish-instance").dataset.touched = "1"; });
    $("publish-build").addEventListener("click", (event) => publishWorld(false, event.currentTarget));
    $("publish-play").addEventListener("click", (event) => publishWorld(true, event.currentTarget));
    $("build-world").addEventListener("click", operatorAction(buildWorldFromBrief));
    $("compile-draft").addEventListener("click", operatorAction(compileDraft));
    $("smoke-draft").addEventListener("click", operatorAction(smokeDraft));
    $("export-draft").addEventListener("click", operatorAction(exportDraft));
    $("console-refresh").addEventListener("click", refreshConsoleInstances);
    $("console-clear").addEventListener("click", () => setConsoleResult("等待命令…"));
    $("console-init").addEventListener("click", () => runConsole("初始化世界", async () => {
      const packageId = $("console-init-package").value.trim() || $("package-select")?.value || "";
      const instanceId = $("console-init-instance").value.trim();
      if (!packageId || !instanceId) throw new Error("需要填写世界模板 ID 和新实例 ID");
      const result = await write("/instances/init", { package_id: packageId, instance_id: instanceId, activate: $("console-init-activate").checked });
      if (result.activation?.status === "accepted") window.location.reload();
      await loadInstances();
      return result;
    }));
    $("console-snapshot").addEventListener("click", () => runConsole("创建快照", async () => write("/snapshots", { label: $("console-snapshot-label").value.trim() || "manual" })));
    $("console-snapshots").addEventListener("click", refreshConsoleInstances);
    $("console-restore").addEventListener("click", () => runConsole("恢复快照", async () => {
      const snapshotId = $("console-restore-id").value.trim();
      if (!snapshotId) throw new Error("需要填写快照 ID");
      const result = await write("/restore", { snapshot_id: snapshotId });
      await refreshRuntime(true);
      return result;
    }));
    $("console-simulate").addEventListener("click", () => runConsole("同步推演", async () => write("/simulate", { minutes: Number($("console-sim-minutes").value || 10), provider: $("console-sim-provider").value.trim() || null })));
    $("console-decide").addEventListener("click", () => runConsole("单步决策", async () => {
      const agentId = $("console-decide-agent").value.trim();
      if (!agentId) throw new Error("需要填写 Agent ID");
      return write("/decide", { agent_id: agentId, role: $("console-decide-role").value.trim() || "agent_decision" });
    }));
    $("console-status").addEventListener("click", () => runConsole("status", async () => request("/status"), { operator: false }));
    $("console-health").addEventListener("click", () => runConsole("health", async () => request("/health"), { operator: false }));
    $("console-metrics").addEventListener("click", () => runConsole("metrics", async () => request("/metrics"), { operator: false }));
    $("console-audit").addEventListener("click", () => runConsole("audit", async () => request("/audit")));
    $("console-validate").addEventListener("click", () => runConsole("校验世界包", async () => write("/authoring/validate", { package_id: $("console-pkg-id").value.trim() || undefined, path: $("console-pkg-path").value.trim() || undefined })));
    $("console-describe").addEventListener("click", () => runConsole("世界包详情", async () => write("/authoring/describe", { package_id: $("console-pkg-id").value.trim() || undefined, path: $("console-pkg-path").value.trim() || undefined })));
    $("console-schema").addEventListener("click", () => runConsole("world-schema", async () => request("/authoring/schema"), { operator: false }));
    $("console-build-world").addEventListener("click", () => runConsole("创建世界包", async () => {
      const parsed = parseDraft();
      if (parsed.error) throw new Error(parsed.error);
      return write("/authoring/build-world", { template: parsed.value, output: $("console-build-output").value.trim(), overwrite: $("console-build-overwrite").checked, instance_id: $("console-build-instance").value.trim() || undefined, source_path: state.sourcePath || undefined });
    }));
    $("console-trade-list").addEventListener("click", () => runConsole("列出交易", async () => {
      const agentId = $("console-trade-agent").value.trim() || state.playerId || "";
      if (!agentId) throw new Error("需要填写 Agent ID（或先进入世界）");
      return request(`/trade/offers?agent_id=${encodeURIComponent(agentId)}&include_closed=1`);
    }, { operator: false }));
    const tradeAct = (kind) => runConsole(kind, async () => {
      const me = $("console-trade-me").value.trim() || state.playerId || "";
      const peer = $("console-trade-peer").value.trim();
      const offerId = $("console-offer-id").value.trim();
      if (!me) throw new Error("需要填写我方 Agent ID（或先进入世界）");
      if (kind === "offer") {
        if (!peer) throw new Error("发起交易需要对方 ID");
        return write("/trade/offers", { offerer_id: me, recipient_id: peer, give: parseBundle($("console-trade-give").value), want: parseBundle($("console-trade-want").value) });
      }
      if (!offerId) throw new Error("需要填写 Offer ID");
      if (kind === "counter") return write("/trade/counter", { counterparty_id: me, offer_id: offerId, give: parseBundle($("console-trade-give").value), want: parseBundle($("console-trade-want").value) });
      if (kind === "accept") return write("/trade/accept", { recipient_id: me, offer_id: offerId });
      return write("/trade/reject", { recipient_id: me, offer_id: offerId });
    }, { operator: false });
    $("console-trade-offer").addEventListener("click", () => tradeAct("offer"));
    $("console-trade-counter").addEventListener("click", () => tradeAct("counter"));
    $("console-trade-accept").addEventListener("click", () => tradeAct("accept"));
    $("console-trade-reject").addEventListener("click", () => tradeAct("reject"));
    $("console-inject").addEventListener("click", () => runConsole("注入事件", async () => {
      const eventType = $("console-event-type").value.trim();
      if (!eventType) throw new Error("需要填写事件类型");
      let payload = {};
      try { payload = $("console-event-payload").value.trim() ? JSON.parse($("console-event-payload").value) : {}; }
      catch (error) { throw new Error(`Payload JSON 错误：${error.message}`); }
      return write("/events", { event_type: eventType, payload });
    }));
    state.operatorToken = handoff.token || readStoredOperatorToken();
    if (handoff.token) rememberOperatorToken(handoff.token);
    setOperatorState(Boolean(state.operatorToken));
    if (!storageWritable()) {
      showNotice("浏览器阻止了本地存储：解锁状态仅当前页面有效，刷新或重开后需重新解锁。");
    }
    try { const session = await request("/session"); state.observerToken = session.session_token || null; state.sessionToken = state.observerToken; } catch (error) { /* public projections remain readable in local read-only mode */ }
    let savedPlayer = null, savedPlayerToken = null;
    try { savedPlayer = localStorage.getItem("sim2_player_id"); savedPlayerToken = localStorage.getItem("sim2_player_token"); } catch (error) { /* ignore */ }
    if (savedPlayer && savedPlayerToken) { state.playerId = savedPlayer; state.playerToken = savedPlayerToken; state.sessionToken = savedPlayerToken; const restored = await loadPlayerView(); if (!restored) { state.playerId = null; state.playerToken = null; state.sessionToken = state.observerToken; try { localStorage.removeItem("sim2_player_id"); localStorage.removeItem("sim2_player_token"); } catch (error) { /* ignore */ } } }
    try { const response = await request("/authoring/schema"); state.schema = response.schema; renderSchema(); } catch (error) { $("schema-list").replaceChildren(make("div", "empty-state", "Schema 暂不可用")); }
    await Promise.all([loadPackageCatalog(), loadPlayerEntry(), loadInstances()]);
    await refreshRuntime(true);
    startStream();
    const requestedView = resolveView(handoff.view);
    if (requestedView) switchView(requestedView);
    state.poll = window.setInterval(() => { if ($("runtime-view").classList.contains("active-view")) refreshRuntime(false); else if ($("play-view").classList.contains("active-view") && state.playerId) loadPlayerView(); }, 4000);
  }

  window.addEventListener("DOMContentLoaded", init);
})();

