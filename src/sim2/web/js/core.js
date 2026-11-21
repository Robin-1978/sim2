
export const $ = (id) => document.getElementById(id);

export const PLAYER_KEY = "sim2.game.player";

export const OPERATOR_KEY = "sim2.game.operator";

export const ENDING_KEY = "sim2.game.ending";

export const SOUND_KEY = "sim2.game.sound";


export const state = {
    world: null,
    entry: null,
    instances: [],
    worlds: [],
    activeDb: "",
    playerId: null,
    playerName: "",
    playerToken: null,
    operatorToken: null,
    view: null,
    runtime: null,
    map: null,
    selectedOrigin: "",
    selectedWorldPackage: "",
    selectedCharacter: null,
    actionFilter: "全部",
    actionExpanded: false,
    goalsExpanded: false,
    feedLimit: 28,
    feedEvents: [],
    taskKey: "",
    taskTotal: 0,
    clockOffset: 0,
    advancing: false,
    busy: false,
    online: false,
    lastDialogueKey: "",
    endingKey: "",
    soundOn: false,
    audio: null,
    pollTimer: null,
    stream: null,
    streamCursor: 0,
    streamConnected: false,
    streamFailures: 0,
    taskWaiters: new Map(),
    booted: false,
  };


export const ICONS = {
    refresh: '<path d="M20 11a8 8 0 1 0-2.4 5.7"/><path d="M20 4v7h-7"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9S14.5 18.3 12 21c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3Z"/>',
    pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    sound: '<path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    soundOff: '<path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M16 9l5 6M21 9l-5 6"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.96 4.6H9a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1Z"/>',
  };


export function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  }


export function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }


export function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }


export function storageGet(key, fallback = null) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (error) {
      return fallback;
    }
  }


export function storageSet(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      /* storage can be blocked; play still works for this tab */
    }
  }


export function setOnline(online) {
    state.online = Boolean(online);
    const dot = $("conn-dot");
    if (dot) dot.className = `conn-dot ${online ? "online" : "offline"}`;
  }


export async function withPending(button, fn) {
    if (!button) return fn();
    const wasDisabled = button.disabled;
    button.disabled = true;
    try {
      return await fn();
    } finally {
      button.disabled = wasDisabled;
    }
  }


  /* ---------- Toasts ---------- */

export function toast(message, kind = "", timeout = 4200) {
    const root = $("toasts");
    if (!root || !message) return;
    while (root.children.length >= 4) root.firstElementChild?.remove();
    const node = el("div", `toast ${kind}`.trim(), message);
    root.append(node);
    setTimeout(() => node.remove(), timeout);
  }


  /* ---------- Formatting ---------- */

export const PHASES = { dawn: "清晨", workday: "白天", dusk: "黄昏", night: "夜晚" };


export function formatClock(worldTime) {
    if (!worldTime) return { day: "第 1 天", time: "07:00", phase: "清晨" };
    const minute = Number(worldTime.minute_of_day ?? 0);
    const hours = String(Math.floor(minute / 60)).padStart(2, "0");
    const minutes = String(minute % 60).padStart(2, "0");
    return {
      day: `第 ${Number(worldTime.day || 1)} 天`,
      time: `${hours}:${minutes}`,
      phase: PHASES[String(worldTime.day_phase || "")] || String(worldTime.day_phase || ""),
    };
  }


export function formatMinute(worldMinute) {
    const minute = Number(worldMinute || 0) + Number(state.clockOffset || 0);
    const day = Math.floor(minute / 1440) + 1;
    const inDay = ((minute % 1440) + 1440) % 1440;
    const hours = String(Math.floor(inDay / 60)).padStart(2, "0");
    const minutes = String(inDay % 60).padStart(2, "0");
    return `第 ${day} 天 ${hours}:${minutes}`;
  }


export function humanizeKey(key) {
    return String(key || "").replace(/_/g, " ");
  }


export function itemLabel(itemId) {
    const value = String(itemId || "");
    const labels = state.view?.item_labels || {};
    if (labels[value]) return labels[value];
    const normalized = value.replace(/_/g, " ").toLowerCase();
    const match = Object.keys(labels).find((key) => key.replace(/_/g, " ").toLowerCase() === normalized);
    return match ? labels[match] : humanizeKey(value);
  }


export function translateTime(text) {
    const value = String(text || "");
    return value.replace(/(\d+)\s*minutes?/gi, "$1 分钟").replace(/(\d+)\s*hours?/gi, "$1 小时");
  }


function normalizedAttributeKey(value) {
  return String(value || "").replace(/_/g, " ").trim().toLowerCase();
}


function configuredAttribute(name) {
  const wanted = normalizedAttributeKey(name);
  return (state.view?.player?.attributes || []).find((item) =>
    normalizedAttributeKey(item.id) === wanted || normalizedAttributeKey(item.label) === wanted
  ) || null;
}


function attributeText(name) {
  return configuredAttribute(name)?.label || humanizeKey(String(name || "").trim());
}


export function translateCost(text) {
  let value = String(text || "").trim();
  if (!value) return value;
  const perMinute = value.match(/^(.+?) ([\d.]+) \(per minute\)\.?$/i);
  if (perMinute && configuredAttribute(perMinute[1])) {
    const sign = configuredAttribute(perMinute[1])?.cost_mode === "increase" ? "+" : "−";
    return `${attributeText(perMinute[1])} ${sign}${perMinute[2]}/分钟`;
  }
  const fixed = value.match(/^(.+?) (-?[\d.]+)\.?$/i);
  if (fixed && configuredAttribute(fixed[1])) {
    const sign = configuredAttribute(fixed[1])?.cost_mode === "increase" ? "+" : "−";
    return `${attributeText(fixed[1])} ${sign}${Math.abs(Number(fixed[2]))}`;
  }
  return translateImpact(value);
}


export function translateImpact(text) {
    let value = String(text || "").trim();
    if (!value) return value;
    const direct = [
      [/^Arrive at the selected location\.?$/i, "抵达目的地"],
      [/^No direct resource cost\.?$/i, "无直接消耗"],
      [/^Records the configured information result\.?$/i, "记录情报"],
      [/^Source stock -(\d+).*$/i, "资源点 -$1"],
    ];
    for (const [pattern, replacement] of direct) {
      if (pattern.test(value)) return value.replace(pattern, replacement);
    }
    const perMinute = value.match(/^(.+?) ([\d.]+) \(per minute\)\.?$/i);
    if (perMinute && configuredAttribute(perMinute[1])) {
      const sign = configuredAttribute(perMinute[1])?.cost_mode === "increase" ? "+" : "−";
      return `${attributeText(perMinute[1])} ${sign}${perMinute[2]}/分钟`;
    }
    const changes = value.match(/^(.+?) changes by (-?[\d.]+)\.?$/i);
    if (changes && configuredAttribute(changes[1])) {
      const delta = Number(changes[2]);
      return `${attributeText(changes[1])} ${delta >= 0 ? "+" : "−"}${Math.abs(delta)}`;
    }
    const amount = value.match(/^(.+?) (-?[\d.]+)\.?$/i);
    if (amount && configuredAttribute(amount[1])) {
      const delta = Number(amount[2]);
      return `${attributeText(amount[1])} ${delta >= 0 ? "+" : "−"}${Math.abs(delta)}`;
    }
    const worldChange = value.match(/^World (.+) changes by (-?\d+(?:\.\d+)?)\.?$/i);
    if (worldChange) {
      const key = worldChange[1].trim().replace(/\s+/g, "_");
      const meta = state.view?.world_property_labels?.[key] || {};
      const label = meta.label || humanizeKey(key);
      const delta = Number(worldChange[2]);
      if (meta.unit === "percent") return `${label} ${delta >= 0 ? "+" : "−"}${Math.abs(Math.round(delta * 100))}%`;
      return `${label} ${delta >= 0 ? "+" : "−"}${Math.abs(delta)}`;
    }
    const worldResource = value.match(/^World resource (.+?) ([+-]\d+(?:\.\d+)?)\.?$/i);
    if (worldResource) {
      const key = worldResource[1].trim().replace(/\s+/g, "_");
      return `${itemLabel(key)} ${worldResource[2]}`;
    }
    const useShared = value.match(/^Use ([\d.]+) (.+) \(carried or shared stock\)\.?$/i);
    if (useShared) return `使用 ${useShared[1]} ${itemLabel(useShared[2])}（随身或公共库存）`;
    const carried = value.match(/^Carried stock -([\d.]+) (.+) on success \(reserved before start\)\.?$/i);
    if (carried) return `预留 ${carried[1]} ${itemLabel(carried[2])}（开始时扣除）`;
    const withdraw = value.match(/^Withdraw ([\d.]+) (.+) from shared stock\.?$/i);
    if (withdraw) return `从公共库存取 ${withdraw[1]} ${itemLabel(withdraw[2])}`;
    const deposit = value.match(/^Deposit ([\d.]+) (.+) into shared stock\.?$/i);
    if (deposit) return `存入公共库存 ${deposit[1]} ${itemLabel(deposit[2])}`;
    const inventory = value.match(/^Inventory ([+-]\d+(?:\.\d+)?) (.+)\.?$/i);
    if (inventory) {
      return `${itemLabel(inventory[2])} ${inventory[1].replace("-", "−")}`;
    }
    const reserve = value.match(/^(.+?)\s+(\d+(?:\.\d+)?)\s+\(reserve\)\.?$/i);
    if (reserve) return `${itemLabel(reserve[1])} ×${reserve[2]}（预留）`;
    const recipe = value.match(/^Recipe inputs:\s*(.+)$/i);
    if (recipe) return `配方材料：${recipe[1]}`;
    return value;
  }


export function translateNarrative(text) {
    let value = String(text || "").trim();
    if (!value) return "";
    value = value.replace(/^The world:\s*/i, "");
    value = value.replace(/\s+entered the world\.?$/i, " 进入了世界。");
    value = value.replace(/\s+began\s+/i, " 开始 ");
    value = value.replace(/\s+started the action\s+/i, " 开始行动 ");
    value = value.replace(/\s+started\s+/i, " 开始 ");
    value = value.replace(/\s+completed\s+/i, " 完成 ");
    value = value.replace(/\s+failed\s+/i, " 失败 ");
    value = value.replace(/\s+researched\s+/i, " 研究了 ");
    value = value.replace(/\s+advanced research on\s+/i, " 推进研究 ");
    value = value.replace(/\s+surveyed\s+/i, " 勘察了 ");
    value = value.replace(/\s+recovered\s+/i, " 找回了 ");
    value = value.replace(/\s+kept a lesson:\s*/i, " 得到经验：");
    value = value.replace(/\s+registered\s+/i, " 注册了 ");
    value = value.replace(/\s+offered a trade to\s+/i, " 向 ");
    value = value.replace(/\s+completed a trade with\s+/i, " 与 ");
    value = value.replace(/\s+rejected a trade from\s+/i, " 拒绝了来自 ");
    value = value.replace(/: arrived\.?$/i, "：抵达。");
    value = value.replace(/Configured background controller selected the highest-priority executable behavior\.?/gi, "后台控制器选择了当前可执行的最高优先行动。");
    value = value.replace(/Player selected this action from the web client\.?/gi, "玩家从游戏界面选择了这个行动。");
    for (const [code, label] of Object.entries(mergedResultLabels())) {
      value = value.replace(new RegExp(`\\b${code}\\b`, "gi"), label);
    }
    value = value.replace(/\bworld property changed\b/gi, "世界状态发生变化");
    value = value.replace(/\bcapability evidence\b/gi, "能力证据");
    value = value.replace(/A direct exchange was recorded as unverified testimony\.?/gi, "一次直接交谈被记录为未经核实的证词。");
    value = value.replace(/\.\s*$/, "。");
    return value;
  }


export const BASE_RESULT_LABELS = {
  inspected: "检查完成",
  inspection_completed: "检查完成",
  resource_collected: "采集完成",
  recipe_completed: "制作完成",
  route_traversed: "抵达目的地",
  arrived: "抵达目的地",
  trade_completed: "交易完成",
  research_completed: "研究完成",
  research_progressed: "研究推进",
  information_recorded: "情报已记录",
  repaired: "修复完成",
  decision_failed: "决策中断",
};


/** World configuration and the current player projection's UI block. */
export function uiConfig() {
  return { ...(state.world?.ui || {}), ...(state.view?.ui || {}) };
}


export function mergedResultLabels() {
  return { ...BASE_RESULT_LABELS, ...(uiConfig().result_labels || {}) };
}


export function resultLabel(code) {
  const key = String(code || "");
  if (!key) return "";
  return mergedResultLabels()[key] || "";
}


export function formatWorldProperty(value, meta = {}) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value ?? "—");
  if (meta.unit === "bool") {
    if (numeric > 0 && meta.true_label) return String(meta.true_label);
    if (numeric <= 0 && meta.false_label) return String(meta.false_label);
    return numeric > 0 ? "已启用" : "未启用";
  }
  if (meta.unit === "percent") return `${Math.round(numeric * 100)}%`;
  if (meta.unit === "hours") return `${Math.round(numeric * 10) / 10} 小时`;
  return `${Math.round(numeric * 10) / 10}`;
}


/** Configured crisis metrics with live values and tone levels. */
export function crisisMetrics(source = state.view) {
  const configured = Array.isArray(source?.ui?.crisis_metrics) ? source.ui.crisis_metrics : [];
  const properties = source?.world_properties || {};
  const labels = source?.world_property_labels || {};
  const result = [];
  for (const metric of configured) {
    const key = String(metric?.property || "").trim();
    const value = properties[key];
    if (!key || value === undefined) continue;
    const base = labels[key] || {};
    const meta = {
      ...base,
      label: metric.label || base.label || humanizeKey(key),
      unit: metric.format || base.unit,
      max: metric.max ?? base.max,
      true_label: metric.true_label ?? base.true_label,
      false_label: metric.false_label ?? base.false_label,
    };
    const numeric = Number(value);
    let level = "";
    if (Number.isFinite(numeric)) {
      const danger = Number(metric.danger_below);
      const warning = Number(metric.warning_below);
      if (Number.isFinite(danger) && numeric < danger) level = "danger";
      else if (Number.isFinite(warning) && numeric < warning) level = "warning";
    }
    let display = formatWorldProperty(value, meta);
    if (meta.max && !["percent", "hours", "bool"].includes(meta.unit)) display += ` / ${meta.max}`;
    result.push({
      key,
      label: meta.label,
      value,
      meta,
      display,
      level,
      hint: String(metric.hint || ""),
    });
  }
  return result;
}


/** Worst configured crisis tone, used by scene and cover treatments. */
export function crisisMood(source = state.view) {
  const metrics = crisisMetrics(source);
  if (metrics.some((metric) => metric.level === "danger")) return "danger";
  if (metrics.some((metric) => metric.level === "warning")) return "warning";
  return "";
}


/** Fill ``{world_property}`` placeholders with live, formatted values. */
export function resolveWorldTemplate(text, source = state.world) {
  const properties = source?.world_properties || {};
  const labels = source?.world_property_labels || {};
  return String(text || "").replace(/\{([A-Za-z0-9_.-]+)\}/g, (_, key) => {
    if (properties[key] === undefined) return "—";
    return formatWorldProperty(properties[key], labels[key] || {});
  });
}


export function describeError(error, fallback = "操作失败") {
    const payload = error?.payload || {};
    const extra = [];
    if (payload.missing) extra.push(`缺失：${payload.missing}`);
    if (Array.isArray(payload.allowed_values) && payload.allowed_values.length) extra.push(`可选：${payload.allowed_values.join("、")}`);
    if (Array.isArray(payload.available_targets) && payload.available_targets.length) extra.push(`可选目标：${payload.available_targets.join("、")}`);
    return `${error?.message || fallback}${extra.length ? `（${extra.join("；")}）` : ""}`;
  }


export function worldIsOpen() {
    return Boolean(state.activeDb) && state.world?.status !== "no_world";
  }
