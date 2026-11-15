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
    const playerId = $("play-player-id")?.value.trim();
    const originId = $("play-player-origin")?.value;
    const name = $("play-player-name")?.value.trim();
    const resultBox = $("play-notice") || $("interaction-result");
    if (!playerId || !originId) { setPlayerResult("需要填写玩家 ID 并选择出身", false); return; }
    try {
      const result = await request("/players", { method: "POST", body: JSON.stringify({ player_id: playerId, origin_id: originId, name: name || undefined }) });
      if (result.status !== "accepted") throw new Error(result.message || "玩家进入失败");
      state.playerId = playerId;
      await loadPlayerView();
      switchView("play-view");
      setPlayerResult("你已经进入世界。先看看周围，再选择下一步。", true);
    } catch (error) { setPlayerResult(error.message || "玩家进入失败", false); }
  }

  async function loadPlayerEntry() {
    try {
      const entry = await request("/player/entry");
      state.playerEntry = entry;
      $("play-world-id").textContent = entry.world_id || "world";
      $("player-entry-copy").textContent = `从 ${entry.entry_location?.name || "入口地点"} 开始；你的选择会产生真实世界事件。`;
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
    } catch (error) { setPlayerResult(error.message || "玩家状态暂不可用", false); }
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
    renderPlayerActions(data.actions || [], data.active_task || {});
    renderPlayerFeed(data.recent_outcomes || []);
    const targets = $("player-dialogue-target"); targets.replaceChildren();
    for (const entity of data.nearby_entities || []) {
      if (!entity.id) continue;
      targets.append(new Option(entity.name || entity.id, entity.id));
    }
    if (!targets.options.length) targets.append(new Option("附近没有可交谈对象", ""));
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

  function renderPlayerActions(actions, activeTask = {}) {
    const root = $("player-actions"); root.replaceChildren();
    if (activeTask.action || activeTask.bt) {
      const running = make("div", "active-task-card"); running.append(make("strong", "", `正在执行：${activeTask.action || activeTask.bt}`)); running.append(make("p", "", `还需要 ${activeTask.remaining_minutes ?? 0} 分钟。行为完成后，Platform 会记录结果并刷新下一步选择。`)); root.append(running);
      const wait = make("button", "button ghost player-wait", "等待一分钟"); wait.type = "button"; wait.addEventListener("click", playerWait); root.append(wait); return;
    }
    if (!actions.length) { root.append(make("div", "empty-state", "暂时没有可执行行动；看看地图或稍后再来。")); return; }
    for (const action of actions) {
      const card = make("article", "action-card"); const head = make("div", "action-card-head"); head.append(make("strong", "action-card-title", action.choice || action.name || "行动")); head.append(make("span", "action-card-time", action.time || "Platform 计算时间")); card.append(head);
      card.append(make("p", "", action.summary || action.purpose || "按照当前世界规则执行一个行动。"));
      const meta = make("div", "action-card-meta"); for (const value of [...(action.cost || []), ...(action.impact || []).slice(0, 2)]) meta.append(make("span", "action-chip", value)); card.append(meta);
      const button = make("button", "button ghost", action.kind === "travel" ? "出发" : "执行行动"); button.type = "button"; button.addEventListener("click", () => submitPlayerAction(action)); card.append(button); root.append(card);
    }
    const wait = make("button", "button ghost player-wait", "等待一分钟"); wait.type = "button"; wait.addEventListener("click", playerWait); root.append(wait);
  }

  async function submitPlayerAction(action) {
    if (!state.playerId) return;
    setPlayerResult("行动已提交，Platform 正在校验…", true);
    try {
      const result = await request("/player/act", { method: "POST", body: JSON.stringify({ player_id: state.playerId, behavior_ref: action.ref, parameters: action.parameters || {}, target: action.target || {}, reason: "Player selected this action from the web client.", confidence: 1 }) });
      if (result.status !== "accepted") throw new Error(result.message || result.errors?.join("; ") || "行动未被接受");
      setPlayerResult(`${action.choice || action.name || "行动"} 已开始。`, true); await loadPlayerView();
    } catch (error) { setPlayerResult(error.message || "行动未提交", false); }
  }

  async function playerWait() {
    if (!state.playerId) return;
    try { await request("/player/wait", { method: "POST", body: JSON.stringify({ player_id: state.playerId }) }); await loadPlayerView(); setPlayerResult("世界向前推进了一分钟。", true); }
    catch (error) { setPlayerResult(error.message || "无法推进世界", false); }
  }

  async function sendPlayerDialogue() {
    const target = $("player-dialogue-target")?.value; const message = $("player-dialogue-message")?.value.trim();
    if (!state.playerId || !target || !message) { setPlayerResult("请选择附近对象并输入消息", false); return; }
    try { const result = await request("/dialogue", { method: "POST", body: JSON.stringify({ speaker_id: state.playerId, listener_id: target, topic: "general", message }) }); if (result.status !== "accepted") throw new Error(result.message || "对话未提交"); $("player-dialogue-message").value = ""; setPlayerResult("对话已经进入世界事件流。", true); await loadPlayerView(); }
    catch (error) { setPlayerResult(error.message || "对话未提交", false); }
  }

  function renderPlayerFeed(outcomes) {
    const root = $("player-feed"); root.replaceChildren();
    if (!outcomes.length) { root.append(make("div", "empty-state", "还没有新的结果。")); return; }
    for (const outcome of outcomes) { const item = make("div", "player-feed-item"); item.append(make("strong", "", outcome.result || outcome.summary || outcome.behavior_choice || "行动结果")); item.append(make("small", "", `${outcome.event_type || "world event"} · minute ${outcome.world_minute ?? "—"}`)); root.append(item); }
  }

  function setPlayerResult(message, ok) { const notice = $("play-notice"); notice.textContent = message || ""; notice.className = `notice ${ok ? "notice-ok" : ""}`; notice.classList.toggle("hidden", !message); }

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

  function renderPackageCatalog(response) {
    const select = $("package-select");
    if (!select) return;
    select.replaceChildren();
    const packages = response?.packages || [];
    state.packages = packages;
    const valid = packages.filter((item) => item.status === "passed");
    if (!valid.length) {
      select.append(new Option("没有可用模板", ""));
      return;
    }
    for (const item of valid) select.append(new Option(`${item.name || item.id} · ${item.id}`, item.id));
  }

  async function loadPackageCatalog() {
    try {
      renderPackageCatalog(await request("/authoring/catalog"));
    } catch (error) {
      const select = $("package-select");
      if (select) { select.replaceChildren(new Option("模板目录不可用", "")); }
    }
  }

  async function createNewInstance() {
    const packageId = $("package-select")?.value || "";
    const instanceId = $("instance-id")?.value.trim() || "";
    const resultBox = $("instance-result");
    if (!packageId || !instanceId) { resultBox.textContent = "请选择模板并填写实例 ID"; resultBox.className = "interaction-result error"; return; }
    resultBox.textContent = "创建中…"; resultBox.className = "interaction-result";
    try {
      const result = await request("/authoring/create", { method: "POST", body: JSON.stringify({ package_id: packageId, instance_id: instanceId }) });
      if (result.status !== "passed") throw new Error((result.errors || ["创建失败"]).join("; "));
      resultBox.textContent = `已创建 ${result.instance_id}：${result.db_path}。请停止当前服务后按 ${result.start_command} 启动。`;
      resultBox.className = "interaction-result ok";
    } catch (error) { resultBox.textContent = error.message || "创建失败"; resultBox.className = "interaction-result error"; }
  }

  async function init() {
    const initial = defaultTemplate();
    setDraft(initial);
    setEditorMode("guided");
    document.querySelectorAll(".mode-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
    $("refresh-button").addEventListener("click", () => refreshRuntime(true)); $("pause-button").addEventListener("click", () => control("/pause")); $("step-button").addEventListener("click", () => control("/step")); $("toggle-ticks").addEventListener("click", () => { state.includeTicks = !state.includeTicks; $("toggle-ticks").textContent = state.includeTicks ? "隐藏 tick" : "显示 tick"; refreshRuntime(true); });
    $("open-player").addEventListener("click", () => switchView("play-view")); $("play-enter").addEventListener("click", enterPlayer); $("play-player-origin").addEventListener("change", updateOriginPreview); $("player-refresh").addEventListener("click", loadPlayerView); $("player-send-dialogue").addEventListener("click", sendPlayerDialogue); $("player-leave").addEventListener("click", () => { state.playerId = null; state.playerData = null; $("player-world").classList.add("hidden"); $("player-setup").classList.remove("hidden"); setPlayerResult("已退出当前角色。", true); }); $("send-dialogue").addEventListener("click", sendDialogue); $("create-instance").addEventListener("click", createNewInstance);
    $("new-draft").addEventListener("click", () => setDraft(defaultTemplate(), { increment: true, clearSource: true })); $("open-current").addEventListener("click", () => openPackage()); $("open-path").addEventListener("click", () => openPackage($("authoring-path").value.trim())); $("format-draft").addEventListener("click", () => { const parsed = parseDraft(); if (!parsed.error) setDraft(parsed.value); else renderDiagnostics({ status: "rejected", errors: [parsed.error] }); }); $("apply-quick-fields").addEventListener("click", () => { const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; } parsed.value.world = { ...(parsed.value.world || {}), id: $("quick-world-id").value.trim(), title: $("quick-world-title").value.trim(), locale: $("quick-world-locale").value.trim() || "en" }; setDraft(parsed.value, { increment: true }); renderDiagnostics(null); }); $("guided-mode").addEventListener("click", () => setEditorMode("guided")); $("raw-mode").addEventListener("click", () => setEditorMode("raw")); $("build-world").addEventListener("click", buildWorldFromBrief); $("compile-draft").addEventListener("click", compileDraft); $("smoke-draft").addEventListener("click", smokeDraft); $("export-draft").addEventListener("click", exportDraft);
    try { const response = await request("/authoring/schema"); state.schema = response.schema; renderSchema(); } catch (error) { $("schema-list").replaceChildren(make("div", "empty-state", "Schema 暂不可用")); }
    await loadPackageCatalog(); await loadPlayerEntry();
    await refreshRuntime(true); state.poll = window.setInterval(() => { if ($("runtime-view").classList.contains("active-view")) refreshRuntime(false); else if ($("play-view").classList.contains("active-view") && state.playerId) loadPlayerView(); }, 4000);
  }

  window.addEventListener("DOMContentLoaded", init);
})();

