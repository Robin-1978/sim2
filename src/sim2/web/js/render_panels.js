import { loadDialogue, loadTrades, travelToNode } from "./actions.js";
import { $, el, formatMinute, itemLabel, state, toast, translateNarrative, uiConfig } from "./core.js";


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


function latestActivityFor(personId) {
    return (state.agentEvents || []).find((event) => event.activity?.actor_id === personId) || null;
  }


function personActivityLine(person) {
    if (person?.status !== "executing" && person?.status !== "paused") return "可以交谈";
    return person?.active_behavior_label || "正在忙碌";
  }


export function renderPeopleActivity() {
    const root = $("people-activity");
    if (!root) return;
    root.replaceChildren();
    const events = (state.agentEvents || [])
      .filter((event) => {
        if (event.activity?.kind !== "behavior" || event.activity?.actor_id === state.playerId) return false;
        return !String(event.public?.correlation_id || "").startsWith("bt:");
      })
      .slice(0, 6);
    if (!events.length) {
      root.append(el("p", "people-activity-empty", "还没有居民行动的记录。推进一点时间，看看他们在做什么。"));
      return;
    }
    for (const event of events) {
      const activity = event.activity || {};
      const row = el("article", `people-activity-row ${activity.kind || ""}`);
      row.append(el("span", "people-activity-time", formatMinute(event.world_minute)));
      const copy = el("div", "people-activity-copy");
      const actor = activity.actor || "世界";
      const label = activity.kind === "behavior"
        ? `${activity.status === "completed" ? "完成" : activity.status === "started" ? "开始" : "中断"} · ${activity.behavior || "行动"}`
        : (activity.label || "世界变化");
      copy.append(el("strong", "", `${actor} ${label}`));
      const detail = translateNarrative(String(activity.reason || activity.summary || "").trim());
      if (detail) copy.append(el("p", "", detail));
      row.append(copy);
      root.append(row);
    }
  }


export function renderPeople() {
    renderPeopleActivity();
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
      copy.append(el("small", "", personActivityLine(person)));
      if (person.status === "executing" || person.status === "paused") {
        const progress = Number(person.initial_state?.action_progress);
        if (Number.isFinite(progress)) {
          const track = el("span", "person-progress");
          const fill = el("span");
          fill.style.width = `${Math.max(0, Math.min(100, progress * 100))}%`;
          track.append(fill);
          copy.append(track);
        }
      }
      card.append(copy);
      card.append(el("span", "badge", personActivityLine(person) === "可以交谈" ? "交谈" : "查看"));
      card.addEventListener("click", () => openCharacter(person));
      root.append(card);
    }
  }


export function updateCharacterSheet(person) {
    const name = person?.name || person?.id || "人物";
    const active = person?.status === "executing" || person?.status === "paused";
    $("character-name").textContent = name;
    $("character-avatar").textContent = String(name).slice(0, 1);
    const place = person?.location || person?.location_id || state.view?.location?.name || "附近";
    const remaining = Number(person?.initial_state?.remaining_minutes);
    $("character-location").textContent = Number.isFinite(remaining) && remaining > 0
      ? `${place} · 还剩 ${Math.ceil(remaining)} 分钟`
      : place;
    $("character-status").textContent = personActivityLine(person);
    const relationshipCard = $("character-relationship");
    if (relationshipCard) {
      const relationship = (state.view?.relationships || []).find((item) => item.person === person?.id);
      const strength = Number(relationship?.strength || 0);
      const labels = uiConfig().relationship_labels || {};
      const defaults = { trust: "信任", affinity: "好感", respect: "尊重", friendship: "友谊" };
      const relation = String(relationship?.relation || "初次见面");
      $("relationship-label").textContent = relationship
        ? (labels[relation] || defaults[relation] || relation.replace(/_/g, " "))
        : "初次见面";
      $("relationship-fill").style.width = `${Math.max(0, Math.min(100, (strength + 1) * 50))}%`;
      $("relationship-note").textContent = relationship?.description
        || (relationship ? "这次互动会被记住。" : "还没有互动记录。");
      relationshipCard.classList.remove("hidden");
    }
    const intent = $("character-intent");
    if (intent) {
      const detail = translateNarrative(String(latestActivityFor(person?.id)?.activity?.reason || "").trim());
      intent.textContent = detail;
      intent.classList.toggle("hidden", !detail);
    }
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


const DEFAULT_EVENT_KINDS = {
    weather: ["weather.", "storm."],
    decision: ["decision.", "public_decision"],
    dialogue: ["dialogue.", "conversation."],
    resource: ["resource."],
    trade: ["market.", "trade."],
    story: ["story.", "beat."],
    origin: ["origin."],
    rule: ["world.change.", "world.rule.", "rule."],
  };

  const DEFAULT_EVENT_TITLES = {
    weather: "天气",
    decision: "公共决策",
    dialogue: "对话",
    resource: "资源",
    trade: "交易",
    story: "世界剧情",
    origin: "出身",
    rule: "世界规则",
    behavior: "角色行动",
    need: "需求",
    player: "角色",
    world: "世界",
  };

  const DEFAULT_PULSE_PREFIXES = ["weather", "story", "world.change", "civilization", "evolution", "decision", "trade"];


export function eventKind(type) {
    const value = String(type || "");
    const configured = uiConfig().event_kinds || {};
    for (const [kind, prefixes] of Object.entries(configured)) {
      for (const prefix of Array.isArray(prefixes) ? prefixes : [prefixes]) {
        if (prefix && value.startsWith(String(prefix))) return kind;
      }
    }
    for (const [kind, prefixes] of Object.entries(DEFAULT_EVENT_KINDS)) {
      for (const prefix of prefixes) {
        if (value.startsWith(prefix)) return kind;
      }
    }
    return "";
  }


export function eventTitle(type) {
    const kind = eventKind(type);
    const titles = { ...DEFAULT_EVENT_TITLES, ...(uiConfig().event_titles || {}) };
    if (kind && titles[kind]) return titles[kind];
    if (type.startsWith("behavior.")) return titles.behavior;
    if (type.startsWith("trade.")) return titles.trade;
    if (type.startsWith("need.")) return titles.need;
    if (type.startsWith("player.")) return titles.player;
    return titles.world;
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
    "resource.withdrawal.completed",
    "world.property_changed",
    "entity.state_changed",
    "player.entered",
    "player.resumed",
    "behavior.started",
    "capability.updated",
    "capability.evidence",
    "memory.experience_recorded",
    "definition.registered",
    "behavior.registered",
    "evolution.task_due",
  ]);


export function eventNarrative(event) {
    const type = String(event?.event_type || "");
    const data = event?.public || {};
    const actor = String(event?.actor || event?.activity?.actor || "有人");
    if (type === "trade.bought" || type === "trade.sold") {
      const item = itemLabel(data.item || "");
      if (item) {
        const price = Number(data.price || 0);
        const verb = type === "trade.bought" ? "买下" : "卖出";
        const money = price ? (type === "trade.bought" ? `，花了 ${price} 枚金币` : `，换到 ${price} 枚金币`) : "";
        return `${actor}${verb}${item}${money}。`;
      }
    }
    if (type === "resource.gathered") {
      const item = itemLabel(data.item || "");
      const quantity = Number(data.quantity || 0);
      if (item) return `${actor}采集到${quantity ? `${quantity} 份` : ""}${item}。`;
    }
    if (type === "meal.eaten") return `${actor}吃了一顿热饭。`;
    if (type === "stew.eaten") return `${actor}吃了一锅炖菜。`;
    if (type === "spiced_stew.eaten") return `${actor}吃了一锅香炖菜。`;
    if (type === "ale.drunk") return `${actor}喝了一壶麦酒。`;
    if (type === "tonic.used") return `${actor}用了药剂。`;
    return translateNarrative(data.summary || event?.narrative || "");
  }


export function renderNpcIntentions() {
    const panel = $("intention-panel");
    const root = $("intention-list");
    if (!panel || !root) return;
    const seen = new Set();
    const items = [];
    for (const event of state.agentEvents || []) {
      const activity = event.activity || {};
      if (activity.kind !== "behavior" || activity.status !== "started") continue;
      const actorId = String(activity.actor_id || "");
      if (!actorId || actorId === state.playerId || seen.has(actorId)) continue;
      if (String(event.public?.correlation_id || "").startsWith("bt:")) continue;
      const reason = String(activity.reason || "").trim();
      if (!reason) continue;
      seen.add(actorId);
      items.push({
        actorId,
        actor: String(activity.actor || "居民"),
        behavior: String(activity.behavior || "行动"),
        reason,
        minute: Number(event.world_minute || 0),
      });
      if (items.length >= 3) break;
    }
    panel.classList.toggle("hidden", !items.length);
    if (!items.length) return;
    root.replaceChildren();
    for (const item of items) {
      const card = el("article", "intention-card");
      card.tabIndex = 0;
      const head = el("div", "intention-head");
      head.append(el("span", "intention-avatar", item.actor.slice(0, 1)));
      const who = el("div", "intention-who");
      who.append(el("strong", "", item.actor));
      who.append(el("small", "", item.behavior));
      head.append(who);
      card.append(head);
      card.append(el("blockquote", "", translateNarrative(item.reason)));
      card.append(el("time", "", formatMinute(item.minute)));
      const open = () => {
        const person = nearbyParticipants().find((candidate) => candidate.id === item.actorId);
        if (person) openCharacter(person);
        else toast(`${item.actor}现在不在附近。`, "ok");
      };
      card.addEventListener("click", open);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      });
      root.append(card);
    }
  }


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
      const narrative = eventNarrative(event);
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
    renderNpcIntentions();
    const more = $("feed-more");
    if (more) more.classList.toggle("hidden", state.feedEvents.length <= state.feedLimit);
  }


export function renderWorldPulse() {
    const root = $("world-pulse");
    if (!root) return;
    root.replaceChildren();
    const events = state.feedEvents.slice(0, 30);
    const currentMinute = Number(state.view?.world_time?.minute || 0);
    const pulsePrefixes = uiConfig().pulse_prefixes || DEFAULT_PULSE_PREFIXES;
    const important = events.find((event) => {
      if (currentMinute - Number(event.world_minute || 0) > 720) return false;
      const type = String(event.event_type || "");
      return pulsePrefixes.some((prefix) => type.includes(String(prefix)));
    }) || events[0];
    if (!important) {
      const activityEvent = (state.agentEvents || []).find((event) => {
        const activity = event.activity || {};
        if (activity.kind !== "behavior" || activity.actor_id === state.playerId) return false;
        return !String(event.public?.correlation_id || "").startsWith("bt:");
      });
      if (activityEvent) {
        const activity = activityEvent.activity || {};
        root.className = "world-pulse";
        root.append(el("span", "eyebrow", `居民动向 · ${formatMinute(activityEvent.world_minute)}`));
        root.append(el("strong", "", `${activity.actor || "居民"} · ${activity.behavior || "行动"}`));
        root.append(el("p", "", translateNarrative(String(activity.reason || activity.summary || "居民正在自行安排下一步。"))));
        return;
      }
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
