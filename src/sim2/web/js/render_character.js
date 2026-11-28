import { $, clamp, el, formatClock, humanizeKey, itemLabel, state, uiConfig } from "./core.js";


export function renderClock() {
    const view = state.view;
    const worldTime = view?.world_time || null;
    const clock = formatClock(worldTime);
    $("hud-day").textContent = clock.day;
    $("hud-time").textContent = clock.time;
    $("hud-phase").textContent = clock.phase;
    $("hud-season").textContent = state.world?.season || worldTime?.season || "—";
    $("hud-world-title").textContent = state.world?.title || view?.world_id || "世界";
    const running = Boolean(state.runtime?.running);
    const pill = $("runtime-pill");
    pill.textContent = running ? "自动运行中" : "手动世界";
    pill.className = `pill${running ? " live" : ""}`;
  }


export function renderCharacter() {
    const view = state.view;
    const player = view.player || {};
    const name = player.name || state.playerName || state.playerId || "?";
    $("player-name").textContent = name;
    $("player-portrait").textContent = String(name).slice(0, 1);
    const role = player.origin_name || (player.role && player.role !== "player" ? player.role : "玩家");
    const location = player.location || view.location?.name || "未知地点";
    $("player-role").textContent = role;
    $("player-location").textContent = location;
    $("player-goal").textContent = player.goal || "在这个世界里找到自己的位置。";
    $("player-sheet-name").textContent = name;
    $("player-sheet-avatar").textContent = String(name).slice(0, 1);
    $("player-sheet-role").textContent = role;
    $("player-sheet-location").textContent = location;

    const badges = $("player-badges");
    badges.replaceChildren();
    badges.append(el("span", player.location_safe ? "badge safe" : "badge danger", player.location_safe ? "安全地点" : "危险地点"));
    const locationTags = Array.isArray(player.location_tags) ? player.location_tags : [];
    const tagNames = { craft: "工坊", social: "社交", market: "集市", resource: "资源", research: "研究", defense: "防线", home: "住屋", infrastructure: "基础设施", exploration: "探索", public: "公共" };
    for (const tag of locationTags) {
      if (tagNames[tag]) badges.append(el("span", "badge", tagNames[tag]));
    }

    const attributes = Array.isArray(player.attributes) ? player.attributes : [];
    $("summary-name").textContent = name;
    $("summary-avatar").textContent = Array.from(name)[0];
    $("summary-origin").textContent = `${role} · ${location}`;
    $("summary-goal").textContent = uiConfig().venture ? "" : player.goal || "";
    const preferred = uiConfig().player_summary_attributes;
    const stats = Array.isArray(preferred)
      ? preferred.map(id => attributes.find(attr => attr.id === id)).filter(Boolean)
      : attributes.slice(0, 3);
    const summaryStats = $("summary-stats");
    summaryStats.replaceChildren();
    for (const attr of stats) {
      const value = Number(attr.value);
      const stat = el("div", attr.cost_mode === "increase" ? "" : barTone(attr.id, value));
      stat.append(el("dt", "", attr.label || humanizeKey(attr.id)),
        el("dd", "", `${Math.round(value * 10) / 10}${attr.unit || ""}`));
      summaryStats.append(stat);
    }
    const needRoot = $("need-bars");
    const vitalRoot = $("vital-bars");
    needRoot.replaceChildren();
    vitalRoot.replaceChildren();
    for (const attr of attributes) {
      const value = Number(attr.value || 0);
      const row = el("div", "bar-row");
      const label = el("div", "bar-label");
      label.append(el("span", "", attr.label || humanizeKey(attr.id)));
      const display = attr.kind === "need"
        ? `${(Math.round(value * 10) / 10).toFixed(1)}`
        : `${Math.round(value)}${attr.unit === "%" ? "%" : ""}`;
      label.append(el("strong", "", display));
      const track = el("div", "bar-track");
      const fill = el("span", `bar-fill ${barTone(attr.id || attr.label, value)}`);
      fill.style.width = `${clamp(value, 0, 100)}%`;
      track.append(fill);
      row.append(label, track);
      if (attr.kind === "need") needRoot.append(row);
      else vitalRoot.append(row);
    }
    if (!needRoot.children.length) needRoot.append(el("p", "hint", "没有可显示的需求。"));
    const inventoryCount = Object.keys(view.inventory || {}).filter((key) => Number(view.inventory[key]) > 0).length;
    $("scene-player-bag").textContent = `行囊 · ${inventoryCount} 种`;
    const capabilityCount = Array.isArray(view.capabilities) ? view.capabilities.length : 0;
    $("inventory-count").textContent = `${inventoryCount} 种`;
    $("capability-count").textContent = `${capabilityCount} 项`;
    $("player-loadout").textContent = `${inventoryCount} 种物品 · ${capabilityCount} 项能力`;
    renderInventory();
    renderCapabilities();
  }


export function barTone(id, value) {
    const key = String(id || "").toLowerCase();
    const threshold = Number(state.view?.alert_thresholds?.[`${key}_alert_threshold`]);
    if (Number.isFinite(threshold)) {
      if (value <= threshold * 0.6) return "low";
      return value <= threshold ? "warn" : "";
    }
    return value <= 30 ? "low" : value <= 45 ? "warn" : "";
  }


export function renderInventory() {
    const grid = $("inventory-grid");
    grid.replaceChildren();
    const entries = Object.entries(state.view?.inventory || {}).filter(([, value]) => Number(value) > 0);
    if (!entries.length) {
      grid.append(el("div", "inventory-empty", "身上没有可用的物品。"));
      return;
    }
    for (const [itemId, amount] of entries) {
      const label = itemLabel(itemId);
      const item = el("div", "inventory-item");
      item.append(el("span", "item-glyph", String(label).slice(0, 1)));
      const copy = el("div", "item-copy");
      copy.append(el("strong", "", label));
      copy.append(el("span", "", `× ${Number(amount)}`));
      const uses = Array.isArray(state.view?.item_uses?.[itemId]) ? state.view.item_uses[itemId] : [];
      if (uses.length) copy.append(el("small", "item-use", `用途：${uses.slice(0, 2).join(" · ")}`));
      item.append(copy);
      grid.append(item);
    }
  }


export function renderCapabilities() {
    const root = $("capability-list");
    root.replaceChildren();
    const capabilities = Array.isArray(state.view?.capabilities) ? state.view.capabilities : [];
    if (!capabilities.length) {
      root.append(el("p", "hint", "还没有已验证的能力经验。"));
      return;
    }
    for (const item of capabilities) {
      const value = Number(item.value || 0);
      const row = el("div", "capability-row");
      row.append(el("span", "", item.label || humanizeKey(item.id)));
      row.append(el("strong", "", `${Math.round(value * 100)}%`));
      const track = el("div", "capability-track");
      const fill = el("span");
      fill.style.width = `${clamp(value * 100, 0, 100)}%`;
      track.append(fill);
      row.append(track);
      root.append(row);
    }
  }
