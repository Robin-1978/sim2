import { actOnPlayerOffer, advancePlayerAction, playerProvider, playerWait, submitPlayerAction, toggleCausal } from "./admin_actions.js";
import { request } from "./admin_api.js";
import { $, make, state } from "./admin_core.js";


export function renderMetrics(status, observer, runtime = {}) {
    $("world-clock").textContent = status.world_minute === undefined ? "—" : `minute ${status.world_minute}`;
    $("metric-status").textContent = runtime.state || status.status || "—";
    $("metric-agents").textContent = String((observer.agents || []).length);
    $("metric-locations").textContent = String((observer.entity_counts || {}).location || 0);
    $("metric-cursor").textContent = String(state.after);
  }


export function renderRuntime(runtime = {}) {
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


export function renderInstances(payload) {
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


export function renderCharacters(observer) {
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


export function renderDialogueTargets(observer) {
    const agents = observer.agents || [];
    for (const id of ["dialogue-speaker", "dialogue-listener"]) {
      const select = $(id); const previous = select.value; select.replaceChildren();
      for (const agent of agents) {
        const option = document.createElement("option"); option.value = agent.id; option.textContent = `${agent.name || agent.id} · ${agent.location || "unknown"}`; select.append(option);
      }
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    }
  }


export function positionNodes(nodes) {
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


export function renderMap(view) {
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


export function renderInspector() {
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


export function stat(label, value) { const node = make("div", "inspector-stat"); node.append(make("span", "inspector-muted", label)); node.append(make("strong", "", value)); return node; }


export function displayValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    if (typeof value === "boolean") return value ? "是" : "否";
    if (Array.isArray(value)) return value.map(displayValue).join(", ");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key}: ${displayValue(item)}`).join(" · ");
    return String(value);
  }


export function renderTimeline() {
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


export async function renderEvolution() {
    const list = $("evolution-list"); list.replaceChildren();
    if (!state.operatorToken) {
      list.append(make("div", "empty-state", "解锁管理后查看演化队列"));
      return;
    }
    try {
      const response = await request("/api/v1/operator/evolution/tasks");
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


export async function renderNpcDialogue() {
    const list = $("npc-dialogue-list"); if (!list) return;
    list.replaceChildren();
    try {
      const response = await request("/api/v1/observer/dialogue?limit=20");
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


export async function renderNpcDecisions() {
    const list = $("npc-decisions-list"); if (!list) return;
    list.replaceChildren();
    try {
      const response = await request("/api/v1/observer/decisions?limit=20");
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


export function renderPlayerWorld(data) {
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


export function bundleText(bundle) {
    if (!bundle || typeof bundle !== "object") return "—";
    const parts = Object.entries(bundle).map(([key, value]) => `${key}×${value}`);
    return parts.length ? parts.join("，") : "—";
  }


export async function renderPlayerTrades() {
    const incoming = $("player-trade-incoming");
    const outgoing = $("player-trade-outgoing");
    if (!incoming || !outgoing || !state.playerId) return;
    incoming.replaceChildren(); outgoing.replaceChildren();
    try {
      const response = await request(`/api/v1/player/${encodeURIComponent(state.playerId)}/trades?include_closed=0`);
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


export function renderNeedBars(attributes) {
    const root = $("player-need-bars"); root.replaceChildren();
    const needs = (attributes || []).filter((item) => item.kind === "need").slice(0, 4);
    for (const item of needs) {
      const value = Number(item.value || 0); const row = make("div", "need-bar");
      const label = make("div", "need-bar-label"); label.append(make("span", "", item.label || item.id), make("span", "", `${Math.round(value)}`));
      const track = make("div", "need-bar-track"); const fill = make("div", "need-bar-fill"); fill.style.width = `${Math.max(0, Math.min(100, value))}%`; track.append(fill); row.append(label, track); root.append(row);
    }
  }


export function renderPlayerMap(view) {
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


export function renderPlayerActions(actions, activeTask = {}, unavailable = []) {
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


export function renderPlayerFeed(outcomes) {
    const root = $("player-feed"); root.replaceChildren();
    if (!outcomes.length) { root.append(make("div", "empty-state", "还没有新的结果。")); return; }
    for (const outcome of outcomes) { const item = make("div", "player-feed-item"); item.append(make("strong", "", outcome.result || outcome.summary || outcome.behavior_choice || "行动结果")); item.append(make("small", "", `${outcome.event_type || "world event"} · minute ${outcome.world_minute ?? "—"}`)); root.append(item); }
  }
