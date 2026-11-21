(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const PLAYER_KEY = "sim2.game.player";
  const OPERATOR_KEY = "sim2.game.operator";
  const ENDING_KEY = "sim2.game.ending";
  const SOUND_KEY = "sim2.game.sound";

  const state = {
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
    booted: false,
  };

  const ICONS = {
    refresh: '<path d="M20 11a8 8 0 1 0-2.4 5.7"/><path d="M20 4v7h-7"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9S14.5 18.3 12 21c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3Z"/>',
    pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    sound: '<path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    soundOff: '<path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M16 9l5 6M21 9l-5 6"/>',
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

  function sceneSoundProfile() {
    const theme = $("scene-art")?.dataset.scene || "harbor";
    const profiles = {
      harbor: { filter: 520, drone: 55, volume: 0.3 },
      quay: { filter: 620, drone: 58, volume: 0.3 },
      gatehouse: { filter: 430, drone: 46, volume: 0.34 },
      workshop: { filter: 340, drone: 62, volume: 0.24 },
      observatory: { filter: 760, drone: 44, volume: 0.2 },
      marsh: { filter: 560, drone: 50, volume: 0.26 },
      lantern: { filter: 300, drone: 65, volume: 0.22 },
    };
    return profiles[theme] || profiles.harbor;
  }

  function renderSoundButton() {
    const button = $("hud-sound");
    if (!button) return;
    button.innerHTML = icon(state.soundOn ? "sound" : "soundOff");
    button.classList.toggle("active", state.soundOn);
    button.setAttribute("aria-pressed", state.soundOn ? "true" : "false");
    button.title = state.soundOn ? "关闭环境音" : "开启环境音";
  }

  function startAmbient() {
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return false;
    if (!state.audio) {
      try {
        const ctx = new AudioCtor();
        const master = ctx.createGain();
        master.gain.value = 0;
        master.connect(ctx.destination);
        const buffer = ctx.createBuffer(1, ctx.sampleRate * 4, ctx.sampleRate);
        const data = buffer.getChannelData(0);
        let last = 0;
        for (let index = 0; index < data.length; index += 1) {
          const white = Math.random() * 2 - 1;
          last = (last + 0.02 * white) / 1.02;
          data[index] = last * 3.2;
        }
        const noise = ctx.createBufferSource();
        noise.buffer = buffer;
        noise.loop = true;
        const filter = ctx.createBiquadFilter();
        filter.type = "lowpass";
        filter.Q.value = 0.8;
        const noiseGain = ctx.createGain();
        noiseGain.gain.value = 0.5;
        const drone = ctx.createOscillator();
        drone.type = "sine";
        const droneGain = ctx.createGain();
        droneGain.gain.value = 0.035;
        const lfo = ctx.createOscillator();
        lfo.frequency.value = 0.06;
        const lfoGain = ctx.createGain();
        lfoGain.gain.value = 160;
        lfo.connect(lfoGain);
        lfoGain.connect(filter.frequency);
        noise.connect(filter);
        filter.connect(noiseGain);
        noiseGain.connect(master);
        drone.connect(droneGain);
        droneGain.connect(master);
        noise.start();
        drone.start();
        lfo.start();
        state.audio = { ctx, master, filter, drone };
      } catch (error) {
        state.audio = null;
        return false;
      }
    }
    const audio = state.audio;
    if (audio.ctx.state === "suspended") audio.ctx.resume();
    applyAmbientProfile(audio);
    return true;
  }

  function applyAmbientProfile(audio = state.audio) {
    if (!audio) return;
    const profile = sceneSoundProfile();
    const now = audio.ctx.currentTime;
    audio.filter.frequency.setTargetAtTime(profile.filter, now, 0.8);
    audio.drone.frequency.setTargetAtTime(profile.drone, now, 0.8);
    audio.master.gain.setTargetAtTime(profile.volume, now, 1.2);
  }

  function stopAmbient() {
    if (!state.audio) return;
    const now = state.audio.ctx.currentTime;
    state.audio.master.gain.setTargetAtTime(0, now, 0.35);
  }

  function toggleSound() {
    state.soundOn = !state.soundOn;
    storageSet(SOUND_KEY, state.soundOn);
    renderSoundButton();
    if (!state.soundOn) {
      stopAmbient();
      toast("环境音已关闭", "ok");
      return;
    }
    if (!startAmbient()) {
      state.soundOn = false;
      storageSet(SOUND_KEY, false);
      renderSoundButton();
      toast("当前浏览器不支持环境音", "error");
      return;
    }
    toast("环境音已开启", "ok");
  }

  function armSoundOnGesture() {
    if (!state.soundOn || state.audio) return;
    const resume = () => {
      window.removeEventListener("pointerdown", resume);
      if (state.soundOn) startAmbient();
    };
    window.addEventListener("pointerdown", resume, { once: true });
  }

  async function unlockFromOwnerLink() {
    const match = String(location.hash || "").match(/^#owner=([^&]+)/);
    if (!match) return;
    const token = decodeURIComponent(match[1]);
    try {
      history.replaceState(null, "", `${location.pathname}${location.search}`);
    } catch (error) {
      /* history can be restricted; the token is still consumed once */
    }
    try {
      const result = await write("/session", { role: "operator", operator_token: token });
      if (result.status === "ok" && result.session_token) {
        state.operatorToken = result.session_token;
        try { sessionStorage.setItem(OPERATOR_KEY, state.operatorToken); } catch (error) { /* ignore */ }
        return;
      }
      // A direct master token is also accepted by the local session boundary.
      state.operatorToken = token;
      try { sessionStorage.setItem(OPERATOR_KEY, token); } catch (error) { /* ignore */ }
    } catch (error) {
      state.operatorToken = token;
      try { sessionStorage.setItem(OPERATOR_KEY, token); } catch (error) { /* ignore */ }
    }
  }


  function tokenFor(path) {
    if (path.startsWith("/runtime/") || path.startsWith("/instances") || path.startsWith("/authoring/") || path.startsWith("/snapshots")) {
      return state.operatorToken || state.playerToken;
    }
    if (path.startsWith("/player/") || path.startsWith("/dialogue") || path.startsWith("/trade/")) {
      return state.playerToken || state.operatorToken;
    }
    return state.operatorToken || state.playerToken;
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
    const value = String(itemId || "");
    const labels = state.view?.item_labels || {};
    if (labels[value]) return labels[value];
    const normalized = value.replace(/_/g, " ").toLowerCase();
    const match = Object.keys(labels).find((key) => key.replace(/_/g, " ").toLowerCase() === normalized);
    return match ? labels[match] : humanizeKey(value);
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

  function translateNarrative(text) {
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
    const resultWords = {
      inspection_completed: "检查完成",
      resource_collected: "采集完成",
      recipe_completed: "制作完成",
      route_traversed: "抵达目的地",
      trade_completed: "交易完成",
      research_completed: "研究完成",
      research_progressed: "研究推进",
      information_recorded: "情报已记录",
      rationing_decided: "配给已调整",
      repair_priority_decided: "抢修已完成",
      marsh_rest_decided: "休养已安排",
      decision_failed: "决策中断",
    };
    for (const [code, label] of Object.entries(resultWords)) {
      value = value.replace(new RegExp(`\\b${code}\\b`, "gi"), label);
    }
    value = value.replace(/\bworld property changed\b/gi, "世界状态发生变化");
    value = value.replace(/\bcapability evidence\b/gi, "能力证据");
    value = value.replace(/A direct exchange was recorded as unverified testimony\.?/gi, "一次直接交谈被记录为未经核实的证词。");
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

  /* ---------- Entry / world hub ---------- */

  function hashString(value) {
    let hash = 2166136261;
    for (const char of String(value || "sim2")) {
      hash ^= char.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return Math.abs(hash);
  }

  function worldPalette(worldId) {
    const palettes = [
      { skyA: "#0b2c31", skyB: "#061416", a: "#2b6d6a", b: "#153c3c", c: "#3f8f86", sun: "#efb26f" },
      { skyA: "#2b2038", skyB: "#0d0a18", a: "#6a4d86", b: "#352547", c: "#8b6aa8", sun: "#f0c27a" },
      { skyA: "#1c3325", skyB: "#08140d", a: "#3f6f4b", b: "#1b3a25", c: "#5f9367", sun: "#d9d38a" },
      { skyA: "#37251c", skyB: "#140b08", a: "#7d5037", b: "#42291b", c: "#a06c49", sun: "#f2b277" },
    ];
    const palette = palettes[hashString(worldId) % palettes.length];
    return {
      ...palette,
      hue: hashString(worldId) % 360,
    };
  }

  function renderEntryCover() {
    const cover = $("entry-cover");
    if (!cover) return;
    const worldId = state.world?.id || state.world?.world_id || state.world?.title || "sim2";
    const palette = worldPalette(worldId);
    const seed = hashString(worldId);
    const stars = Array.from({ length: 18 }, (_, index) => {
      const x = ((seed >> (index % 12)) + index * 137) % 1200;
      const y = ((seed >> ((index + 3) % 10)) + index * 53) % 84;
      const r = index % 4 === 0 ? 1.5 : 0.8;
      return `<circle cx="${x}" cy="${y}" r="${r}" fill="rgba(255,255,255,${0.18 + (index % 5) * 0.08})"/>`;
    }).join("");
    const ridges = [
      { y: 92, amp: 14, phase: seed % 19, color: palette.c, opacity: 0.32 },
      { y: 108, amp: 18, phase: (seed >> 3) % 23, color: palette.a, opacity: 0.5 },
      { y: 126, amp: 11, phase: (seed >> 6) % 17, color: palette.b, opacity: 0.85 },
    ].map((ridge) => {
      const points = Array.from({ length: 9 }, (_, index) => {
        const x = index * 150;
        const y = ridge.y - Math.sin((index + ridge.phase) * 1.37) * ridge.amp - ((seed >> (index % 8)) % 7);
        return `${x},${Math.max(54, Math.min(136, y)).toFixed(1)}`;
      });
      return `<path d="M0 140 L${points[0]} L${points.slice(1).join(" L")} L1200 140 Z" fill="${ridge.color}" opacity="${ridge.opacity}"/>`;
    }).join("");
    cover.innerHTML = `
      <svg viewBox="0 0 1200 140" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="entry-sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${palette.skyA}"/>
            <stop offset="100%" stop-color="${palette.skyB}"/>
          </linearGradient>
        </defs>
        <rect width="1200" height="140" fill="url(#entry-sky)"/>
        ${stars}
        <circle cx="${180 + (seed % 720)}" cy="${42 + (seed % 18)}" r="17" fill="${palette.sun}" opacity="0.72"/>
        <circle cx="${180 + (seed % 720)}" cy="${42 + (seed % 18)}" r="30" fill="${palette.sun}" opacity="0.08"/>
        ${ridges}
      </svg>`;
  }

  function paintWorldCard(card, worldId) {
    const palette = worldPalette(worldId);
    card.style.setProperty("--cover-a", palette.a);
    card.style.setProperty("--cover-b", palette.b);
  }

  function artStars(seed, count = 30, maxY = 190) {
    return Array.from({ length: count }, (_, index) => {
      const x = ((seed >> (index % 11)) + index * 151) % 1200;
      const y = ((seed >> ((index + 2) % 9)) + index * 41) % maxY;
      const r = index % 5 === 0 ? 1.6 : 0.85;
      const delay = ((seed + index * 37) % 80) / 10;
      return `<circle class="art-star" style="animation-delay:${delay}s" cx="${x}" cy="${y}" r="${r}" fill="rgba(255,255,255,${0.16 + (index % 5) * 0.08})"/>`;
    }).join("");
  }

  function artReeds(seed, baseY, color, count = 22) {
    return Array.from({ length: count }, (_, index) => {
      const x = 30 + index * 55 + ((seed >> (index % 7)) % 26);
      const height = 42 + ((seed >> (index % 9)) % 74);
      const lean = index % 2 ? 8 : -7;
      return `<path d="M${x} ${baseY} Q${x + lean * 0.4} ${baseY - height * 0.58} ${x + lean} ${baseY - height}" stroke="${color}" stroke-width="${index % 4 === 0 ? 2.2 : 1.2}" fill="none" opacity="${0.32 + (index % 3) * 0.12}"/>`;
    }).join("");
  }

  function renderSceneWorldCover(theme = "harbor") {
    const root = $("scene-world-cover");
    if (!root) return;
    const worldId = state.world?.world_id || state.world?.title || "sim2";
    const cacheKey = `${worldId}|${theme}`;
    if (root.dataset.world === cacheKey) return;
    root.dataset.world = cacheKey;
    const p = worldPalette(worldId);
    const seed = hashString(worldId);
    const moonX = 860 + (seed % 140);
    const moonY = 86 + (seed % 38);
    const stars = artStars(seed);
    const mist = `<ellipse class="art-mist" cx="${260 + (seed % 500)}" cy="250" rx="360" ry="26" fill="url(#scene-mist)"/>`;
    const rain = `<g class="art-rain">${Array.from({ length: 18 }, (_, index) => {
      const x = (index * 71 + (seed % 40)) % 1200;
      return `<line x1="${x}" y1="${(index * 23) % 180}" x2="${x - 8}" y2="${((index * 23) % 180) + 58}" stroke="rgba(190,220,232,0.22)" stroke-width="1"/>`;
    }).join("")}</g>`;
    const water = `<path d="M0 250 C160 242 280 256 420 250 S760 240 900 250 S1100 256 1200 246 L1200 420 L0 420Z" fill="url(#scene-water)"/>`;
    const seaGlints = Array.from({ length: 12 }, (_, index) => {
      const y = 270 + index * 11;
      const width = 34 + (index % 4) * 24;
      const x = 80 + ((seed >> (index % 8)) + index * 97) % 980;
      return `<rect x="${x}" y="${y}" width="${width}" height="1.2" rx="0.6" fill="${p.sun}" opacity="${0.05 + (index % 4) * 0.025}"/>`;
    }).join("");
    let scene = "";
    if (theme === "gatehouse") {
      scene = `
        <path d="M0 0 L1200 0 L1200 130 C1020 168 900 96 760 126 C620 156 500 122 380 138 C250 154 120 128 0 150Z" fill="${p.skyB}" opacity=".78"/>
        ${stars}
        <circle cx="${moonX}" cy="${moonY}" r="24" fill="${p.sun}" opacity=".5"/>
        <circle cx="${moonX}" cy="${moonY}" r="58" fill="${p.sun}" opacity=".07"/>
        ${water}
        <path d="M0 262 C180 250 320 266 470 258 S780 246 940 260 S1110 264 1200 254" stroke="${p.c}" stroke-opacity=".28" stroke-width="2" fill="none"/>
        ${seaGlints}
        <g fill="#071017" opacity=".96">
          <path d="M250 420 L250 128 L330 84 L420 128 L420 420Z"/>
          <path d="M780 420 L780 116 L870 70 L970 116 L970 420Z"/>
          <rect x="285" y="150" width="100" height="240" fill="${p.b}" opacity=".55"/>
          <rect x="820" y="142" width="110" height="248" fill="${p.b}" opacity=".55"/>
          <path d="M420 196 L780 184 L780 228 L420 238Z" fill="#09141a"/>
          <path d="M420 238 L780 228 L780 420 L420 420Z" fill="#041014" opacity=".55"/>
        </g>
        <g stroke="${p.sun}" stroke-opacity=".35" fill="none">
          <path d="M470 196 L470 420 M505 194 L505 420 M540 192 L540 420 M575 190 L575 420 M610 188 L610 420 M645 186 L645 420 M680 184 L680 420 M715 184 L715 420" stroke-width="2"/>
          <circle cx="600" cy="300" r="58" stroke-width="3"/>
          <circle cx="600" cy="300" r="18" stroke-width="2"/>
          <path d="M600 242 L600 358 M542 300 L658 300 M559 259 L641 341 M641 259 L559 341" stroke-width="1.4"/>
        </g>
        <g class="art-beam" opacity=".55">
          <path d="M952 158 L1200 112 L1200 196 Z" fill="${p.sun}" opacity=".18"/>
          <path d="M952 158 L1200 132 L1200 172 Z" fill="${p.sun}" opacity=".14"/>
        </g>
        <g>
          <rect x="930" y="150" width="24" height="86" rx="5" fill="#0a1a20" stroke="${p.c}" stroke-opacity=".5"/>
          <circle cx="942" cy="150" r="15" fill="${p.sun}" opacity=".85"/>
          <circle cx="942" cy="150" r="28" fill="${p.sun}" opacity=".12"/>
        </g>
        ${rain}
        ${mist}
        <path d="M0 380 C120 368 220 386 320 378 S520 366 620 380 S840 386 940 372 S1100 366 1200 380 L1200 420 L0 420Z" fill="${p.skyB}" opacity=".6"/>`;
    } else if (theme === "workshop") {
      scene = `
        <rect width="1200" height="420" fill="url(#scene-interior)"/>
        <g stroke="${p.b}" stroke-opacity=".6" fill="none">
          <path d="M0 56 L1200 18" stroke-width="10"/>
          <path d="M0 96 L1200 58" stroke-width="6"/>
          <path d="M120 0 L200 100 M380 0 L440 92 M660 0 L700 84 M920 0 L960 76" stroke-width="4"/>
        </g>
        <rect x="828" y="54" width="250" height="176" rx="6" fill="#07131b" stroke="${p.c}" stroke-opacity=".42" stroke-width="2"/>
        <path d="M953 54 L953 230 M828 142 L1078 142" stroke="${p.b}" stroke-width="5" opacity=".8"/>
        <g class="art-rain" clip-path="url(#scene-window-clip)">
          ${Array.from({ length: 10 }, (_, index) => `<line x1="${850 + index * 23}" y1="${64 + (index % 3) * 8}" x2="${844 + index * 23}" y2="${130 + (index % 4) * 22}" stroke="rgba(190,220,232,0.25)" stroke-width="1"/>`).join("")}
        </g>
        <g opacity=".9">
          <path d="M0 332 L1200 318 L1200 420 L0 420Z" fill="#0a1512"/>
          <path d="M0 332 L1200 318" stroke="${p.sun}" stroke-opacity=".18" stroke-width="2"/>
          <rect x="90" y="260" width="340" height="22" rx="4" fill="#1a2018"/>
          <rect x="118" y="282" width="18" height="86" fill="#111812"/>
          <rect x="382" y="282" width="18" height="86" fill="#111812"/>
          <g fill="#263027">
            <rect x="150" y="236" width="36" height="24" rx="3"/>
            <rect x="198" y="228" width="18" height="32" rx="2"/>
            <rect x="230" y="240" width="46" height="20" rx="3"/>
          </g>
        </g>
        <g stroke="${p.c}" stroke-opacity=".55" fill="none">
          <path d="M760 0 L760 330 M760 330 L900 330" stroke-width="9"/>
          <path d="M790 330 L790 388 M860 330 L860 388" stroke-width="5"/>
          <rect x="740" y="318" width="176" height="70" rx="10" fill="#12201e" stroke-width="2"/>
        </g>
        <g fill="${p.sun}" opacity=".8">
          <circle cx="708" cy="160" r="9"/>
          <circle cx="650" cy="188" r="6"/>
          <circle cx="790" cy="150" r="7"/>
        </g>
        <circle cx="708" cy="160" r="50" fill="${p.sun}" opacity=".08"/>
        <g stroke="${p.c}" stroke-opacity=".4" fill="none">
          <path d="M520 88 L520 220 M540 100 L540 214 M560 108 L560 208" stroke-width="2"/>
          <circle cx="540" cy="224" r="14"/>
          <path d="M610 92 L610 196" stroke-width="3"/>
          <path d="M596 196 L624 196 L620 224 L600 224Z" fill="#0a1718"/>
        </g>
        ${mist}`;
    } else if (theme === "observatory") {
      scene = `
        ${stars}
        <circle cx="${moonX}" cy="${moonY}" r="34" fill="${p.sun}" opacity=".55"/>
        <circle cx="${moonX}" cy="${moonY}" r="82" fill="${p.sun}" opacity=".06"/>
        <ellipse cx="700" cy="120" rx="260" ry="110" fill="${p.c}" opacity=".08"/>
        <path d="M0 280 C160 268 280 284 420 278 S760 266 900 280 S1100 286 1200 274 L1200 420 L0 420Z" fill="url(#scene-water)" opacity=".82"/>
        <path d="M0 292 C180 280 320 296 460 288 S780 278 940 292 S1110 296 1200 286" stroke="${p.c}" stroke-opacity=".24" stroke-width="2" fill="none"/>
        <path d="M0 420 L0 338 C120 314 210 330 300 318 C390 306 470 328 560 332 C660 336 760 314 860 322 C980 332 1080 318 1200 338 L1200 420Z" fill="#071017"/>
        <g>
          <path d="M790 330 L810 150 L900 104 L990 150 L1010 330Z" fill="#0a1820" stroke="${p.c}" stroke-opacity=".3"/>
          <path d="M810 150 L990 150 L960 108 L840 108Z" fill="#0d2028"/>
          <circle cx="900" cy="128" r="30" fill="#0a141a" stroke="${p.c}" stroke-opacity=".55" stroke-width="2"/>
          <circle cx="900" cy="128" r="17" fill="${p.sun}" opacity=".8"/>
          <circle cx="900" cy="128" r="50" fill="${p.sun}" opacity=".1"/>
          <path d="M900 128 L1200 88 L1200 162 Z" class="art-beam" fill="${p.sun}" opacity=".11"/>
          <path d="M830 330 L830 180 M970 330 L970 180" stroke="${p.c}" stroke-opacity=".24"/>
          <path d="M848 330 L848 210 M952 330 L952 210" stroke="${p.c}" stroke-opacity=".18"/>
        </g>
        <g fill="${p.c}" opacity=".5">
          <path d="M700 380 L720 350 L738 380Z"/>
          <path d="M740 388 L758 362 L776 388Z"/>
        </g>
        ${mist}`;
    } else if (theme === "marsh") {
      scene = `
        <circle cx="${760 + (seed % 160)}" cy="${132 + (seed % 30)}" r="34" fill="${p.sun}" opacity=".38"/>
        <circle cx="${760 + (seed % 160)}" cy="${132 + (seed % 30)}" r="82" fill="${p.sun}" opacity=".06"/>
        <path d="M0 238 C140 224 260 244 400 232 S700 218 860 236 S1080 242 1200 226 L1200 420 L0 420Z" fill="url(#scene-marsh-water)"/>
        <path d="M0 252 C180 240 320 258 470 248 S780 236 940 252 S1110 256 1200 246" stroke="${p.c}" stroke-opacity=".26" stroke-width="2" fill="none"/>
        <path d="M0 300 C160 286 280 308 430 298 S760 284 920 302 S1110 306 1200 294 L1200 420 L0 420Z" fill="#0a1a15" opacity=".7"/>
        <path d="M260 420 C300 370 340 350 390 330 C450 306 500 336 540 360 C580 386 620 372 680 340 C740 308 800 342 850 380 L900 420Z" fill="#0c211a"/>
        <path d="M80 420 C120 388 160 372 210 360 C250 350 280 370 300 390 L320 420Z" fill="#0d241c"/>
        ${artReeds(seed + 11, 400, p.c, 24)}
        ${artReeds(seed + 29, 410, p.a, 18)}
        <g fill="#071410" opacity=".85">
          <path d="M930 300 L940 260 L950 300Z"/>
          <path d="M960 304 L970 268 L980 304Z"/>
          <path d="M910 306 L918 276 L926 306Z"/>
        </g>
        <g stroke="rgba(220,235,225,0.35)" stroke-width="1.2" fill="none" class="art-birds">
          <path d="M520 160 Q530 152 540 160 Q550 152 560 160"/>
          <path d="M600 136 Q608 129 616 136 Q624 129 632 136"/>
        </g>
        ${mist}`;
    } else if (theme === "lantern") {
      scene = `
        <rect width="1200" height="420" fill="url(#scene-interior-warm)"/>
        <g stroke="#3a2418" stroke-opacity=".9" fill="none">
          <path d="M0 52 L1200 20" stroke-width="12"/>
          <path d="M0 86 L1200 54" stroke-width="7"/>
          <path d="M180 0 L260 104 M520 0 L560 96 M900 0 L940 88" stroke-width="5"/>
        </g>
        <g fill="#2a1a12" opacity=".7">
          <rect x="60" y="120" width="300" height="210" rx="8"/>
          <rect x="60" y="120" width="300" height="14" fill="#3b2619"/>
          <rect x="860" y="110" width="280" height="220" rx="8"/>
          <rect x="860" y="110" width="280" height="14" fill="#3b2619"/>
        </g>
        <rect x="870" y="152" width="200" height="120" rx="4" fill="#07131b" stroke="#4a3020" stroke-width="4"/>
        <g class="art-rain">
          ${Array.from({ length: 9 }, (_, index) => `<line x1="${890 + index * 20}" y1="${162 + (index % 3) * 6}" x2="${884 + index * 20}" y2="${220 + (index % 4) * 14}" stroke="rgba(180,210,225,0.22)" stroke-width="1"/>`).join("")}
        </g>
        <g class="art-ember">
          <circle cx="250" cy="300" r="3" fill="${p.sun}" opacity=".8"/>
          <circle cx="285" cy="274" r="2" fill="${p.sun}" opacity=".65"/>
          <circle cx="220" cy="250" r="1.6" fill="${p.sun}" opacity=".6"/>
        </g>
        <g>
          <path d="M180 420 L180 356 C220 334 300 334 340 356 L340 420Z" fill="#1b120c"/>
          <ellipse cx="260" cy="360" rx="54" ry="24" fill="${p.sun}" opacity=".18"/>
          <path d="M235 360 C240 334 252 326 260 314 C270 332 284 340 282 360 C278 378 248 382 235 360Z" fill="${p.sun}" opacity=".75"/>
          <path d="M250 362 C254 348 260 344 264 338 C270 350 272 356 268 366 C264 376 252 374 250 362Z" fill="#fff1cf" opacity=".7"/>
        </g>
        <g>
          <path d="M520 0 L520 58" stroke="#2a1a12" stroke-width="3"/>
          <rect x="500" y="58" width="40" height="54" rx="8" fill="${p.sun}" opacity=".55"/>
          <rect x="506" y="64" width="28" height="42" rx="6" fill="#ffe5b8" opacity=".35"/>
          <path d="M700 0 L700 38" stroke="#2a1a12" stroke-width="3"/>
          <rect x="684" y="38" width="32" height="44" rx="7" fill="${p.sun}" opacity=".5"/>
        </g>
        <g opacity=".9">
          <path d="M380 420 L380 380 L760 380 L760 420Z" fill="#1a110b"/>
          <path d="M400 380 L400 352 L520 352 L520 380Z" fill="#2a1a12"/>
          <path d="M560 380 L560 342 L700 342 L700 380Z" fill="#241610"/>
        </g>
        ${mist}`;
    } else {
      const quayExtra = theme === "quay" ? `
        <g>
          <path d="M90 300 L90 250 L250 250 L250 300Z" fill="#1b1710"/>
          <path d="M80 250 L260 250 L240 226 L100 226Z" fill="${p.sun}" opacity=".18"/>
          <rect x="120" y="258" width="34" height="34" rx="3" fill="#2a2116"/>
          <rect x="164" y="264" width="28" height="28" rx="3" fill="#33271a"/>
          <path d="M250 300 L250 210 M250 220 L300 240 L250 260" stroke="#0b1414" stroke-width="3" fill="none"/>
          <circle cx="300" cy="244" r="5" fill="${p.sun}" opacity=".8"/>
        </g>` : `
        <g>
          <path d="M790 300 L850 300 L866 326 L774 326Z" fill="#0b1414"/>
          <path d="M820 300 L820 230" stroke="#0b1414" stroke-width="4"/>
          <path d="M820 232 L880 280 L820 280Z" fill="${p.c}" opacity=".22"/>
          <path d="M820 242 L776 280 L820 280Z" fill="${p.c}" opacity=".15"/>
        </g>`;
      scene = `
        ${stars}
        <circle cx="${moonX}" cy="${moonY}" r="30" fill="${p.sun}" opacity=".62"/>
        <circle cx="${moonX}" cy="${moonY}" r="72" fill="${p.sun}" opacity=".08"/>
        <path d="M0 246 C140 238 240 252 360 246 S620 236 760 248 S1020 254 1200 242 L1200 420 L0 420Z" fill="url(#scene-water)"/>
        <path d="M0 258 C160 248 300 264 440 256 S740 244 880 258 S1080 262 1200 252" stroke="${p.c}" stroke-opacity=".28" stroke-width="2" fill="none"/>
        ${seaGlints}
        <path d="M0 258 L110 226 L150 258Z" fill="${p.b}" opacity=".8"/>
        <path d="M1010 258 L1080 214 L1150 258Z" fill="${p.b}" opacity=".72"/>
        <path d="M940 258 L986 232 L1032 258Z" fill="${p.b}" opacity=".6"/>
        <g opacity=".95">
          <path d="M-20 348 L540 306 L640 420 L-20 420Z" fill="#071416"/>
          <path d="M-20 348 L540 306" stroke="${p.c}" stroke-opacity=".34" stroke-width="2"/>
          <path d="M40 348 L60 420 M160 340 L180 420 M280 332 L300 420 M400 322 L420 420 M520 308 L540 420" stroke="${p.b}" stroke-opacity=".65" stroke-width="3"/>
          <rect x="500" y="286" width="18" height="112" rx="4" fill="#0a1718"/>
          <rect x="626" y="272" width="18" height="126" rx="4" fill="#0a1718"/>
          <circle cx="509" cy="286" r="18" fill="${p.sun}" opacity=".1"/>
          <circle cx="509" cy="286" r="5" fill="${p.sun}" opacity=".8"/>
          <circle cx="635" cy="272" r="24" fill="${p.sun}" opacity=".08"/>
          <circle cx="635" cy="272" r="6" fill="${p.sun}" opacity=".7"/>
        </g>
        ${quayExtra}
        ${mist}
        <path d="M0 374 C120 364 220 382 320 374 S520 362 620 376 S840 382 940 368 S1100 362 1200 378 L1200 420 L0 420Z" fill="${p.skyB}" opacity=".62"/>
        <g stroke="rgba(210,235,230,0.15)" stroke-width="1" fill="none">
          <path d="M40 400 Q120 394 200 400 T360 400 T520 400 T680 400 T840 400 T1000 400 T1160 400"/>
          <path d="M80 410 Q160 404 240 410 T400 410 T560 410 T720 410 T880 410 T1040 410"/>
        </g>`;
    }
    root.innerHTML = `
      <svg viewBox="0 0 1200 420" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="scene-water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${p.c}" stop-opacity=".42"/>
            <stop offset="100%" stop-color="${p.skyB}" stop-opacity=".94"/>
          </linearGradient>
          <linearGradient id="scene-marsh-water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${p.a}" stop-opacity=".55"/>
            <stop offset="100%" stop-color="${p.skyB}" stop-opacity=".9"/>
          </linearGradient>
          <linearGradient id="scene-interior" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#241a12"/>
            <stop offset="62%" stop-color="#15100c"/>
            <stop offset="100%" stop-color="#090b08"/>
          </linearGradient>
          <linearGradient id="scene-interior-warm" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#2d1c12"/>
            <stop offset="58%" stop-color="#1a110b"/>
            <stop offset="100%" stop-color="#0b0907"/>
          </linearGradient>
          <radialGradient id="scene-mist" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="rgba(214,236,232,0.2)"/>
            <stop offset="100%" stop-color="rgba(214,236,232,0)"/>
          </radialGradient>
          <radialGradient id="scene-vignette" cx="50%" cy="48%" r="72%">
            <stop offset="54%" stop-color="#03100f" stop-opacity="0"/>
            <stop offset="100%" stop-color="#03100f" stop-opacity=".5"/>
          </radialGradient>
          <clipPath id="scene-window-clip"><rect x="828" y="54" width="250" height="176" rx="6"/></clipPath>
        </defs>
        ${scene}
        <rect width="1200" height="420" fill="url(#scene-vignette)"/>
      </svg>`;
  }

  function worldIsOpen() {
    return Boolean(state.activeDb) && state.world?.status !== "no_world";
  }

  function renderEntry() {
    const world = state.world || {};
    const entry = state.entry || {};
    const open = worldIsOpen();
    const players = Array.isArray(entry.players) ? entry.players : [];
    const hasPlayer = players.length > 0;
    const noWorld = !open;
    renderEntryCover();
    $("entry-title").textContent = noWorld ? "开始一个新世界" : (world.title || "世界");
    document.title = noWorld ? "sim2 · 世界大厅" : `${world.title || "世界"} · sim2`;
    $("entry-premise").textContent = noWorld
      ? "还没有可进入的世界。选择一个模板创建第一个实例，或者创建后从存档列表进入。"
      : (world.premise || "读取世界入口…");
    $("entry-genre").textContent = noWorld ? "世界大厅" : (world.genre || "世界模拟");
    $("entry-season").textContent = world.season || "—";
    $("entry-instance").textContent = noWorld ? "未创建" : (world.instance_id || "当前实例");
    $("hud-world-title").textContent = noWorld ? "世界大厅" : (world.title || "世界");
    $("entry-no-world").classList.toggle("hidden", !noWorld);
    $("entry-play").classList.toggle("hidden", noWorld);
    $("entry-continue").classList.toggle("hidden", noWorld || !hasPlayer);
    $("entry-new-character").classList.toggle("hidden", noWorld || hasPlayer);
    $("entry-origin-section").classList.toggle("hidden", noWorld || hasPlayer);
    $("entry-return").classList.toggle("hidden", !state.playerId);

    renderEntryInstances();
    renderContinue(players);
    if (!noWorld) renderOrigins(entry);
    renderWorldTemplates();

    const startMinute = Number(world.world_clock?.start_minute || 0);
    const totalMinute = Number(world.world_minute || 0) + startMinute;
    const clock = formatClock({ minute_of_day: totalMinute % 1440, day: Math.floor(totalMinute / 1440) + 1, day_phase: "" });
    $("entry-world-state").textContent = noWorld
      ? "创建后会自动打开，并从这里继续。"
      : `世界时间 ${clock.day} ${clock.time}`;
  }

  function renderEntryInstances() {
    const list = $("entry-instance-list");
    if (!list) return;
    list.replaceChildren();
    if (!state.instances.length) {
      list.append(el("p", "entry-empty", "还没有世界存档。"));
      return;
    }
    for (const item of state.instances) {
      const card = el("article", `instance-card${item.active ? " active" : ""}`);
      card.append(el("strong", "", item.world_id || item.instance_id || "世界"));
      const day = Math.floor(Number(item.world_minute || 0) / 1440) + 1;
      card.append(el("small", "", `${item.instance_id || "实例"} · 第 ${day} 天 · ${item.runtime_status || "未知状态"}`));
      const button = el("button", "button ghost", item.active ? (state.playerId ? "返回游戏" : "当前世界") : "切换");
      button.type = "button";
      if (item.active) {
        button.disabled = !state.playerId;
        button.addEventListener("click", () => returnToGame());
      } else {
        button.addEventListener("click", () => activateWorldInstance(item.instance_id, button));
      }
      card.append(button);
      list.append(card);
    }
  }

  function renderContinue(players) {
    const root = $("entry-continue");
    if (!root) return;
    root.replaceChildren();
    if (!players.length) return;
    root.append(el("span", "eyebrow", "继续游戏"));
    for (const player of players) {
      const card = el("div", "continue-card-inner");
      card.append(el("strong", "", player.name || player.id));
      const meta = [player.location ? `位于 ${player.location}` : "", `角色 ID ${player.id}`].filter(Boolean).join(" · ");
      card.append(el("p", "", meta));
      const button = el("button", "button primary wide", `继续 ${player.name || player.id}`);
      button.type = "button";
      button.addEventListener("click", () => submitEntry(button, player.id));
      card.append(button);
      root.append(card);
    }
    const hint = el("p", "hint", "这个实例已经有一个玩家角色。进入后会恢复它的物品、能力和未完成的行动。");
    root.append(hint);
  }

  function renderOrigins(entry) {
    const origins = Array.isArray(entry.origins) ? entry.origins : [];
    const list = $("origin-list");
    list.replaceChildren();
    if (!origins.length) {
      list.append(el("p", "hint", "该世界没有配置出身；输入玩家 ID 即可进入。"));
      return;
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
      if (origin.first_task) card.append(el("div", "origin-first-task", `第一件事：${origin.first_task}`));
      card.addEventListener("click", () => {
        state.selectedOrigin = origin.id;
        for (const sibling of list.children) sibling.classList.remove("active");
        card.classList.add("active");
      });
      list.append(card);
    }
  }

  function renderWorldTemplates() {
    const list = $("world-template-list");
    if (!list) return;
    list.replaceChildren();
    const worlds = Array.isArray(state.worlds) ? state.worlds : [];
    if (!worlds.length) {
      list.append(el("p", "entry-empty", "世界模板列表暂不可用。可以尝试刷新页面，或用运营台的“设计世界”打开已有包。"));
      return;
    }
    if (!worlds.some((item) => item.id === state.selectedWorldPackage)) {
      state.selectedWorldPackage = worlds[0]?.id || "";
    }
    for (const item of worlds) {
      const card = el("button", `world-template-card${item.id === state.selectedWorldPackage ? " active" : ""}`);
      card.type = "button";
      paintWorldCard(card, item.id || item.name);
      card.append(el("div", "world-card-cover"));
      card.append(el("strong", "", item.name || item.id));
      if (item.genre) card.append(el("small", "", item.genre));
      if (item.premise) card.append(el("small", "", item.premise));
      const badges = el("div", "badge-row");
      badges.append(el("span", "badge", item.id));
      if (item.locale) badges.append(el("span", "badge", item.locale));
      if (item.source === "authored") badges.append(el("span", "badge accent", "自建包"));
      card.append(badges);
      card.addEventListener("click", () => selectWorldPackage(item, card));
      list.append(card);
    }
  }

  function selectWorldPackage(item, card = null) {
    state.selectedWorldPackage = item?.id || "";
    const list = $("world-template-list");
    if (list) {
      for (const child of list.children) {
        child.classList.toggle("active", child === card);
      }
    }
    const input = $("new-instance-id");
    if (input && item?.id) {
      const suffix = Date.now().toString(36).slice(-4);
      input.value = `${item.id}_${suffix}`;
    }
    $("new-instance-hint").textContent = item?.name
      ? `${item.name}${item.genre ? ` · ${item.genre}` : ""}。实例 ID 用于存档目录，只能使用字母、数字、下划线或连字符。`
      : "实例 ID 用于存档目录，只能使用字母、数字、下划线或连字符。";
    $("new-instance-error").textContent = "";
  }

  function openCreatePanel() {
    $("entry-create-panel").classList.remove("hidden");
    renderWorldTemplates();
    const input = $("new-instance-id");
    if (input && !input.value && state.selectedWorldPackage) {
      input.value = `${state.selectedWorldPackage}_${Date.now().toString(36).slice(-4)}`;
    }
    $("new-instance-id")?.focus();
  }

  async function loadEntryContext() {
    const results = await Promise.allSettled([
      request("/world"),
      request("/player/entry"),
      request("/instances"),
      request("/runtime"),
      request("/worlds"),
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

  function openWorldHub(message = "") {
    $("game").classList.add("hidden");
    $("mobile-action-bar").classList.add("hidden");
    closePlayerSheet();
    closeCharacter();
    $("entry-screen").classList.remove("hidden");
    $("entry-error").textContent = message || "";
    $("entry-return").classList.toggle("hidden", !state.playerId);
    renderEntry();
  }

  function showEntry(message = "") {
    state.playerId = null;
    state.playerToken = null;
    state.view = null;
    storageSet(PLAYER_KEY, null);
    openWorldHub(message);
  }

  function returnToGame() {
    if (!state.playerId) {
      toast("先选择一个角色进入世界。", "error");
      return;
    }
    enterGame();
    if (!state.view) loadView().catch(() => {});
  }

  function enterGame() {
    $("entry-screen").classList.add("hidden");
    $("game").classList.remove("hidden");
    $("entry-error").textContent = "";
  }

  async function submitEntry(button, forcedPlayerId = null) {
    const playerId = String(forcedPlayerId || $("entry-id").value || "").trim();
    const name = forcedPlayerId ? "" : String($("entry-name").value || "").trim();
    const originId = forcedPlayerId ? "" : state.selectedOrigin;
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
        state.playerName = result.name || name || state.playerId;
        state.playerToken = result.session_token || null;
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

  async function activateWorldInstance(instanceId, button = null) {
    if (!instanceId) return;
    if (!state.operatorToken) {
      openCreatePanel();
      $("entry-admin").open = true;
      $("new-instance-error").textContent = "切换已有世界需要先在下方“管理权限”解锁。";
      return;
    }
    await withPending(button, async () => {
      try {
        const result = await write("/instances/activate", { instance_id: instanceId });
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

  async function createWorldInstance(button) {
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
        const result = await write("/instances/init", { package_id: packageId, instance_id: instanceId, activate: true });
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

  async function restorePlayer() {
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
    renderCrisisStrip();
    renderMiniMap();
    renderTask();
    renderActions();
    renderResultBeat();
    renderMobileActionBar();
    renderOnboarding();
    renderGoals();
    renderWorldProperties();
    renderFacilities();
    renderEnding();
    renderTalkTargets();
    renderPeople();
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
    const role = player.role && player.role !== "player" ? player.role : "玩家";
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
    const inventoryCount = Object.keys(view.inventory || {}).filter((key) => Number(view.inventory[key]) > 0).length;
    const capabilityCount = Array.isArray(view.capabilities) ? view.capabilities.length : 0;
    $("inventory-count").textContent = `${inventoryCount} 种`;
    $("capability-count").textContent = `${capabilityCount} 项`;
    $("player-loadout").textContent = `${inventoryCount} 种物品 · ${capabilityCount} 项能力`;
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
    const theme = sceneTheme(location);
    art.dataset.scene = theme;
    renderSceneWorldCover(theme);
    if (state.soundOn && state.audio) applyAmbientProfile();
    $("scene-title").textContent = location.name || player.location || "未知地点";
    $("scene-description").textContent = location.description || "这里没有更多描述。";
    $("scene-eyebrow").textContent = player.location_safe ? "安全地点" : "你在这里";
    const tags = $("scene-tags");
    tags.replaceChildren();
    const tagNames = { public: "公共", social: "社交", market: "集市", resource: "资源", craft: "工坊", safe: "安全", research: "研究", exploration: "探索", defense: "防线", infrastructure: "基础设施", home: "住屋" };
    for (const tag of location.tags || []) {
      if (tagNames[tag]) tags.append(el("span", "badge", tagNames[tag]));
    }
    const properties = view.world_properties || {};
    const propertyLabels = view.world_property_labels || {};
    for (const [key, meta] of Object.entries(propertyLabels)) {
      if (!meta?.alert || properties[key] === undefined) continue;
      tags.append(el("span", "badge danger", `${meta.label || humanizeKey(key)} ${formatWorldProperty(properties[key], meta)}`));
    }

    const objects = $("scene-objects");
    objects.replaceChildren();
    const nearby = Array.isArray(view.nearby_entities) ? view.nearby_entities : [];
    const objectItems = nearby.filter((item) => item.type && !["agent", "player", "resource"].includes(item.type));
    const statusNames = { offline: "未修复", working: "运转中", damaged: "受损", repaired: "已修复", dim: "暗淡", tuned: "已校准", partial: "残缺", braced: "加固中", open: "开放", closed: "封闭" };
    for (const item of objectItems.slice(0, 4)) {
      const status = item.initial_state?.status || "";
      const chip = el("div", "scene-object");
      chip.append(el("i"));
      chip.append(el("span", "", `${item.name || item.id}${status ? ` · ${statusNames[status] || status}` : ""}`));
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
        const select = $("talk-target");
        const option = select ? [...select.options].find((item) => item.value === person.id) : null;
        if (option || nearbyParticipants().some((item) => item.id === person.id)) {
          openCharacter(person);
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

  function renderCrisisStrip() {
    const panel = $("crisis-strip");
    if (!panel) return;
    const metrics = Array.isArray(state.view?.ui?.crisis_metrics) ? state.view.ui.crisis_metrics : [];
    const root = $("crisis-metrics");
    if (!root) return;
    if (!metrics.length) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    root.replaceChildren();
    const properties = state.view?.world_properties || {};
    const labels = state.view?.world_property_labels || {};
    for (const metric of metrics) {
      const key = String(metric?.property || "").trim();
      if (!key || properties[key] === undefined) continue;
      const base = labels[key] || {};
      const meta = {
        ...base,
        label: metric.label || base.label || humanizeKey(key),
        unit: metric.format || base.unit,
        max: metric.max ?? base.max,
        true_label: metric.true_label ?? base.true_label,
        false_label: metric.false_label ?? base.false_label,
      };
      const numeric = Number(properties[key]);
      const danger = Number(metric.danger_below);
      const warning = Number(metric.warning_below);
      let level = "good";
      if (Number.isFinite(danger) && numeric < danger) level = "danger";
      else if (Number.isFinite(warning) && numeric < warning) level = "warning";
      const card = el("article", `crisis-metric ${level}`);
      if (metric.hint) card.title = String(metric.hint);
      card.append(el("span", "crisis-label", meta.label));
      let display = formatWorldProperty(properties[key], meta);
      if (meta.max && !["percent", "hours", "bool"].includes(meta.unit)) display += ` / ${meta.max}`;
      card.append(el("strong", "", display));
      const track = el("div", "crisis-track");
      const fill = el("span");
      const max = Number(meta.max || (meta.unit === "percent" ? 1 : Math.max(1, numeric)));
      fill.style.width = `${clamp((numeric / max) * 100, 0, 100)}%`;
      track.append(fill);
      card.append(track);
      if (metric.hint) card.append(el("small", "", metric.hint));
      root.append(card);
    }
    if (!root.children.length) panel.classList.add("hidden");
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

  const CATEGORY_ORDER = ["移动", "采集", "制作", "公共", "交易", "社交", "恢复", "其他"];

  function actionCategory(action) {
    if (action.kind === "travel" || action.ref === "behavior:travel") return "移动";
    const ref = String(action.ref || "");
    if (ref.includes("market") || ref.includes("trade") || ref.includes("barter")) return "交易";
    if (ref.includes("gather")) return "采集";
    if (ref.includes("craft")) return "制作";
    if (ref.includes("communicate")) return "社交";
    if (ref.includes("eat") || ref.includes("drink")) return "恢复";
    if (ref.includes("inspect") || ref.includes("repair") || ref.includes("research") || ref.includes("explore") || ref.includes("tune") || ref.includes("brace") || ref.includes("stock")) return "公共";
    return "其他";
  }

  function actionButtonLabel(category, action) {
    if (category === "移动") return "前往";
    if (category === "采集") return "开始采集";
    if (category === "制作") return "开始制作";
    if (category === "社交") return "交谈";
    if (category === "交易") return "交换";
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
        state.actionExpanded = false;
        renderActions();
        renderMobileActionBar();
      });
      filterRoot.append(button);
    }

    const filtered = actions.filter((action) => state.actionFilter === "全部" || actionCategory(action) === state.actionFilter);
    const taskActive = Boolean(view?.active_task?.bt || view?.active_task?.action);
    const featured = !taskActive && filtered.length ? chooseRecommendedAction(filtered) : null;
    const recommended = $("recommended-action");
    recommended.replaceChildren();
    recommended.classList.toggle("hidden", !featured);
    if (featured) recommended.append(recommendedActionCard(featured));

    const grid = $("action-grid");
    grid.replaceChildren();
    const rest = featured ? filtered.filter((action) => action !== featured) : filtered;
    if (!actions.length) {
      grid.append(el("div", "inventory-empty", "现在没有平台认可的行动；可以在地图上换一个地点。"));
    } else if (!rest.length && !featured) {
      grid.append(el("div", "inventory-empty", "这个分类下暂时没有可执行行动。"));
    }
    const limit = state.actionExpanded ? rest.length : Math.min(5, rest.length);
    for (const action of rest.slice(0, limit)) {
      grid.append(actionRow(action));
    }
    const more = $("action-more");
    const remaining = rest.length - limit;
    const canCollapse = state.actionExpanded && rest.length > 5;
    more.classList.toggle("hidden", !(remaining > 0 || canCollapse));
    more.textContent = state.actionExpanded
      ? "收起行动"
      : `显示更多行动（${Math.max(0, remaining)}）`;

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

  function chooseRecommendedAction(actions) {
    return actions.find((action) => actionCategory(action) !== "移动") || actions[0] || null;
  }

  function actionChips(action, limit = 4) {
    const chips = [];
    for (const value of action.cost || []) chips.push(translateImpact(value));
    for (const value of (action.impact || []).slice(0, 2)) chips.push(translateImpact(value));
    return [...new Set(chips.filter(Boolean))].slice(0, limit);
  }

  function recommendedActionCard(action) {
    const category = actionCategory(action);
    const card = el("article", "recommended-card");
    card.dataset.category = category;
    const copy = el("div", "recommended-copy");
    copy.append(el("span", "eyebrow", `推荐下一步 · ${category}`));
    copy.append(el("strong", "", action.choice || action.name || "行动"));
    copy.append(el("p", "", action.summary || action.purpose || "按照世界规则执行这个行动。"));
    const meta = el("div", "badge-row");
    const goal = (state.view?.goals || []).find((item) => (item.ready_behavior_refs || []).includes(action.ref));
    if (goal) meta.append(el("span", "badge accent", `推进：${goal.label || goal.id}`));
    meta.append(el("span", "badge", translateTime(action.time || "时间未知")));
    for (const chip of actionChips(action, 3)) meta.append(el("span", "badge", chip));
    copy.append(meta);
    card.append(copy);
    const button = el("button", "button primary recommended-action-button", actionButtonLabel(category, action));
    button.type = "button";
    button.addEventListener("click", () => beginAction(action, button));
    card.append(button);
    return card;
  }

  function actionRow(action) {
    const category = actionCategory(action);
    const row = el("article", "action-row");
    row.dataset.category = category;
    const main = el("div", "action-row-main");
    const head = el("div", "action-row-head");
    head.append(el("strong", "", action.choice || action.name || "行动"));
    head.append(el("span", "badge", category));
    main.append(head);
    main.append(el("p", "", action.summary || action.purpose || "按照世界规则执行这个行动。"));
    const meta = el("div", "action-row-meta");
    for (const chip of actionChips(action, 3)) meta.append(el("span", "action-chip", chip));
    if (meta.children.length) main.append(meta);
    row.append(main);
    const side = el("div", "action-row-side");
    side.append(el("span", "action-time", translateTime(action.time || "时间未知")));
    const button = el("button", "button ghost", actionButtonLabel(category, action));
    button.type = "button";
    button.addEventListener("click", () => beginAction(action, button));
    side.append(button);
    row.append(side);
    return row;
  }

  const RESULT_LABELS = {
    resource_collected: "采集完成",
    recipe_completed: "制作完成",
    route_traversed: "抵达目的地",
    arrived: "抵达目的地",
    inspected: "检查完成",
    inspection_completed: "检查完成",
    research_progressed: "研究推进",
    research_completed: "研究完成",
    repaired: "修复完成",
    information_recorded: "情报已记录",
    rationing_decided: "配给已调整",
    repair_priority_decided: "抢修已完成",
    marsh_rest_decided: "休养已安排",
    decision_failed: "决策中断",
  };

  function renderResultBeat() {
    const panel = $("result-beat");
    if (!panel) return;
    const outcome = (state.view?.recent_outcomes || [])[0];
    const currentMinute = Number(state.view?.world_time?.minute || 0);
    if (!outcome || currentMinute - Number(outcome.world_minute || 0) > 720) {
      panel.classList.add("hidden");
      return;
    }
    const result = String(outcome.result || "");
    const failed = String(outcome.event_type || "").includes("failed");
    panel.classList.remove("hidden");
    panel.classList.toggle("failed", failed);
    panel.querySelector(".result-beat-mark").textContent = failed ? "!" : "✓";
    $("result-time").textContent = formatMinute(outcome.world_minute);
    const expectedSummary = String(outcome.outcome?.expected?.summary || "");
    $("result-title").textContent = (expectedSummary && expectedSummary.length <= 24 ? expectedSummary : "")
      || outcome.behavior_choice
      || expectedSummary
      || outcome.behavior_id
      || "行动完成";
    $("result-detail").textContent = failed
      ? `行动未完成${result ? ` · ${result}` : ""}`
      : (RESULT_LABELS[result] || humanizeKey(result || "completed"));

    const deltas = $("result-deltas");
    deltas.replaceChildren();
    const delta = outcome.outcome?.delta || {};
    const chips = [];
    if (delta.location?.after) chips.push(`抵达 ${delta.location.after}`);
    for (const [key, change] of Object.entries(delta.inventory || {})) {
      const before = Number(change?.before || 0);
      const after = Number(change?.after || 0);
      const diff = Math.round((after - before) * 10) / 10;
      if (!diff) continue;
      chips.push(`${itemLabel(key)} ${diff > 0 ? "+" : "−"}${Math.abs(diff)}`);
    }
    const needNames = { hydration: "水分", satiety: "饱食", belonging: "归属", purpose: "意义" };
    for (const [key, change] of Object.entries(delta.needs || {})) {
      const diff = Number(change?.after || 0) - Number(change?.before || 0);
      if (Math.abs(diff) < 1) continue;
      chips.push(`${needNames[key] || humanizeKey(key)} ${diff > 0 ? "+" : "−"}${Math.abs(Math.round(diff))}`);
    }
    const attributeNames = { stamina: "精力", integrity: "体力" };
    for (const [key, change] of Object.entries(delta.attributes || {})) {
      const diff = Number(change?.after || 0) - Number(change?.before || 0);
      if (Math.abs(diff) < 0.01) continue;
      chips.push(`${attributeNames[key] || humanizeKey(key)} ${diff > 0 ? "+" : "−"}${Math.abs(diff).toFixed(2)}`);
    }
    for (const chip of chips.slice(0, 4)) deltas.append(el("span", "badge", chip));
    if (!chips.length) deltas.append(el("span", "badge", result || "世界已记录"));
  }

  function renderMobileActionBar() {
    const bar = $("mobile-action-bar");
    if (!bar) return;
    const task = state.view?.active_task || {};
    const taskActive = Boolean(task.bt || task.action);
    const actions = Array.isArray(state.view?.actions) ? state.view.actions : [];
    const action = taskActive ? null : chooseRecommendedAction(actions);
    if (!taskActive && !action) {
      bar.classList.add("hidden");
      bar._action = null;
      return;
    }
    bar.classList.remove("hidden");
    bar._action = action;
    if (taskActive) {
      $("mobile-primary-label").textContent = `推进：${task.bt || task.action || "当前行动"}`;
      $("mobile-primary-meta").textContent = `还需 ${Number(task.remaining_minutes || 0)} 分钟`;
    } else {
      $("mobile-primary-label").textContent = action.choice || action.name || "下一步";
      $("mobile-primary-meta").textContent = `${actionCategory(action)} · ${translateTime(action.time || "时间未知")}`;
    }
  }

  function formatWorldProperty(value, meta = {}) {
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

  function humanizeReason(reason) {
    let text = String(reason || "").trim().replace(/^Configured condition:\s*/i, "").replace(/\.\s*$/, "");
    let match = text.match(/^cooldown until world minute (\d+)$/i);
    if (match) {
      const current = Number(state.view?.world_time?.minute || 0);
      const remaining = Math.max(0, Number(match[1]) - current);
      const days = Math.floor(remaining / 1440);
      const hours = Math.floor((remaining % 1440) / 60);
      const minutes = remaining % 60;
      const parts = [];
      if (days) parts.push(`${days} 天`);
      if (hours) parts.push(`${hours} 小时`);
      if (minutes || !parts.length) parts.push(`${minutes} 分钟`);
      return `行动冷却中：还需 ${parts.join(" ")}（世界第 ${match[1]} 分钟后可再次执行）`;
    }
    match = text.match(/^(?:World|world\.)([A-Za-z0-9_ .-]+?) (>=|<=|==|>|<) (-?\d+(?:\.\d+)?)$/);
    if (match) {
      const key = match[1].trim().replace(/\s+/g, "_");
      const meta = state.view?.world_property_labels?.[key] || {};
      const label = meta.label || humanizeKey(key);
      const symbol = match[2].includes("<") ? "不高于" : (match[2].includes(">") ? "至少" : "等于");
      return `需要${label}${symbol} ${formatWorldProperty(match[3], meta)}`;
    }
    match = text.match(/^actor\.need\.(\w+) ([<>]=?) (\d+(?:\.\d+)?)$/);
    if (match) {
      const needNames = { hydration: "水分", satiety: "饱食", belonging: "归属", purpose: "意义" };
      const symbol = match[2].includes("<") ? "低于或等于" : "高于";
      return `需要${needNames[match[1]] || match[1]}${symbol} ${match[3]}`;
    }
    match = text.match(/^(?:Actor|actor) inventory ([A-Za-z0-9_ .-]+?) (>=|<=) (-?\d+(?:\.\d+)?)$/);
    if (match) {
      const key = match[1].trim().replace(/\s+/g, "_");
      return `需要 ${itemLabel(key)} ${match[2] === ">=" ? "至少" : "不多于"} ${match[3]} 个`;
    }
    match = text.match(/^(?:Actor|actor) need (\w+) ([<>]=?) (-?\d+(?:\.\d+)?)$/);
    if (match) {
      const needNames = { hydration: "水分", satiety: "饱食", belonging: "归属", purpose: "意义" };
      return `需要${needNames[match[1]] || match[1]}${match[2].includes("<") ? "低于或等于" : "高于"} ${match[3]}`;
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
      [/Action gather[ _]resource cannot start: required resource (.+) is unavailable/, (_m, name) => `${itemLabel(String(name).replace(/\s+/g, "_"))}资源点已经枯竭`],
      [/Action craft[ _]recipe cannot start: required carried item (.+) is unavailable/, (_m, name) => `缺少随身材料：${itemLabel(String(name).replace(/\s+/g, "_"))}`],
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

  function actionForRef(ref) {
    const actions = Array.isArray(state.view?.actions) ? state.view.actions : [];
    return actions.find((action) => action.ref === ref) || null;
  }

  function renderOnboarding() {
    const card = $("onboarding-card");
    if (!card) return;
    const step = state.view?.onboarding || null;
    if (!step) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    $("onboarding-step").textContent = `${step.step || 1}/${step.total || 1}`;
    $("onboarding-title").textContent = step.title || "序章";
    $("onboarding-body").textContent = step.body || "";
    const action = step.action_ref ? actionForRef(step.action_ref) : null;
    const button = $("onboarding-action");
    button.classList.toggle("hidden", !action);
    if (action) {
      button.textContent = `去做：${action.choice || action.name || step.title}`;
      button.onclick = () => beginAction(action, button);
    }
  }

  function renderGoals() {
    const root = $("goal-list");
    root.replaceChildren();
    const goals = Array.isArray(state.view?.goals) ? state.view.goals : [];
    const chapter = state.view?.chapter || null;
    const chapterEyebrow = $("chapter-eyebrow");
    if (chapterEyebrow) {
      chapterEyebrow.textContent = chapter
        ? (chapter.kind === "story" ? "自动演化" : (chapter.kind === "ending" ? "季末结局" : "当前态势"))
        : "公开篇章";
    }
    const chapterTitle = $("chapter-title");
    if (chapterTitle) chapterTitle.textContent = chapter?.title || "世界目标";
    const chapterSummary = $("chapter-summary");
    if (chapterSummary) {
      const text = String(chapter?.summary || "").trim();
      chapterSummary.textContent = text;
      chapterSummary.classList.toggle("hidden", !text);
    }
    const chapterGoal = $("chapter-goal");
    if (chapterGoal) {
      const text = String(chapter?.goal_hint || "").trim();
      chapterGoal.textContent = text;
      chapterGoal.classList.toggle("hidden", !text);
    }
    const goalsPanel = document.querySelector(".goals-panel");
    if (goalsPanel) goalsPanel.classList.toggle("ending", chapter?.kind === "ending");
    const summary = $("goal-summary");
    if (summary) summary.textContent = goals.length ? `${goals.length} 项待完成` : "全部完成";
    if (!goals.length) {
      root.append(el("p", "goal-empty", "当前世界没有公开的目标投影。"));
      return;
    }
    const statusNames = { ready: "可推进", needs_support: "需准备", blocked: "受阻", completed: "已完成" };
    const visibleGoals = state.goalsExpanded ? goals : goals.slice(0, 3);
    visibleGoals.forEach((goal, index) => {
      const status = String(goal.status || "blocked");
      const card = el("article", `chapter-card ${status}`);
      const head = el("div", "chapter-card-head");
      head.append(el("span", "goal-mark", String(index + 1).padStart(2, "0")));
      const copy = el("div", "goal-copy");
      copy.append(el("strong", "", goal.label || goal.id));
      copy.append(el("span", "", goal.summary || ""));
      head.append(copy);
      head.append(el("span", `goal-status ${status}`, statusNames[status] || status));
      card.append(head);

      const current = Number(goal.progress?.current || 0);
      const required = Number(goal.progress?.required || 0);
      if (required > 0) {
        const track = el("div", "goal-track");
        const fill = el("span");
        fill.style.width = `${clamp((current / required) * 100, 0, 100)}%`;
        track.append(fill);
        card.append(track);
      }

      const foot = el("div", "goal-foot");
      if (required > 0) foot.append(el("span", "goal-progress", `${current} / ${required}`));
      const readyCount = (goal.ready_behavior_refs || []).length;
      const nextLabel = readyCount <= 1
        ? ((goal.ready_behaviors || [])[0] || (goal.support_behaviors || [])[0] || "")
        : "";
      if (nextLabel) foot.append(el("span", "goal-next", `${status === "ready" ? "下一步" : "先准备"}：${nextLabel}`));
      if (foot.children.length) card.append(foot);

      const requirements = (goal.missing_requirements || [])
        .map((reason) => humanizeReason(reason))
        .filter(Boolean);
      if (status !== "ready" && requirements.length) {
        card.append(el("p", "goal-blockers", `还缺：${requirements.slice(0, 2).join("；")}`));
      }

      const goalActions = (goal.ready_behavior_refs || []).map(actionForRef).filter(Boolean).slice(0, 3);
      if (goalActions.length) {
        const row = el("div", `goal-actions${goalActions.length > 1 ? " choice" : ""}`);
        goalActions.forEach((item, actionIndex) => {
          const label = item.choice || item.name || goal.label;
          const button = el(
            "button",
            `button goal-do${actionIndex === 0 ? " primary" : " ghost"}`,
            goalActions.length > 1 ? label : `去做：${label}`,
          );
          button.type = "button";
          button.addEventListener("click", () => beginAction(item, button));
          row.append(button);
        });
        card.append(row);
      }
      root.append(card);
    });
    if (goals.length > 3) {
      const more = el("button", "text-button wide goal-more", state.goalsExpanded ? "收起目标" : `显示全部 ${goals.length} 个目标`);
      more.type = "button";
      more.addEventListener("click", () => {
        state.goalsExpanded = !state.goalsExpanded;
        renderGoals();
        renderMobileActionBar();
      });
      root.append(more);
    }
  }

  function appendWorldProperty(root, key, value, meta) {
    const label = meta.label || humanizeKey(key);
    const numeric = Number(value);
    let display = formatWorldProperty(value, meta);
    if (meta.max && !["percent", "hours", "bool"].includes(meta.unit)) display += ` / ${meta.max}`;
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

  function renderWorldProperties() {
    const root = $("world-properties");
    root.replaceChildren();
    const properties = state.view?.world_properties || {};
    const labels = state.view?.world_property_labels || {};
    const visibleEntries = Object.entries(properties)
      .filter(([key]) => !(labels[key] || {}).hidden)
      .sort((left, right) => {
        const leftMeta = labels[left[0]] || {};
        const rightMeta = labels[right[0]] || {};
        return (Number(leftMeta.group_order || 0) - Number(rightMeta.group_order || 0))
          || (Number(leftMeta.order || 0) - Number(rightMeta.order || 0));
      });
    if (!visibleEntries.length) {
      root.append(el("p", "hint", "当前世界没有公开数值。"));
      return;
    }
    if (!visibleEntries.some(([key]) => (labels[key] || {}).group)) {
      for (const [key, value] of visibleEntries) appendWorldProperty(root, key, value, labels[key] || {});
      return;
    }
    const groups = new Map();
    for (const [key, value] of visibleEntries) {
      const group = String((labels[key] || {}).group || "其他");
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push([key, value]);
    }
    for (const [group, rows] of groups) {
      root.append(el("div", "world-group-title", group));
      for (const [key, value] of rows) appendWorldProperty(root, key, value, labels[key] || {});
    }
  }

  const FACILITY_STATUS = {
    damaged: ["受损", "warning"],
    offline: ["未修复", "warning"],
    dim: ["暗淡", "warning"],
    partial: ["残缺", "warning"],
    repaired: ["已修复", "good"],
    working: ["运转中", "good"],
    tuned: ["已校准", "good"],
    braced: ["加固中", "good"],
    stocked: ["可领取", "good"],
    empty: ["已空", "danger"],
    open: ["开放", "good"],
    closed: ["封闭", "danger"],
  };

  function renderFacilities() {
    const panel = $("facility-panel");
    const root = $("facility-grid");
    if (!panel || !root) return;
    const facilities = Array.isArray(state.view?.facilities) ? state.view.facilities : [];
    if (!facilities.length) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    root.replaceChildren();
    for (const item of facilities) {
      const card = el("article", "facility-card");
      card.dataset.status = String(item.status || "");
      if (item.description) card.title = String(item.description);
      const head = el("div", "facility-head");
      head.append(el("strong", "", item.name || item.id || "设施"));
      const status = FACILITY_STATUS[item.status] || [item.status ? humanizeKey(item.status) : "待检查", "neutral"];
      head.append(el("span", `facility-status ${status[1]}`, status[0]));
      card.append(head);
      if (item.location) card.append(el("span", "facility-location", item.location));
      const values = el("div", "facility-values");
      for (const prop of item.properties || []) {
        const value = el("div", "facility-value");
        value.append(el("span", "", prop.label || humanizeKey(prop.property)));
        let display = formatWorldProperty(prop.value, {
          unit: prop.unit,
          true_label: prop.true_label,
          false_label: prop.false_label,
        });
        if (prop.max && !["percent", "hours", "bool"].includes(prop.unit)) display += ` / ${prop.max}`;
        value.append(el("strong", "", display));
        values.append(value);
      }
      if (!values.children.length && item.stock !== null && item.stock !== undefined) {
        const value = el("div", "facility-value");
        value.append(el("span", "", "库存"));
        value.append(el("strong", "", String(item.stock)));
        values.append(value);
      }
      if (values.children.length) card.append(values);
      root.append(card);
    }
  }

  function renderEnding() {
    const overlay = $("ending-overlay");
    if (!overlay) return;
    const chapter = state.view?.chapter || null;
    if (!chapter || chapter.kind !== "ending") {
      if (!overlay.classList.contains("hidden")) closeEnding(false);
      state.endingKey = "";
      return;
    }
    const key = String(chapter.id || chapter.title || "ending");
    const stored = storageGet(`${ENDING_KEY}.${state.playerId || "guest"}`, "");
    if (stored === key) {
      if (!overlay.classList.contains("hidden")) closeEnding(false);
      return;
    }
    if (state.endingKey === key && !overlay.classList.contains("hidden")) return;
    state.endingKey = key;
    $("ending-title").textContent = chapter.title || "季末结局";
    $("ending-summary").textContent = chapter.summary || "";
    const goal = String(chapter.goal_hint || "").trim();
    $("ending-goal").textContent = goal;
    $("ending-goal").classList.toggle("hidden", !goal);
    const stats = Array.isArray(chapter.stats) ? chapter.stats : [];
    const root = $("ending-stats");
    root.replaceChildren();
    for (const item of stats) {
      const property = String(item?.property || "");
      if (!property || state.view?.world_properties?.[property] === undefined) continue;
      const meta = state.view?.world_property_labels?.[property] || {};
      const card = el("div", "ending-stat");
      card.append(el("span", "", item.label || meta.label || humanizeKey(property)));
      card.append(el("strong", "", formatWorldProperty(state.view.world_properties[property], meta)));
      root.append(card);
    }
    root.classList.toggle("hidden", !root.children.length);
    overlay.classList.remove("hidden");
    document.body.classList.add("ending-open");
    window.setTimeout(() => $("ending-continue")?.focus(), 30);
  }

  function closeEnding(markSeen = true) {
    const overlay = $("ending-overlay");
    if (!overlay) return;
    if (markSeen && state.endingKey && state.playerId) {
      storageSet(`${ENDING_KEY}.${state.playerId}`, state.endingKey);
    }
    overlay.classList.add("hidden");
    document.body.classList.remove("ending-open");
  }

  /* ---------- Talk ---------- */

  function nearbyParticipants() {
    return (state.view?.nearby_participants || []).filter((item) => item.id !== state.playerId);
  }

  function renderTalkTargets() {
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

  function renderPeople() {
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

  function updateCharacterSheet(person) {
    const name = person?.name || person?.id || "人物";
    $("character-name").textContent = name;
    $("character-avatar").textContent = String(name).slice(0, 1);
    $("character-location").textContent = person?.location || person?.location_id || state.view?.location?.name || "附近";
    $("character-status").textContent = person?.status === "executing" ? "正在忙碌" : "可以交谈";
  }

  function openCharacter(person) {
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

  function closeCharacter() {
    state.selectedCharacter = null;
    $("character-drawer").classList.add("hidden");
  }

  function openPlayerSheet() {
    $("player-drawer").classList.remove("hidden");
  }

  function closePlayerSheet() {
    $("player-drawer").classList.add("hidden");
  }

  function personName(personId) {
    const person = nearbyParticipants().find((item) => item.id === personId);
    return person?.name || personId;
  }

  async function loadDialogue(characterId = state.selectedCharacter?.id || "") {
    const root = $("dialogue-list");
    if (!root) return;
    try {
      const response = await request("/observer/dialogue?limit=80");
      const exchanges = (response.exchanges || [])
        .filter((item) => item.speaker_id === state.playerId || item.listener_id === state.playerId)
        .filter((item) => !characterId || item.speaker_id === characterId || item.listener_id === characterId)
        .slice(-30);
      root.replaceChildren();
      if (!exchanges.length) {
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
      root.scrollTop = root.scrollHeight;
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
        await Promise.allSettled([loadView(), loadFeed()]);
        const selected = nearbyParticipants().find((item) => item.id === target);
        if (selected && state.selectedCharacter?.id === target) updateCharacterSheet(selected);
        await loadDialogue(target);
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

  function eventKind(type) {
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

  function eventTitle(type) {
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

  function renderWorldPulse() {
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

  function showMapDialog() {
    const dialog = $("map-dialog");
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }
  }

  async function openMap() {
    if (!state.playerId) return;
    state.map = state.view?.map || state.map;
    if (state.map) {
      renderMap();
      showMapDialog();
    }
    try {
      const map = await request(`/map?viewer_id=${encodeURIComponent(state.playerId)}&observer=0`);
      state.map = map;
      renderMap();
      showMapDialog();
    } catch (error) {
      if (!state.map) toast(describeError(error, "地图暂不可用"), "error");
    }
  }

  function renderMiniMap() {
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

  function renderMap() {
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

  function travelToNode(node) {
    const travelAction = (state.view?.actions || []).find(
      (entry) => entry.ref === "behavior:travel" && entry.parameters?.location_id === node.id,
    );
    const dialog = $("map-dialog");
    if (dialog.open) dialog.close();
    // beginAction forwards action.parameters; calling submitAction directly
    // sent an empty parameter object, dropping the destination.
    beginAction(travelAction || {
      ref: "behavior:travel",
      choice: node.name || node.id,
      name: node.name || node.id,
      kind: "travel",
      parameters: { location_id: node.id },
    });
  }

  function renderTopology(canvas, map, options = {}) {
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
      } else if (runtime.llm_configured === false) {
        detail.textContent = "未配置模型：世界时间和你的长行动仍会推进，NPC 暂时不会自主决策。配置 ~/.sim2/llm.json 后即可启用完整 Agent。";
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
      renderEntryInstances();
    } catch (error) {
      toast(describeError(error, "无法读取实例列表"), "error");
    }
  }

  function renderInstanceSelects() {
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

  async function exchangeOperatorToken(raw) {
    const token = String(raw || "").trim();
    if (!token) throw new Error("请输入 Operator Token。");
    const result = await write("/session", { role: "operator", operator_token: token });
    if (result.status !== "ok" || !result.session_token) throw new Error(result.message || "解锁失败");
    state.operatorToken = result.session_token;
    try { sessionStorage.setItem(OPERATOR_KEY, state.operatorToken); } catch (error) { /* ignore */ }
    return state.operatorToken;
  }

  async function unlockOperator() {
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

  async function unlockOperatorFromEntry() {
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

  /* ---------- World editor handoff ---------- */

  function adminUrl(viewId = "") {
    const params = new URLSearchParams();
    if (viewId) params.set("view", viewId);
    if (state.operatorToken) params.set("operator", state.operatorToken);
    const query = params.toString();
    return `/admin.html${query ? `#${query}` : ""}`;
  }

  function openAdmin(viewId = "") {
    const win = window.open(adminUrl(viewId), "_blank");
    if (win) {
      try { win.opener = null; } catch (error) { /* cross-window hardening */ }
    } else {
      toast("浏览器阻止了弹出窗口；请允许本站打开新标签后重试。", "error");
    }
  }

  function openEditor() {
    const overlay = $("editor-overlay");
    const frame = $("editor-frame");
    if (!overlay || !frame) return;
    overlay.classList.remove("hidden");
    document.body.classList.add("editor-open");
    frame.src = adminUrl("authoring-view");
  }

  async function closeEditor() {
    $("editor-overlay").classList.add("hidden");
    document.body.classList.remove("editor-open");
    $("editor-frame").src = "about:blank";
    try {
      const instances = await request("/instances");
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
        if (name === "topic") input.value = "近况";
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
    if (tab === "talk") renderPeople();
    if (tab === "trade") loadTrades();
  }

  /* ---------- Wiring ---------- */

  function wireUi() {
    $("hud-refresh").innerHTML = icon("refresh");
    $("hud-worlds").innerHTML = icon("globe");
    $("hud-editor").innerHTML = icon("pen");
    $("hud-sound").innerHTML = icon("soundOff");
    $("hud-settings").innerHTML = icon("settings");
    $("settings-close").innerHTML = "";
    $("entry-worlds-refresh").innerHTML = icon("refresh");

    state.soundOn = Boolean(storageGet(SOUND_KEY, false));
    renderSoundButton();

    $("hud-refresh").addEventListener("click", () => refreshAll());
    $("hud-worlds").addEventListener("click", () => openWorldHub());
    $("hud-editor").addEventListener("click", () => openEditor());
    $("hud-sound").addEventListener("click", toggleSound);
    $("hud-settings").addEventListener("click", openSettings);
    $("ending-continue").addEventListener("click", () => closeEnding(true));
    $("ending-ledger").addEventListener("click", () => {
      closeEnding(true);
      document.querySelector(".world-panel")?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    $("ending-overlay").addEventListener("click", (event) => {
      if (event.target === $("ending-overlay")) closeEnding(true);
    });
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
    $("entry-create-toggle").addEventListener("click", openCreatePanel);
    $("entry-open-editor").addEventListener("click", openEditor);
    $("new-instance-submit").addEventListener("click", (event) => createWorldInstance(event.currentTarget));
    $("entry-worlds-refresh").addEventListener("click", () => loadEntryContext());
    $("entry-operator-unlock").addEventListener("click", unlockOperatorFromEntry);
    $("entry-return").addEventListener("click", returnToGame);
    $("settings-open-worlds").addEventListener("click", () => {
      closeSettings();
      openWorldHub();
    });
    $("settings-open-editor").addEventListener("click", () => {
      closeSettings();
      openEditor();
    });
    $("settings-open-admin").addEventListener("click", () => openAdmin(""));
    $("editor-close").addEventListener("click", () => closeEditor());
    $("editor-external").addEventListener("click", () => openAdmin("authoring-view"));

    $("task-advance").addEventListener("click", (event) => {
      event.currentTarget.disabled = true;
      advanceTask("继续：").finally(() => renderTask());
    });
    $("task-wait").addEventListener("click", (event) => waitOneMinute(event.currentTarget));
    $("player-details").addEventListener("click", openPlayerSheet);
    $("player-sheet-close").addEventListener("click", closePlayerSheet);
    for (const scrim of document.querySelectorAll("[data-close-player]")) scrim.addEventListener("click", closePlayerSheet);
    $("action-more").addEventListener("click", () => {
      state.actionExpanded = !state.actionExpanded;
      renderActions();
      renderMobileActionBar();
    });
    $("mobile-primary").addEventListener("click", (event) => {
      const action = $("mobile-action-bar")._action;
      if (state.view?.active_task?.bt || state.view?.active_task?.action) {
        advanceTask("继续：");
      } else if (action) {
        beginAction(action, event.currentTarget);
      }
    });
    $("mobile-more").addEventListener("click", () => {
      document.querySelector(".actions-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("player-leave").addEventListener("click", () => showEntry("已回到世界大厅。"));

    $("talk-form").addEventListener("submit", (event) => {
      event.preventDefault();
      sendDialogue(event.currentTarget);
    });
    $("talk-target").addEventListener("change", () => {
      const person = nearbyParticipants().find((item) => item.id === $("talk-target").value);
      if (!person) return;
      state.selectedCharacter = person;
      updateCharacterSheet(person);
      loadDialogue(person.id);
    });
    $("character-close").addEventListener("click", closeCharacter);
    for (const scrim of document.querySelectorAll("[data-close-character]")) scrim.addEventListener("click", closeCharacter);
    $("trade-form").addEventListener("submit", (event) => {
      event.preventDefault();
      sendTrade(event.currentTarget);
    });

    for (const button of $("right-tabs").querySelectorAll("[data-tab]")) {
      button.addEventListener("click", () => switchTab(button.dataset.tab));
    }
    $("open-map").addEventListener("click", openMap);
    $("mini-map-expand").addEventListener("click", openMap);
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
      if (event.key !== "Escape") return;
      if (!$("ending-overlay").classList.contains("hidden")) {
        closeEnding(true);
        return;
      }
      if (!$("editor-overlay").classList.contains("hidden")) {
        closeEditor();
        return;
      }
      if (!$("player-drawer").classList.contains("hidden")) {
        closePlayerSheet();
        return;
      }
      if (!$("character-drawer").classList.contains("hidden")) {
        closeCharacter();
        return;
      }
      if (!$("settings-drawer").classList.contains("hidden")) closeSettings();
    });
    window.addEventListener("message", (event) => {
      if (event.origin !== location.origin) return;
      if (event.data?.type === "sim2:close-editor") closeEditor();
    });
  }

  async function boot() {
    if (state.booted) return;
    state.booted = true;
    try {
      state.operatorToken = sessionStorage.getItem(OPERATOR_KEY) || sessionStorage.getItem("sim2_operator_token") || null;
    } catch (error) {
      state.operatorToken = null;
    }
    wireUi();
    armSoundOnGesture();
    await unlockFromOwnerLink();
    await loadEntryContext();
    const restored = await restorePlayer();
    if (!restored) {
      $("entry-screen").classList.remove("hidden");
      $("game").classList.add("hidden");
      if (!worldIsOpen()) openCreatePanel();
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
      if (!$("character-drawer").classList.contains("hidden")) loadDialogue();
    }, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
