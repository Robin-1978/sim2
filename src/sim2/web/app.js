(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const PLAYER_KEY = "sim2.game.player";
  const OPERATOR_KEY = "sim2.game.operator";

  const state = {
    world: null,
    entry: null,
    instances: [],
    activeDb: "",
    playerId: null,
    playerName: "",
    playerToken: null,
    operatorToken: null,
    view: null,
    runtime: null,
    map: null,
    selectedOrigin: "",
    actionFilter: "全部",
    feedLimit: 28,
    feedEvents: [],
    taskKey: "",
    taskTotal: 0,
    clockOffset: 0,
    advancing: false,
    busy: false,
    online: false,
    lastDialogueKey: "",
    pollTimer: null,
    booted: false,
  };

  const ICONS = {
    refresh: '<path d="M20 11a8 8 0 1 0-2.4 5.7"/><path d="M20 4v7h-7"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.96 4.6H9a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1Z"/>',
  };

  function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function storageGet(key, fallback = null) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (error) {
      return fallback;
    }
  }

  function storageSet(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      /* storage can be blocked; play still works for this tab */
    }
  }

  function tokenFor(path) {
    if (path.startsWith("/runtime/") || path.startsWith("/instances/activate") || path.startsWith("/authoring/") || path.startsWith("/snapshots")) {
      return state.operatorToken || state.playerToken;
    }
    if (path.startsWith("/player/") || path.startsWith("/dialogue") || path.startsWith("/trade/")) {
      return state.playerToken || state.operatorToken;
    }
    return state.playerToken || state.operatorToken;
  }

  function setOnline(online) {
    state.online = Boolean(online);
    const dot = $("conn-dot");
    if (dot) dot.className = `conn-dot ${online ? "online" : "offline"}`;
  }

  async function request(path, options = {}) {
    const token = tokenFor(path);
    const method = String(options.method || "GET").toUpperCase();
    const isGet = method === "GET";
    path = isGet
      ? `${path}${path.includes("?") ? "&" : "?"}_sim2_ts=${Date.now()}`
      : path;
    let response;
    try {
      response = await fetch(path, {
        ...options,
        cache: isGet ? "no-store" : options.cache,
        headers: {
          "content-type": "application/json",
          ...(token ? { "x-sim2-session": token } : {}),
          ...(options.headers || {}),
        },
      });
    } catch (error) {
      setOnline(false);
      throw error;
    }
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      setOnline(false);
      throw new Error(`服务端返回了 ${response.status} ${type || "未知内容"}`);
    }
    const value = await response.json();
    setOnline(response.ok || response.status < 500);
    if (!response.ok) {
      const error = new Error(value.message || `请求失败 (${response.status})`);
      error.status = response.status;
      error.code = value.failed_check || value.status || null;
      error.payload = value;
      throw error;
    }
    return value;
  }

  function newIdempotencyKey() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return `web-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e12).toString(36)}`;
  }

  async function write(path, body = {}) {
    const key = newIdempotencyKey();
    const attempt = () => request(path, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ ...body, idempotency_key: key }),
    });
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
    try {
      return await fn();
    } finally {
      button.disabled = wasDisabled;
    }
  }

  /* ---------- Toasts ---------- */

  function toast(message, kind = "", timeout = 4200) {
    const root = $("toasts");
    if (!root || !message) return;
    while (root.children.length >= 4) root.firstElementChild?.remove();
    const node = el("div", `toast ${kind}`.trim(), message);
    root.append(node);
    setTimeout(() => node.remove(), timeout);
  }

  /* ---------- Formatting ---------- */

  const PHASES = { dawn: "清晨", workday: "白天", dusk: "黄昏", night: "夜晚" };

  function formatClock(worldTime) {
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

  function formatMinute(worldMinute) {
    const minute = Number(worldMinute || 0) + Number(state.clockOffset || 0);
    const day = Math.floor(minute / 1440) + 1;
    const inDay = ((minute % 1440) + 1440) % 1440;
    const hours = String(Math.floor(inDay / 60)).padStart(2, "0");
    const minutes = String(inDay % 60).padStart(2, "0");
    return `第 ${day} 天 ${hours}:${minutes}`;
  }

  function humanizeKey(key) {
    return String(key || "").replace(/_/g, " ");
  }

  function itemLabel(itemId) {
    const labels = state.view?.item_labels || {};
    return labels[String(itemId)] || humanizeKey(itemId);
  }

  function translateTime(text) {
    const value = String(text || "");
    return value.replace(/(\d+)\s*minutes?/gi, "$1 分钟").replace(/(\d+)\s*hours?/gi, "$1 小时");
  }

  function translateImpact(text) {
    let value = String(text || "").trim();
    if (!value) return value;
    const direct = [
      [/^Arrive at the selected location\.?$/i, "抵达目的地"],
      [/^No direct resource cost\.?$/i, "无直接消耗"],
      [/^Records the configured information result\.?$/i, "记录情报"],
      [/^World maintenance evidence changes by (\d+)\.?$/i, "维护记录 +$1"],
      [/^Source stock -(\d+).*$/i, "资源点 -$1"],
      [/^Stamina ([\d.]+) \(per minute\)\.?$/i, "精力 -$1/分钟"],
      [/^Integrity ([\d.]+)\.?$/i, "体力 -$1"],
      [/^Hydration ([\d.]+)\.?$/i, "水分 +$1"],
      [/^Satiety ([\d.]+)\.?$/i, "饱食 +$1"],
      [/^Belonging ([\d.]+)\.?$/i, "归属 +$1"],
      [/^Purpose ([\d.]+)\.?$/i, "意义 +$1"],
    ];
    for (const [pattern, replacement] of direct) {
      if (pattern.test(value)) return value.replace(pattern, replacement);
    }
    const inventory = value.match(/^Inventory ([+-]\d+(?:\.\d+)?) (.+)\.?$/i);
    if (inventory) {
      const raw = inventory[2].toLowerCase();
      const id = Object.keys(state.view?.item_labels || {}).find((key) => key.replace(/_/g, " ") === raw);
      return `${id ? itemLabel(id) : raw} ${inventory[1].replace("-", "−")}`;
    }
    return value;
  }

  function translateNarrative(text) {
    let value = String(text || "").trim();
    if (!value) return "";
    value = value.replace(/^The world:\s*/i, "");
    value = value.replace(/\s+entered the world\.?$/i, " 进入了世界。");
    value = value.replace(/\s+began\s+/i, " 开始 ");
    value = value.replace(/\s+started the action\s+/i, " 开始行动 ");
    value = value.replace(/\s+completed\s+/i, " 完成 ");
    value = value.replace(/\s+failed\s+/i, " 失败 ");
    value = value.replace(/: arrived\.?$/i, "：抵达。");
    value = value.replace(/Configured background controller selected the highest-priority executable behavior\.?/gi, "后台控制器选择了当前可执行的最高优先行动。");
    value = value.replace(/Player selected this action from the web client\.?/gi, "玩家从游戏界面选择了这个行动。");
    value = value.replace(/\.\s*$/, "。");
    return value;
  }

  function describeError(error, fallback = "操作失败") {
    const payload = error?.payload || {};
    const extra = [];
    if (payload.missing) extra.push(`缺失：${payload.missing}`);
    if (Array.isArray(payload.allowed_values) && payload.allowed_values.length) extra.push(`可选：${payload.allowed_values.join("、")}`);
    if (Array.isArray(payload.available_targets) && payload.available_targets.length) extra.push(`可选目标：${payload.available_targets.join("、")}`);
    return `${error?.message || fallback}${extra.length ? `（${extra.join("；")}）` : ""}`;
  }

  /* ---------- Entry ---------- */

  function renderEntry() {
    const world = state.world || {};
    const entry = state.entry || {};
    $("entry-title").textContent = world.title || "世界";
    if (world.title) document.title = `${world.title} · sim2`;
    $("entry-premise").textContent = world.premise || "读取世界入口…";
    $("entry-genre").textContent = world.genre || "世界模拟";
    $("entry-season").textContent = world.season || "—";
    $("entry-instance").textContent = world.instance_id || "当前实例";
    $("hud-world-title").textContent = world.title || "世界";
    const instanceSelect = $("entry-instance-select");
    instanceSelect.replaceChildren();
    const instances = state.instances.length ? state.instances : [{ instance_id: world.instance_id || "当前实例", active: true, world_minute: world.world_minute || 0 }];
    for (const item of instances) {
      const option = el("option", "", `${item.instance_id}${item.active ? "（当前）" : ""} · 第 ${Math.floor(Number(item.world_minute || 0) / 1440) + 1} 天`);
      option.value = item.instance_id || "";
      option.selected = Boolean(item.active);
      instanceSelect.append(option);
    }
    instanceSelect.disabled = true;

    const origins = Array.isArray(entry.origins) ? entry.origins : [];
    const list = $("origin-list");
    list.replaceChildren();
    if (!origins.length) {
      list.append(el("p", "hint", "该世界没有配置出身；输入玩家 ID 即可进入。"));
    }
    if (!origins.some((item) => item.id === state.selectedOrigin)) {
      state.selectedOrigin = origins[0]?.id || "";
    }
    for (const origin of origins) {
      const card = el("button", `origin-card${origin.id === state.selectedOrigin ? " active" : ""}`);
      card.type = "button";
      card.append(el("strong", "", origin.name || origin.id));
      if (origin.description) card.append(el("small", "", origin.description));
      if (origin.goal) card.append(el("div", "origin-goal", `目标：${origin.goal}`));
      card.addEventListener("click", () => {
        state.selectedOrigin = origin.id;
        for (const sibling of list.children) sibling.classList.remove("active");
        card.classList.add("active");
      });
      list.append(card);
    }

    const startMinute = Number(world.world_clock?.start_minute || 0);
    const totalMinute = Number(world.world_minute || 0) + startMinute;
    const clock = formatClock({ minute_of_day: totalMinute % 1440, day: Math.floor(totalMinute / 1440) + 1, day_phase: "" });
    $("entry-world-state").textContent = `世界时间 ${clock.day} ${clock.time}`;
  }

  async function loadEntryContext() {
    const results = await Promise.allSettled([
      request("/world"),
      request("/player/entry"),
      request("/instances"),
      request("/runtime"),
    ]);
    const [world, entry, instances, runtime] = results;
    if (world.status === "fulfilled") state.world = world.value;
    if (entry.status === "fulfilled") state.entry = entry.value;
    if (instances.status === "fulfilled" && Array.isArray(instances.value.instances)) {
      state.instances = instances.value.instances;
      state.activeDb = instances.value.active_db || "";
    }
    if (runtime.status === "fulfilled") state.runtime = runtime.value;
    renderEntry();
    renderRuntimeStatus();
  }

  function showEntry(message = "") {
    state.playerId = null;
    state.playerToken = null;
    state.view = null;
    storageSet(PLAYER_KEY, null);
    $("game").classList.add("hidden");
    $("entry-screen").classList.remove("hidden");
    $("entry-error").textContent = message || "";
    if (state.world) renderEntry();
  }

  function enterGame() {
    $("entry-screen").classList.add("hidden");
    $("game").classList.remove("hidden");
    $("entry-error").textContent = "";
  }

  async function submitEntry(button) {
    const playerId = String($("entry-id").value || "").trim();
    const name = String($("entry-name").value || "").trim();
    const originId = state.selectedOrigin;
    $("entry-error").textContent = "";
    if (!playerId) {
      $("entry-error").textContent = "请输入玩家 ID。";
      return;
    }
    if (!/^[A-Za-z0-9_.-]{2,64}$/.test(playerId)) {
      $("entry-error").textContent = "玩家 ID 只能包含字母、数字、下划线、点和连字符，长度 2-64。";
      return;
    }
    await withPending(button, async () => {
      try {
        const result = await write("/players", { player_id: playerId, origin_id: originId, name: name || undefined });
        if (result.status !== "accepted") throw new Error(result.message || "无法进入世界");
        state.playerId = result.player_id || playerId;
        state.playerName = name || state.playerId;
        state.playerToken = result.session_token || null;
        storageSet(PLAYER_KEY, { id: state.playerId, token: state.playerToken, name: state.playerName });
        enterGame();
        toast(result.resumed ? `已恢复角色 ${state.playerName}。` : `欢迎来到${state.world?.title || "这个世界"}，${state.playerName}。`, "ok");
        await Promise.all([loadView(), loadRuntime()]);
        await Promise.allSettled([loadFeed(), loadDialogue(), loadTrades()]);
      } catch (error) {
        $("entry-error").textContent = describeError(error, "无法进入世界");
      }
    });
  }

  async function restorePlayer() {
    const saved = storageGet(PLAYER_KEY, null);
    if (!saved?.id || !saved?.token) return false;
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

  async function loadView() {
    if (!state.playerId) return null;
    let view;
    try {
      view = await request(`/player/view?player_id=${encodeURIComponent(state.playerId)}`);
    } catch (error) {
      if (error.status === 401 || error.status === 403) {
        showEntry("角色会话已失效，请重新进入。");
      }
      throw error;
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

  function syncTaskProgress(task) {
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

  function renderAll() {
    if (!state.view) return;
    renderClock();
    renderCharacter();
    renderScene();
    renderTask();
    renderActions();
    renderGoals();
    renderWorldProperties();
    renderTalkTargets();
    renderTaskHint();
    $("last-update").textContent = `同步于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  }

  function renderClock() {
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

  function renderCharacter() {
    const view = state.view;
    const player = view.player || {};
    const name = player.name || state.playerName || state.playerId || "?";
    $("player-name").textContent = name;
    $("player-portrait").textContent = String(name).slice(0, 1);
    $("player-role").textContent = player.role && player.role !== "player" ? player.role : "公社居民";
    $("player-location").textContent = player.location || view.location?.name || "未知地点";
    $("player-goal").textContent = player.goal || "在公社里找到自己的位置。";

    const badges = $("player-badges");
    badges.replaceChildren();
    badges.append(el("span", player.location_safe ? "badge safe" : "badge danger", player.location_safe ? "安全地点" : "危险地点"));
    const locationTags = Array.isArray(player.location_tags) ? player.location_tags : [];
    const tagNames = { craft: "工坊", social: "社交", market: "集市", resource: "资源", research: "研究", defense: "防线", home: "住屋", infrastructure: "基础设施", exploration: "探索", public: "公共" };
    for (const tag of locationTags) {
      if (tagNames[tag]) badges.append(el("span", "badge", tagNames[tag]));
    }

    const attributes = Array.isArray(player.attributes) ? player.attributes : [];
    const needRoot = $("need-bars");
    const vitalRoot = $("vital-bars");
    needRoot.replaceChildren();
    vitalRoot.replaceChildren();
    for (const attr of attributes) {
      const value = Number(attr.value || 0);
      const row = el("div", "bar-row");
      const label = el("div", "bar-label");
      label.append(el("span", "", attr.label || humanizeKey(attr.id)));
      label.append(el("strong", "", `${Math.round(value)}${attr.unit === "%" ? "%" : ""}`));
      const track = el("div", "bar-track");
      const fill = el("span", `bar-fill ${barTone(attr.id || attr.label, value)}`);
      fill.style.width = `${clamp(value, 0, 100)}%`;
      track.append(fill);
      row.append(label, track);
      if (attr.kind === "need") needRoot.append(row);
      else vitalRoot.append(row);
    }
    if (!needRoot.children.length) needRoot.append(el("p", "hint", "没有可显示的需求。"));
    $("inventory-count").textContent = `${Object.keys(view.inventory || {}).filter((key) => Number(view.inventory[key]) > 0).length} 种`;
    renderInventory();
    renderCapabilities();
  }

  function barTone(id, value) {
    const key = String(id || "").toLowerCase();
    if (key.includes("belonging") || key.includes("purpose") || key.includes("归属") || key.includes("意义")) return value <= 30 ? "low" : "quiet";
    const threshold = Number(state.view?.alert_thresholds?.[`${key}_alert_threshold`]);
    if (Number.isFinite(threshold)) {
      if (value <= threshold * 0.6) return "low";
      return value <= threshold ? "warn" : "";
    }
    return value <= 30 ? "low" : value <= 45 ? "warn" : "";
  }

  function renderInventory() {
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
      item.append(copy);
      grid.append(item);
    }
  }

  function renderCapabilities() {
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

  function sceneTheme(location) {
    const tags = new Set(location?.tags || []);
    if (tags.has("craft")) return "workshop";
    if (tags.has("research")) return "observatory";
    if (tags.has("resource") && tags.has("exploration")) return "marsh";
    if (tags.has("defense") || tags.has("infrastructure")) return "gatehouse";
    if (tags.has("home")) return "lantern";
    if (tags.has("market") || tags.has("social")) return "quay";
    return "harbor";
  }

  function renderScene() {
    const view = state.view;
    const location = view.location || {};
    const player = view.player || {};
    const art = $("scene-art");
    art.dataset.scene = sceneTheme(location);
    $("scene-title").textContent = location.name || player.location || "未知地点";
    $("scene-description").textContent = location.description || "这里没有更多描述。";
    $("scene-eyebrow").textContent = player.location_safe ? "安全地点" : "你在这里";
    const tags = $("scene-tags");
    tags.replaceChildren();
    const tagNames = { public: "公共", social: "社交", market: "集市", resource: "资源", craft: "工坊", safe: "安全", research: "研究", exploration: "探索", defense: "防线", infrastructure: "基础设施", home: "住屋" };
    for (const tag of location.tags || []) {
      if (tagNames[tag]) tags.append(el("span", "badge", tagNames[tag]));
    }

    const objects = $("scene-objects");
    objects.replaceChildren();
    const nearby = Array.isArray(view.nearby_entities) ? view.nearby_entities : [];
    const objectItems = nearby.filter((item) => item.type && !["agent", "player", "resource"].includes(item.type));
    for (const item of objectItems.slice(0, 4)) {
      const status = item.initial_state?.status || "";
      const chip = el("div", "scene-object");
      chip.append(el("i"));
      chip.append(el("span", "", `${item.name || item.id}${status ? ` · ${status}` : ""}`));
      objects.append(chip);
    }

    const people = $("scene-people");
    people.replaceChildren();
    const participants = (view.nearby_participants || []).filter((item) => item.id !== state.playerId);
    if (!participants.length) people.append(el("span", "scene-empty", "这里暂时没有别人。"));
    for (const person of participants.slice(0, 8)) {
      const chip = el("button", `person-chip${person.status && person.status !== "idle" ? "" : " person-idle"}`);
      chip.type = "button";
      chip.title = `和 ${person.name || person.id} 交谈`;
      chip.append(el("span", "person-avatar", String(person.name || person.id).slice(0, 1)));
      const copy = el("span", "person-copy");
      copy.append(el("strong", "", person.name || person.id));
      copy.append(el("span", "", person.status === "executing" ? "正在忙碌" : "可以交谈"));
      chip.append(copy);
      chip.addEventListener("click", () => {
        switchTab("talk");
        const select = $("talk-target");
        const option = [...select.options].find((item) => item.value === person.id);
        if (option) {
          select.value = person.id;
          $("talk-message").focus();
        } else {
          toast("对方不在可交谈距离内。", "error");
        }
      });
      people.append(chip);
    }

    const resources = $("scene-resources");
    resources.replaceChildren();
    const resourceItems = nearby.filter((item) => item.type === "resource");
    if (!resourceItems.length) resources.append(el("span", "scene-empty", "这里没有可采集的资源点。"));
    for (const item of resourceItems) {
      const amount = Number(item.amount ?? item.initial_state?.quantity ?? 0);
      const chip = el("span", `resource-chip${amount <= 0 ? " empty" : ""}`);
      chip.append(el("span", "", item.name || item.id));
      chip.append(el("strong", "", amount <= 0 ? "枯竭" : Math.round(amount)));
      resources.append(chip);
    }
  }

  function renderTask() {
    const view = state.view;
    const task = view?.active_task || {};
    const panel = $("task-panel");
    const active = Boolean(task.bt || task.action);
    panel.classList.toggle("hidden", !active);
    if (!active) {
      $("task-progress-bar").style.width = "0%";
      return;
    }
    const remaining = Number(task.remaining_minutes || 0);
    const total = Math.max(1, Number(state.taskTotal || remaining || 1));
    $("task-name").textContent = task.bt || "行动中";
    const provider = selectedProvider();
    $("task-detail").textContent = `${task.action || "推进中"} · 还需 ${remaining} 分钟${state.advancing ? ` · 同步推进世界${provider ? `（${provider}）` : ""}` : ""}`;
    const progress = clamp(((total - remaining) / total) * 100, 2, 100);
    $("task-progress-bar").style.width = `${progress}%`;
    const running = Boolean(state.runtime?.running);
    $("task-advance").disabled = running || state.advancing;
    $("task-wait").disabled = running || state.advancing;
  }

  function renderTaskHint() {
    const root = $("task-detail");
    if (!root || !state.advancing) return;
  }

  /* ---------- Actions ---------- */

  const CATEGORY_ORDER = ["移动", "采集", "制作", "公共", "社交", "恢复", "其他"];

  function actionCategory(action) {
    if (action.kind === "travel" || action.ref === "behavior:travel") return "移动";
    const ref = String(action.ref || "");
    if (ref.includes("gather")) return "采集";
    if (ref.includes("craft")) return "制作";
    if (ref.includes("communicate")) return "社交";
    if (ref.includes("eat") || ref.includes("drink")) return "恢复";
    if (ref.includes("inspect") || ref.includes("repair") || ref.includes("research") || ref.includes("explore") || ref.includes("tune")) return "公共";
    return "其他";
  }

  function actionButtonLabel(category, action) {
    if (category === "移动") return "前往";
    if (category === "采集") return "开始采集";
    if (category === "制作") return "开始制作";
    if (category === "社交") return "交谈";
    if (category === "恢复") return "立即使用";
    if (category === "公共") return "执行";
    return "开始";
  }

  function renderActions() {
    const view = state.view;
    const actions = Array.isArray(view?.actions) ? view.actions : [];
    const filterRoot = $("action-filters");
    const present = new Set(actions.map(actionCategory));
    const filters = ["全部", ...CATEGORY_ORDER.filter((item) => present.has(item))];
    if (!filters.includes(state.actionFilter)) state.actionFilter = "全部";
    filterRoot.replaceChildren();
    for (const item of filters) {
      const button = el("button", `segment${item === state.actionFilter ? " active" : ""}`, item);
      button.type = "button";
      button.setAttribute("role", "tab");
      button.addEventListener("click", () => {
        state.actionFilter = item;
        renderActions();
      });
      filterRoot.append(button);
    }

    const grid = $("action-grid");
    grid.replaceChildren();
    if (!actions.length) {
      grid.append(el("div", "inventory-empty", "现在没有平台认可的行动；可以在地图上换一个地点。"));
    }
    const filtered = actions.filter((action) => state.actionFilter === "全部" || actionCategory(action) === state.actionFilter);
    for (const action of filtered) {
      grid.append(actionCard(action));
    }

    const unavailable = Array.isArray(view?.unavailable_actions) ? view.unavailable_actions : [];
    const locked = $("locked-actions");
    locked.classList.toggle("hidden", !unavailable.length);
    $("locked-count").textContent = `（${unavailable.length}）`;
    const lockedList = $("locked-list");
    lockedList.replaceChildren();
    for (const item of unavailable.slice(0, 12)) {
      const row = el("div", "locked-row");
      row.append(el("strong", "", item.label || item.ref || "行动"));
      row.append(el("small", "", (item.reasons || []).map(humanizeReason).join("；") || "当前条件不满足"));
      lockedList.append(row);
    }
  }

  function actionCard(action) {
    const category = actionCategory(action);
    const card = el("article", "action-card");
    card.dataset.category = category;
    const head = el("div", "action-head");
    head.append(el("strong", "", action.choice || action.name || "行动"));
    head.append(el("span", "action-time", translateTime(action.time || "时间未知")));
    card.append(head);
    card.append(el("p", "", action.summary || action.purpose || "按照世界规则执行这个行动。"));
    const meta = el("div", "action-meta");
    const chips = [];
    for (const value of action.cost || []) chips.push(translateImpact(value));
    for (const value of (action.impact || []).slice(0, 2)) chips.push(translateImpact(value));
    for (const value of chips.filter(Boolean).slice(0, 4)) meta.append(el("span", "action-chip", value));
    card.append(meta);
    const foot = el("div", "action-foot");
    const button = el("button", "button ghost", actionButtonLabel(category, action));
    button.type = "button";
    button.addEventListener("click", () => beginAction(action, button));
    foot.append(button);
    card.append(foot);
    return card;
  }

  function humanizeReason(reason) {
    const text = String(reason || "").trim();
    let match = text.match(/^actor\.need\.(\w+) ([<>]=?) (\d+(?:\.\d+)?)$/);
    if (match) {
      const needNames = { hydration: "水分", satiety: "饱食", belonging: "归属", purpose: "意义" };
      const symbol = match[2].includes("<") ? "低于或等于" : "高于";
      return `需要${needNames[match[1]] || match[1]}${symbol} ${match[3]}`;
    }
    match = text.match(/^actor\.inventory\.(\w+) >= (\d+(?:\.\d+)?)$/);
    if (match) return `需要 ${itemLabel(match[1])} 至少 ${match[2]} 个`;
    const words = [
      [/recipe\.available/, "配方当前不可用"],
      [/inputs\.available/, "随身材料不足"],
      [/station\.available/, "工坊当前不可用"],
      [/object\.available/, "目标设施不可用"],
      [/route\.known/, "路线尚未确认"],
      [/actor\.alive/, "角色状态不允许"],
      [/actor\.can_(\w+)/, (_m, cap) => `需要${cap}能力`],
      [/Action gather_resource cannot start: required resource (.+) is unavailable/, (_m, name) => `${name} 资源点已经枯竭`],
      [/Action craft_recipe cannot start: required carried item (.+) is unavailable/, (_m, name) => `缺少随身材料：${name}`],
    ];
    for (const [pattern, replacement] of words) {
      if (pattern.test(text)) return text.replace(pattern, replacement);
    }
    return text;
  }

  async function beginAction(action, button) {
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

  async function submitAction(action, extra = {}, button = null) {
    if (!state.playerId || state.busy) return;
    state.busy = true;
    const label = action.choice || action.name || "行动";
    await withPending(button, async () => {
      try {
        const result = await write("/player/act", {
          player_id: state.playerId,
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

  function parseMinutes(text) {
    const match = String(text || "").match(/(\d+)\s*(?:minutes?|分钟)/i);
    return match ? Number(match[1]) : 0;
  }

  async function advanceTask(prefix = "") {
    if (!state.playerId || state.advancing) return;
    state.advancing = true;
    let decisions = 0;
    let errors = 0;
    let stopReason = "";
    let chunks = 0;
    renderTask();
    try {
      for (let index = 0; index < 80; index += 1) {
        const task = state.view?.active_task || {};
        if (!task.bt && !task.action) break;
        const remaining = Number(task.remaining_minutes || 0);
        const minutes = Math.max(1, Math.min(remaining + 1, 10));
        const result = await write("/player/wait", {
          player_id: state.playerId,
          minutes,
          provider: selectedProvider(),
        });
        if (result.status !== "accepted") throw new Error(result.message || "无法推进世界");
        decisions += Number(result.report?.decisions || 0);
        errors += Number(result.report?.llm_errors || 0);
        stopReason = result.report?.stop_reason || stopReason;
        chunks += 1;
        await loadView();
        renderTask();
      }
      const stillActive = Boolean(state.view?.active_task?.bt || state.view?.active_task?.action);
      const worldTime = state.view?.world_time?.label ? `${state.view.world_time.day} 日 ${state.view.world_time.time_of_day}` : "";
      if (stillActive) {
        toast("行动还没有结束；可以再点一次“推进到完成”。", "error", 6000);
      } else if (errors) {
        toast(`行动完成，但同步推进中有 ${errors} 次 NPC 决策失败${stopReason ? `（${stopReason}）` : ""}；这 ${decisions} 次决策里请检查模型配置。`, "error", 8000);
      } else {
        toast(`${prefix}行动完成${worldTime ? ` · ${worldTime}` : ""}${chunks ? ` · NPC 决策 ${decisions} 次` : ""}`, "ok", 6000);
      }
      await Promise.allSettled([loadFeed(), loadTrades()]);
    } catch (error) {
      toast(describeError(error, "无法推进世界"), "error", 7000);
    } finally {
      state.advancing = false;
      renderTask();
    }
  }

  async function waitOneMinute(button) {
    if (!state.playerId || state.advancing) return;
    await withPending(button, async () => {
      try {
        const result = await write("/player/wait", { player_id: state.playerId, minutes: 1, provider: selectedProvider() });
        if (result.status !== "accepted") throw new Error(result.message || "无法推进世界");
        await loadView();
        const decisions = Number(result.report?.decisions || 0);
        toast(`世界向前推进了一分钟${decisions ? `，NPC 决策 ${decisions} 次` : ""}。`, "ok");
        await Promise.allSettled([loadFeed(), loadDialogue()]);
      } catch (error) {
        toast(describeError(error, "无法推进世界"), "error");
      }
    });
  }

  function selectedProvider() {
    const value = String($("settings-provider")?.value || "").trim();
    return value || state.runtime?.provider || null;
  }

  /* ---------- Goals / world properties ---------- */

  function renderGoals() {
    const root = $("goal-list");
    root.replaceChildren();
    const goals = Array.isArray(state.view?.goals) ? state.view.goals : [];
    if (!goals.length) {
      root.append(el("p", "goal-empty", "当前世界没有公开的目标投影。"));
      return;
    }
    const statusNames = { ready: "可推进", needs_support: "需准备", blocked: "受阻", completed: "已完成" };
    goals.forEach((goal, index) => {
      const status = String(goal.status || "blocked");
      const row = el("div", `goal-row ${status}`);
      row.append(el("span", "goal-mark", String(index + 1)));
      const copy = el("div", "goal-copy");
      copy.append(el("strong", "", goal.label || goal.id));
      copy.append(el("span", "", goal.summary || ""));
      row.append(copy);
      const progress = goal.progress && Number(goal.progress.required) > 0
        ? `${goal.progress.current}/${goal.progress.required}`
        : statusNames[status] || status;
      row.append(el("span", "goal-progress", progress));
      row.title = (goal.ready_behaviors || []).length ? `现在可做：${goal.ready_behaviors.join("、")}` : (goal.support_behaviors || []).join("、");
      root.append(row);
    });
  }

  function renderWorldProperties() {
    const root = $("world-properties");
    root.replaceChildren();
    const properties = state.view?.world_properties || {};
    const labels = state.view?.world_property_labels || {};
    const entries = Object.entries(properties);
    if (!entries.length) {
      root.append(el("p", "hint", "当前世界没有公开数值。"));
      return;
    }
    for (const [key, value] of entries) {
      const meta = labels[key] || {};
      const label = meta.label || humanizeKey(key);
      const numeric = Number(value);
      let display;
      if (meta.unit === "percent") display = `${Math.round(numeric * 100)}%`;
      else display = `${Math.round(numeric * 10) / 10}`;
      if (meta.max && meta.unit !== "percent") display += ` / ${meta.max}`;
      const row = el("div", "world-prop");
      row.append(el("span", "", label));
      row.append(el("strong", "", display));
      const track = el("div", "track");
      const fill = el("span");
      const max = Number(meta.max || (meta.unit === "percent" ? 1 : Math.max(1, numeric)));
      fill.style.width = `${clamp((numeric / max) * 100, 0, 100)}%`;
      track.append(fill);
      row.append(track);
      root.append(row);
    }
  }

  /* ---------- Talk ---------- */

  function renderTalkTargets() {
    const participants = (state.view?.nearby_participants || []).filter((item) => item.id !== state.playerId);
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

  async function loadDialogue() {
    const root = $("dialogue-list");
    if (!root) return;
    try {
      const response = await request("/observer/dialogue?limit=60");
      const exchanges = (response.exchanges || []).filter((item) => item.speaker_id === state.playerId || item.listener_id === state.playerId);
      const latest = exchanges.slice(-14).reverse();
      root.replaceChildren();
      if (!latest.length) {
        root.append(el("p", "dialogue-empty", "还没有人跟你交谈。走近一位居民，或者先开口。"));
        return;
      }
      for (const item of latest) {
        const bubble = el("div", "dialogue-bubble");
        const mine = item.speaker_id === state.playerId;
        bubble.append(el("strong", "", `${mine ? "你" : item.speaker} → ${item.listener_id === state.playerId ? "你" : item.listener}`));
        bubble.append(el("p", "", item.message || ""));
        root.append(bubble);
      }
    } catch (error) {
      root.replaceChildren(el("p", "dialogue-empty", "对话记录暂不可用。"));
    }
  }

  async function sendDialogue(form) {
    const target = $("talk-target")?.value;
    const message = String($("talk-message")?.value || "").trim();
    if (!target) return toast("附近没有可交谈的人。", "error");
    if (!message) return toast("先说点什么。", "error");
    const button = $("talk-send");
    await withPending(button, async () => {
      try {
        const result = await write("/dialogue", {
          speaker_id: state.playerId,
          listener_id: target,
          topic: "general",
          message,
          provider: selectedProvider() || undefined,
        });
        if (result.status !== "accepted") throw new Error(result.message || "对话未发送");
        $("talk-message").value = "";
        const reply = result.reply || {};
        toast(reply.status === "rejected" ? "消息已记录，但 NPC 回复暂不可用。" : "对话已发送。", reply.status === "rejected" ? "error" : "ok");
        await Promise.allSettled([loadView(), loadDialogue(), loadFeed()]);
      } catch (error) {
        toast(describeError(error, "对话未发送"), "error");
      }
    });
  }

  /* ---------- Trade ---------- */

  function bundleText(bundle) {
    const parts = Object.entries(bundle || {}).map(([key, value]) => `${itemLabel(key)}×${value}`);
    return parts.length ? parts.join("、") : "—";
  }

  function parseBundle(text) {
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

  async function loadTrades() {
    const root = $("trade-list");
    if (!root || !state.playerId) return;
    try {
      const response = await request(`/trade/offers?agent_id=${encodeURIComponent(state.playerId)}&include_closed=0`);
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

  async function answerOffer(kind, offer, button) {
    await withPending(button, async () => {
      try {
        const result = await write(`/trade/${kind}`, { recipient_id: state.playerId, offer_id: offer.offer_id });
        if (result.status !== "accepted") throw new Error(result.message || "交易未完成");
        toast(kind === "accept" ? "交易已完成。" : "已拒绝报价。", "ok");
        await Promise.allSettled([loadView(), loadTrades(), loadFeed()]);
      } catch (error) {
        toast(describeError(error, "交易未完成"), "error");
      }
    });
  }

  async function sendTrade(form) {
    const target = $("trade-target")?.value;
    const give = parseBundle($("trade-give")?.value || "");
    const want = parseBundle($("trade-want")?.value || "");
    if (!target) return toast("附近没有可交易的人。", "error");
    if (!give || !want) return toast("用 item=数量 的格式填写给出和想要的物品。", "error");
    const button = $("trade-send");
    await withPending(button, async () => {
      try {
        const result = await write("/trade/offers", {
          offerer_id: state.playerId,
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

  /* ---------- Feed ---------- */

  const SKIP_EVENTS = new Set([
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
  ]);

  function eventKind(type) {
    if (type.includes("dialogue") || type.includes("conversation")) return "dialogue";
    if (type.includes("resource") || type.includes("ration") || type.includes("rope")) return "resource";
    if (type.includes("repair") || type.includes("gate") || type.includes("signal")) return "repair";
    if (type.includes("story") || type.includes("beat")) return "story";
    return "";
  }

  function eventTitle(type) {
    const kind = eventKind(type);
    if (kind === "dialogue") return "对话";
    if (kind === "resource") return "采集与制作";
    if (kind === "repair") return "公共设施";
    if (kind === "story") return "世界剧情";
    if (type.startsWith("behavior.")) return "角色行动";
    if (type.startsWith("trade.")) return "交易";
    if (type.startsWith("need.")) return "需求";
    if (type.startsWith("player.")) return "角色";
    return "世界";
  }

  async function loadFeed(force = false) {
    try {
      const response = await request("/timeline?limit=80&after=0&latest=1");
      const events = (response.events || []).filter((event) => !SKIP_EVENTS.has(String(event.event_type || "")));
      state.feedEvents = events.slice().reverse();
      renderFeed();
    } catch (error) {
      if (force) toast("世界动态暂不可用。", "error");
    }
  }

  function renderFeed() {
    const root = $("world-feed");
    if (!root) return;
    root.replaceChildren();
    const events = state.feedEvents.slice(0, state.feedLimit);
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

  /* ---------- Map ---------- */

  async function openMap() {
    if (!state.playerId) return;
    try {
      const map = await request(`/map?viewer_id=${encodeURIComponent(state.playerId)}&observer=0`);
      state.map = map;
      renderMap();
      const dialog = $("map-dialog");
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "open");
    } catch (error) {
      toast(describeError(error, "地图暂不可用"), "error");
    }
  }

  function renderMap() {
    $("map-world-title").textContent = state.world?.title || "世界";
    const canvas = $("map-canvas");
    canvas.replaceChildren();
    const nodes = Array.isArray(state.map?.nodes) ? state.map.nodes : [];
    if (!nodes.length) {
      canvas.append(el("p", "hint", "没有可见地点。"));
      return;
    }
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
    const width = 640;
    const height = 380;
    const pad = 84;
    const px = (x) => pad + ((x - minX) / Math.max(1, maxX - minX)) * (width - pad * 2);
    const py = (y) => pad + ((y - minY) / Math.max(1, maxY - minY)) * (height - pad * 2);
    const routes = new Set((state.view?.routes || []).map((route) => route.id));
    const current = state.view?.location?.id;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("class", "map-lines");
    svg.setAttribute("aria-hidden", "true");
    for (const edge of state.map?.edges || []) {
      const from = positions.find((item) => item.node.id === edge.from);
      const to = positions.find((item) => item.node.id === edge.to);
      if (!from || !to) continue;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", px(from.x));
      line.setAttribute("y1", py(from.y));
      line.setAttribute("x2", px(to.x));
      line.setAttribute("y2", py(to.y));
      line.setAttribute("stroke", "rgba(139,204,192,.22)");
      line.setAttribute("stroke-width", "1");
      line.setAttribute("stroke-dasharray", "4 5");
      svg.append(line);
    }
    const wrap = el("div", "map-stage");
    wrap.append(svg);
    for (const item of positions) {
      const node = item.node;
      const reachable = routes.has(node.id);
      const button = el("button", `map-node${node.id === current ? " current" : ""}${reachable ? " reachable" : ""}`);
      button.type = "button";
      button.style.left = `${(px(item.x) / width) * 100}%`;
      button.style.top = `${(py(item.y) / height) * 100}%`;
      button.append(el("strong", "", node.name || node.id));
      button.append(el("small", "", node.id === current ? "你在这里" : reachable ? "可前往" : (node.tags || []).join(" · ")));
      if (reachable && node.id !== current) {
        // The server words every travel action from the world's ui_text, so the
        // map reuses it instead of composing its own phrases.
        const travelAction = () => (state.view?.actions || []).find(
          (entry) => entry.ref === "behavior:travel" && entry.parameters?.location_id === node.id,
        );
        button.title = travelAction()?.choice || node.name || node.id;
        button.addEventListener("click", () => {
          const dialog = $("map-dialog");
          if (dialog.open) dialog.close();
          // beginAction forwards action.parameters; calling submitAction directly
          // sent an empty parameter object, dropping the destination.
          beginAction(travelAction() || {
            ref: "behavior:travel",
            choice: node.name || node.id,
            name: node.name || node.id,
            kind: "travel",
            parameters: { location_id: node.id },
          });
        });
      }
      wrap.append(button);
    }
    canvas.append(wrap);
    const legend = $("map-legend");
    legend.textContent = "金色边框是当前位置，亮色节点可以直接前往；虚线表示已知路线。";
  }

  /* ---------- Settings / runtime ---------- */

  function renderProviderSelect() {
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
    let chosen = previous;
    if (stored === null && names.includes("remote")) chosen = "remote";
    if (chosen && [...select.options].some((option) => option.value === chosen)) select.value = chosen;
  }

  function renderRuntimeStatus() {
    const runtime = state.runtime || {};
    renderProviderSelect();
    const running = Boolean(runtime.running);
    const pill = $("runtime-pill");
    if (pill) {
      pill.textContent = running ? "自动运行中" : "手动世界";
      pill.className = `pill${running ? " live" : ""}`;
    }
    const detail = $("runtime-detail");
    if (detail) {
      if (running) {
        detail.textContent = `自动运行中 · 从第 ${runtime.start_minute ?? "?"} 分钟到第 ${runtime.target_minute ?? "?"} 分钟 · 模型 ${runtime.provider || "默认"}`;
      } else {
        detail.textContent = `手动世界 · 第 ${runtime.world_minute ?? state.view?.world_time?.minute ?? 0} 分钟${runtime.last_error ? ` · 上次错误：${runtime.last_error}` : ""}`;
      }
    }
    renderTask();
  }

  async function loadRuntime() {
    try {
      state.runtime = await request("/runtime");
      renderRuntimeStatus();
    } catch (error) {
      setOnline(false);
    }
  }

  async function refreshAll() {
    if (!state.playerId) return;
    try {
      await Promise.all([loadView(), loadRuntime()]);
      await Promise.allSettled([loadFeed(), loadDialogue(), loadTrades()]);
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) toast(describeError(error, "刷新失败"), "error");
    }
  }

  async function loadInstances() {
    try {
      const response = await request("/instances");
      state.instances = response.instances || [];
      state.activeDb = response.active_db || "";
      renderInstanceSelects();
    } catch (error) {
      toast(describeError(error, "无法读取实例列表"), "error");
    }
  }

  function renderInstanceSelects() {
    const select = $("settings-instance");
    if (!select) return;
    select.replaceChildren();
    for (const item of state.instances) {
      select.append(new Option(`${item.instance_id}${item.active ? "（当前）" : ""} · ${item.world_id || "?"}`, item.instance_id));
      if (item.active) select.value = item.instance_id;
    }
    const entrySelect = $("entry-instance-select");
    if (entrySelect && !entrySelect.options.length) {
      for (const item of state.instances) entrySelect.append(new Option(`${item.instance_id}${item.active ? "（当前）" : ""}`, item.instance_id));
    }
  }

  async function unlockOperator() {
    const raw = String($("operator-token").value || "").trim();
    if (!raw) return toast("请输入 Operator Token。", "error");
    try {
      const result = await write("/session", { role: "operator", operator_token: raw });
      if (result.status !== "ok" || !result.session_token) throw new Error(result.message || "解锁失败");
      state.operatorToken = result.session_token;
      try { sessionStorage.setItem(OPERATOR_KEY, state.operatorToken); } catch (error) { /* ignore */ }
      $("operator-token").value = "";
      toast("管理写入已解锁。", "ok");
      await loadRuntime();
    } catch (error) {
      toast(describeError(error, "解锁失败"), "error");
    }
  }

  async function activateInstance() {
    const select = $("settings-instance");
    const instanceId = select?.value;
    if (!instanceId) return;
    if (!state.operatorToken) return toast("切换实例需要先解锁管理。", "error");
    try {
      const result = await write("/instances/activate", { instance_id: instanceId });
      if (result.status !== "accepted") throw new Error(result.message || "切换失败");
      storageSet(PLAYER_KEY, null);
      window.location.reload();
    } catch (error) {
      toast(describeError(error, "切换实例失败"), "error");
    }
  }

  async function runtimeCommand(path, body = {}) {
    if (!state.operatorToken) return toast("自动运行需要先解锁管理。", "error");
    try {
      const result = await write(path, body);
      if (result.status === "rejected") throw new Error(result.message || "运行时操作被拒绝");
      toast(path.endsWith("/start") ? "自动运行已开始。" : "运行时已更新。", "ok");
      await loadRuntime();
    } catch (error) {
      toast(describeError(error, "运行时操作失败"), "error");
    }
  }

  function openSettings() {
    $("settings-drawer").classList.remove("hidden");
    renderInstanceSelects();
    renderRuntimeStatus();
  }

  function closeSettings() {
    $("settings-drawer").classList.add("hidden");
  }

  /* ---------- Action dialog ---------- */

  function ensureActionDialog() {
    let dialog = $("action-dialog");
    if (dialog) return dialog;
    dialog = el("dialog", "map-dialog action-dialog");
    dialog.id = "action-dialog";
    const form = el("form", "stack-form");
    form.method = "dialog";
    const head = el("div", "map-dialog-head");
    const titleWrap = el("div");
    titleWrap.append(el("span", "eyebrow", "行动配置"));
    titleWrap.append(el("h2", "", "确认行动"));
    head.append(titleWrap);
    const close = el("button", "icon-button close");
    close.type = "button";
    close.setAttribute("aria-label", "关闭");
    close.addEventListener("click", () => dialog.close());
    head.append(close);
    form.append(head);
    form.append(el("p", "hint", ""));
    form.id = "action-dialog-form";
    form.dataset.fieldFor = "";
    const fields = el("div", "action-dialog-fields");
    fields.id = "action-dialog-fields";
    form.append(fields);
    const actions = el("div", "form-row");
    const cancel = el("button", "button ghost", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", () => dialog.close());
    const confirm = el("button", "button primary", "开始行动");
    confirm.type = "button";
    confirm.id = "action-dialog-confirm";
    actions.append(cancel, confirm);
    form.append(actions);
    dialog.append(form);
    document.body.append(dialog);
    return dialog;
  }

  function openActionDialog(action) {
    const dialog = ensureActionDialog();
    const form = $("action-dialog-form");
    const fields = $("action-dialog-fields");
    const title = dialog.querySelector("h2");
    const hint = dialog.querySelector("p.hint");
    title.textContent = action.choice || action.name || "确认行动";
    hint.textContent = action.summary || action.purpose || "";
    fields.replaceChildren();

    if (action.target_required) {
      const label = el("label", "", "对谁说");
      const select = document.createElement("select");
      select.id = "action-target";
      for (const option of action.target_options || []) {
        select.append(new Option(option.name || option.id, option.id || option.name || ""));
      }
      label.append(select);
      fields.append(label);
    }

    for (const spec of Array.isArray(action.parameters) ? action.parameters : []) {
      const match = String(spec).match(/^(\w+)\s*\(([^)]+)\):\s*(.*)$/);
      if (!match) continue;
      const [, name, kind, description] = match;
      if (action.parameter_options?.[name]) {
        const label = el("label", "", name === "source_id" ? "研究来源" : name);
        const select = document.createElement("select");
        select.dataset.param = name;
        for (const option of action.parameter_options[name]) {
          select.append(new Option(option.name || option.value || "选项", option.name || option.value || ""));
        }
        label.append(select);
        fields.append(label);
        continue;
      }
      if (name === "message" || name === "topic") {
        const label = el("label", "", name === "message" ? "消息" : "话题");
        const input = name === "message" ? document.createElement("textarea") : document.createElement("input");
        input.dataset.param = name;
        input.required = kind.includes("required");
        input.rows = 3;
        if (name === "topic") input.value = "公社近况";
        input.placeholder = description || "";
        label.append(input);
        fields.append(label);
      }
    }

    if (!fields.children.length) {
      fields.append(el("p", "hint", "该行动不需要额外参数。"));
    }
    const confirm = $("action-dialog-confirm");
    confirm.onclick = () => {
      const targetId = $("action-target")?.value || "";
      if (action.target_required && !targetId) {
        toast("请选择一个目标。", "error");
        return;
      }
      const parameters = {};
      for (const node of fields.querySelectorAll("[data-param]")) {
        const value = String(node.value || "").trim();
        if (value) parameters[node.dataset.param] = value;
      }
      if (action.target_required && !parameters.message) {
        toast("请填写一条消息。", "error");
        return;
      }
      dialog.close();
      dialog.dataset.choice = "confirmed";
      dialog._resolve?.({
        target: action.target_required ? { name: targetId } : {},
        parameters,
      });
    };
    dialog.dataset.choice = "";
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "open");
    return new Promise((resolve) => {
      dialog._resolve = resolve;
      const observer = new MutationObserver(() => {
        if (!dialog.open) {
          observer.disconnect();
          if (dialog.dataset.choice !== "confirmed") resolve(null);
          dialog.dataset.choice = "";
        }
      });
      observer.observe(dialog, { attributes: true, attributeFilter: ["open"] });
    });
  }

  /* ---------- Tabs ---------- */

  function switchTab(tab) {
    for (const button of $("right-tabs").querySelectorAll("[data-tab]")) {
      button.classList.toggle("active", button.dataset.tab === tab);
    }
    for (const body of document.querySelectorAll(".tab-body")) {
      body.classList.toggle("active", body.id === `tab-${tab}`);
    }
    if (tab === "talk") loadDialogue();
    if (tab === "trade") loadTrades();
  }

  /* ---------- Wiring ---------- */

  function wireUi() {
    $("hud-refresh").innerHTML = icon("refresh");
    $("hud-settings").innerHTML = icon("settings");
    $("settings-close").innerHTML = "";

    $("hud-refresh").addEventListener("click", () => refreshAll());
    $("hud-settings").addEventListener("click", openSettings);
    $("settings-close").addEventListener("click", closeSettings);
    for (const scrim of document.querySelectorAll("[data-close-drawer]")) scrim.addEventListener("click", closeSettings);
    $("settings-provider").addEventListener("change", () => {
      const value = String($("settings-provider").value || "");
      try { localStorage.setItem("sim2.game.provider", value || "default"); } catch (error) { /* ignore */ }
      if (state.playerId) toast(`同步推进将使用模型：${value || "默认"}`, "ok");
      renderTask();
    });

    $("entry-submit").addEventListener("click", (event) => submitEntry(event.currentTarget));
    $("entry-id").addEventListener("keydown", (event) => {
      if (event.key === "Enter") submitEntry($("entry-submit"));
    });
    $("entry-name").addEventListener("keydown", (event) => {
      if (event.key === "Enter") submitEntry($("entry-submit"));
    });

    $("task-advance").addEventListener("click", (event) => {
      event.currentTarget.disabled = true;
      advanceTask("继续：").finally(() => renderTask());
    });
    $("task-wait").addEventListener("click", (event) => waitOneMinute(event.currentTarget));
    $("player-leave").addEventListener("click", () => showEntry("已退出角色，可以输入另一个玩家 ID。"));

    $("talk-form").addEventListener("submit", (event) => {
      event.preventDefault();
      sendDialogue(event.currentTarget);
    });
    $("trade-form").addEventListener("submit", (event) => {
      event.preventDefault();
      sendTrade(event.currentTarget);
    });

    for (const button of $("right-tabs").querySelectorAll("[data-tab]")) {
      button.addEventListener("click", () => switchTab(button.dataset.tab));
    }
    $("open-map").addEventListener("click", openMap);
    $("feed-more").addEventListener("click", () => {
      state.feedLimit += 28;
      renderFeed();
    });

    $("instance-activate").addEventListener("click", activateInstance);
    $("instance-refresh").addEventListener("click", loadInstances);
    $("operator-unlock").addEventListener("click", unlockOperator);
    $("runtime-start").addEventListener("click", () => runtimeCommand("/runtime/start", {
      provider: selectedProvider() || undefined,
      max_world_minutes: 1440,
      max_llm_calls: 120,
    }));
    $("runtime-pause").addEventListener("click", () => runtimeCommand("/runtime/pause", { reason: "game-settings" }));
    $("runtime-stop").addEventListener("click", () => runtimeCommand("/runtime/stop", { reason: "game-settings" }));
    $("runtime-step").addEventListener("click", () => runtimeCommand("/runtime/step", {}));

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("settings-drawer").classList.contains("hidden")) closeSettings();
    });
  }

  async function boot() {
    if (state.booted) return;
    state.booted = true;
    try {
      state.operatorToken = sessionStorage.getItem(OPERATOR_KEY) || null;
    } catch (error) {
      state.operatorToken = null;
    }
    wireUi();
    await loadEntryContext();
    const restored = await restorePlayer();
    if (!restored) {
      $("entry-screen").classList.remove("hidden");
      $("game").classList.add("hidden");
    }
    state.pollTimer = window.setInterval(() => {
      if (!state.playerId || document.hidden || state.advancing) return;
      loadView().catch(() => {});
      loadRuntime();
      loadFeed();
    }, 15000);
    window.setInterval(() => {
      if (!state.playerId || document.hidden || state.advancing) return;
      loadTrades();
      loadDialogue();
    }, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
