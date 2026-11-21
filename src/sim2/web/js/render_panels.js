import { loadDialogue, loadTrades, travelToNode } from "./actions.js";
import { $, el, formatMinute, itemLabel, state, translateNarrative } from "./core.js";


  /* ---------- Talk ---------- */

export function nearbyParticipants() {
    return (state.view?.nearby_participants || []).filter((item) => item.id !== state.playerId);
  }


export function renderTalkTargets() {
    const participants = nearbyParticipants();
    for (const selectId of ["talk-target", "trade-target"]) {
      const select = $(selectId);
      if (!select) continue;
      const previous = select.value;
      select.replaceChildren();
      if (!participants.length) {
        select.append(new Option("附近没有人", ""));
        select.disabled = true;
        continue;
      }
      select.disabled = false;
      for (const person of participants) {
        select.append(new Option(`${person.name || person.id}${person.status === "executing" ? "（忙碌）" : ""}`, person.id));
      }
      if (previous && [...select.options].some((option) => option.value === previous)) select.value = previous;
    }
    const send = $("talk-send");
    if (send) send.disabled = !participants.length;
  }


export function renderPeople() {
    const root = $("people-list");
    if (!root) return;
    root.replaceChildren();
    const participants = nearbyParticipants();
    if (state.selectedCharacter && !participants.some((item) => item.id === state.selectedCharacter.id)) {
      closeCharacter();
    }
    if (!participants.length) {
      root.append(el("p", "people-empty", "附近暂时没有可以交谈的人。"));
      return;
    }
    for (const person of participants) {
      const card = el("button", "people-card");
      card.type = "button";
      card.append(el("span", "person-avatar", String(person.name || person.id).slice(0, 1)));
      const copy = el("div");
      copy.append(el("strong", "", person.name || person.id));
      copy.append(el("small", "", person.status === "executing" ? "正在忙碌" : "可以交谈"));
      card.append(copy);
      card.append(el("span", "badge", "交谈"));
      card.addEventListener("click", () => openCharacter(person));
      root.append(card);
    }
  }


export function updateCharacterSheet(person) {
    const name = person?.name || person?.id || "人物";
    $("character-name").textContent = name;
    $("character-avatar").textContent = String(name).slice(0, 1);
    $("character-location").textContent = person?.location || person?.location_id || state.view?.location?.name || "附近";
    $("character-status").textContent = person?.status === "executing" ? "正在忙碌" : "可以交谈";
  }


export function openCharacter(person) {
    if (!person) return;
    state.selectedCharacter = person;
    renderTalkTargets();
    const select = $("talk-target");
    if (select && [...select.options].some((option) => option.value === person.id)) {
      select.value = person.id;
    }
    updateCharacterSheet(person);
    $("character-drawer").classList.remove("hidden");
    loadDialogue(person.id);
    window.setTimeout(() => $("talk-message")?.focus(), 80);
  }


export function closeCharacter() {
    state.selectedCharacter = null;
    $("character-drawer").classList.add("hidden");
  }


export function openPlayerSheet() {
    $("player-drawer").classList.remove("hidden");
  }


export function closePlayerSheet() {
    $("player-drawer").classList.add("hidden");
  }


export function personName(personId) {
    const person = nearbyParticipants().find((item) => item.id === personId);
    return person?.name || personId;
  }


  /* ---------- Trade ---------- */

export function bundleText(bundle) {
    const parts = Object.entries(bundle || {}).map(([key, value]) => `${itemLabel(key)}×${value}`);
    return parts.length ? parts.join("、") : "—";
  }


export function parseBundle(text) {
    const bundle = {};
    for (const token of String(text || "").split(/[\s,，;；]+/)) {
      if (!token) continue;
      const [key, raw] = token.split("=");
      const amount = Number(raw);
      if (!key || !Number.isFinite(amount) || amount <= 0) return null;
      bundle[key] = amount;
    }
    return Object.keys(bundle).length ? bundle : null;
  }


export function eventKind(type) {
    const value = String(type || "");
    if (value.startsWith("public_decision") || value.startsWith("decision.")) return "decision";
    if (value.startsWith("storm.") || value.startsWith("weather.")) return "storm";
    if (value.startsWith("dialogue.") || value.startsWith("conversation.")) return "dialogue";
    if (value.startsWith("resource.") || value.startsWith("ration") || value.startsWith("rope.")) return "resource";
    if (value.startsWith("tide_gate.") || value.startsWith("repair") || value.startsWith("signal.") || value.startsWith("gate.") || value.startsWith("gate_brace.") || value.startsWith("rain_catcher.") || value.startsWith("rainwater.") || value.startsWith("commons.volunteer")) return "repair";
    if (value.startsWith("market.") || value.startsWith("trade.")) return "trade";
    if (value.startsWith("confidence.")) return "decision";
    if (value.startsWith("season.")) return "storm";
    if (value.startsWith("story.") || value.startsWith("beat.")) return "story";
    if (value.startsWith("origin.")) return "origin";
    return "";
  }


export function eventTitle(type) {
    const kind = eventKind(type);
    if (kind === "decision") return "公共决策";
    if (kind === "storm") return "风暴";
    if (kind === "dialogue") return "对话";
    if (kind === "resource") return "采集与制作";
    if (kind === "repair") return "公共设施";
    if (kind === "trade") return "交易";
    if (kind === "story") return "世界剧情";
    if (kind === "origin") return "出身";
    if (type.startsWith("behavior.")) return "角色行动";
    if (type.startsWith("trade.")) return "交易";
    if (type.startsWith("need.")) return "需求";
    if (type.startsWith("player.")) return "角色";
    return "世界";
  }


  /* ---------- Feed ---------- */

export const SKIP_EVENTS = new Set([
    "world_minute.advanced",
    "action.started",
    "action.completed",
    "outcome.evaluated",
    "runtime.started",
    "runtime.stopped",
    "runtime.paused",
    "runtime.resumed",
    "snapshot.created",
    "snapshot.restored",
    "resource.changed",
    "resource.regenerated",
    "world.property_changed",
    "entity.state_changed",
    "player.entered",
    "player.resumed",
    "behavior.started",
    "capability.updated",
    "memory.experience_recorded",
    "definition.registered",
    "behavior.registered",
    "evolution.task_due",
  ]);


export function renderFeed() {
    const root = $("world-feed");
    if (!root) return;
    root.replaceChildren();
    const events = state.feedEvents.slice(0, state.feedLimit);
    renderWorldPulse();
    if (!events.length) {
      root.append(el("p", "feed-empty", "世界还没有留下可读的记录。"));
      return;
    }
    for (const event of events) {
      const type = String(event.event_type || "");
      const narrative = translateNarrative(event.public?.summary || event.narrative || "");
      if (!narrative) continue;
      const item = el("article", "feed-item");
      item.dataset.kind = eventKind(type);
      item.append(el("span", "feed-time", formatMinute(event.world_minute)));
      const copy = el("div", "feed-copy");
      copy.append(el("strong", "", eventTitle(type)));
      copy.append(el("p", "", narrative));
      item.append(copy);
      root.append(item);
    }
    if (!root.children.length) root.append(el("p", "feed-empty", "世界还没有留下可读的记录。"));
    const more = $("feed-more");
    if (more) more.classList.toggle("hidden", state.feedEvents.length <= state.feedLimit);
  }


export function renderWorldPulse() {
    const root = $("world-pulse");
    if (!root) return;
    root.replaceChildren();
    const events = state.feedEvents.slice(0, 30);
    const currentMinute = Number(state.view?.world_time?.minute || 0);
    const important = events.find((event) => {
      if (currentMinute - Number(event.world_minute || 0) > 720) return false;
      const type = String(event.event_type || "");
      return type.includes("storm")
        || type.includes("story")
        || type.includes("world.change")
        || type.includes("civilization")
        || type.includes("evolution")
        || type.startsWith("gate.")
        || type.startsWith("confidence.")
        || type.startsWith("season.")
        || type.startsWith("signal.")
        || type.startsWith("rain")
        || type.startsWith("market.")
        || type.startsWith("commons.");
    }) || events[0];
    if (!important) {
      root.className = "world-pulse empty";
      root.append(el("span", "eyebrow", "世界脉搏"));
      root.append(el("strong", "", "还没有新的变化"));
      root.append(el("p", "", "行动和事件会在这里汇总成世界级进展。"));
      return;
    }
    root.className = "world-pulse";
    root.append(el("span", "eyebrow", `世界脉搏 · ${formatMinute(important.world_minute)}`));
    root.append(el("strong", "", eventTitle(String(important.event_type || ""))));
    root.append(el("p", "", translateNarrative(important.public?.summary || important.narrative || "")));
  }


  /* ---------- Map ---------- */

export function showMapDialog() {
    const dialog = $("map-dialog");
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }
  }


export function renderMiniMap() {
    const root = $("mini-map");
    if (!root) return;
    const map = state.view?.map;
    if (!map || !Array.isArray(map.nodes) || !map.nodes.length) {
      root.replaceChildren(el("p", "mini-map-empty", "这个世界还没有可见的地点。"));
      return;
    }
    root.replaceChildren();
    renderTopology(root, map, { width: 940, height: 210, padX: 132, padY: 48, compact: true });
  }


export function renderMap() {
    $("map-world-title").textContent = state.world?.title || "世界";
    const canvas = $("map-canvas");
    canvas.replaceChildren();
    const map = state.map || state.view?.map;
    if (!map || !Array.isArray(map.nodes) || !map.nodes.length) {
      canvas.append(el("p", "hint", "没有可见地点。"));
      return;
    }
    renderTopology(canvas, map, { width: 640, height: 380, pad: 84, compact: false });
    const legend = $("map-legend");
    legend.textContent = "金色边框是当前位置，亮色节点可以直接前往；虚线表示已知路线。";
  }


export function renderTopology(canvas, map, options = {}) {
    const nodes = Array.isArray(map?.nodes) ? map.nodes : [];
    if (!nodes.length) {
      canvas.append(el("p", "hint", "没有可见地点。"));
      return;
    }
    const compact = Boolean(options.compact);
    const width = Number(options.width || 640);
    const height = Number(options.height || 380);
    const padX = Number(options.padX || options.pad || 80);
    const padY = Number(options.padY || options.pad || 80);
    const positions = nodes.map((node, index) => {
      const position = node.position || {};
      const x = Number(position.x);
      const y = Number(position.y);
      return {
        node,
        x: Number.isFinite(x) ? x : index % 3,
        y: Number.isFinite(y) ? y : Math.floor(index / 3),
      };
    });
    const xs = positions.map((item) => item.x);
    const ys = positions.map((item) => item.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const px = (x) => padX + ((x - minX) / Math.max(1, maxX - minX)) * (width - padX * 2);
    const py = (y) => padY + ((y - minY) / Math.max(1, maxY - minY)) * (height - padY * 2);
    const routes = new Set((state.view?.routes || []).map((route) => route.id));
    const current = state.view?.location?.id;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("class", "map-lines");
    svg.setAttribute("aria-hidden", "true");
    for (const edge of map?.edges || []) {
      const from = positions.find((item) => item.node.id === edge.from);
      const to = positions.find((item) => item.node.id === edge.to);
      if (!from || !to) continue;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", px(from.x));
      line.setAttribute("y1", py(from.y));
      line.setAttribute("x2", px(to.x));
      line.setAttribute("y2", py(to.y));
      line.setAttribute("stroke", "rgba(139,204,192,.22)");
      line.setAttribute("stroke-width", compact ? "1.4" : "1");
      line.setAttribute("stroke-dasharray", compact ? "5 6" : "4 5");
      svg.append(line);
    }
    const wrap = el("div", compact ? "map-stage mini-map-stage" : "map-stage");
    wrap.append(svg);
    for (const item of positions) {
      const node = item.node;
      const reachable = routes.has(node.id);
      const closed = node.open === false;
      const button = el("button", `map-node${node.id === current ? " current" : ""}${reachable && !closed ? " reachable" : ""}${closed ? " closed" : ""}`);
      button.type = "button";
      button.style.left = `${(px(item.x) / width) * 100}%`;
      button.style.top = `${(py(item.y) / height) * 100}%`;
      button.append(el("strong", "", node.name || node.id));
      const occupants = Array.isArray(node.occupants) ? node.occupants.length : 0;
      const tagNames = { resource: "资源", research: "研究", craft: "工坊", social: "社交", market: "集市", defense: "防线", home: "住屋", exploration: "探索", infrastructure: "设施", public: "公共", safe: "安全" };
      const nodeTags = (node.tags || []).map((tag) => tagNames[tag] || tag);
      const detail = closed
        ? (node.closed_reason || "暂时封闭")
        : node.id === current
          ? "你在这里"
          : reachable
            ? "可前往"
            : compact
              ? (occupants ? `${occupants} 人` : nodeTags.slice(0, 1).join(" · "))
              : nodeTags.join(" · ");
      if (detail) button.append(el("small", "", detail));
      if (reachable && !closed && node.id !== current) {
        // The server words every travel action from the world's ui_text, so the
        // map reuses it instead of composing its own phrases.
        const travelAction = (state.view?.actions || []).find(
          (entry) => entry.ref === "behavior:travel" && entry.parameters?.location_id === node.id,
        );
        button.title = travelAction?.choice || node.name || node.id;
        button.addEventListener("click", () => travelToNode(node));
      }
      wrap.append(button);
    }
    canvas.append(wrap);
  }


  /* ---------- Tabs ---------- */

export function switchTab(tab) {
    for (const button of $("right-tabs").querySelectorAll("[data-tab]")) {
      button.classList.toggle("active", button.dataset.tab === tab);
    }
    for (const body of document.querySelectorAll(".tab-body")) {
      body.classList.toggle("active", body.id === `tab-${tab}`);
    }
    if (tab === "talk") renderPeople();
    if (tab === "trade") loadTrades();
  }
