import { activateWorldInstance, deleteWorldInstance, returnToGame, submitEntry } from "./actions.js";
import { paintWorldCard, renderEntryCover } from "./art.js";
import { $, crisisMetrics, el, formatClock, humanizeKey, resolveWorldTemplate, state, worldIsOpen } from "./core.js";


export function renderEntry() {
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
    const taglineText = noWorld ? "" : resolveWorldTemplate(world.ui?.tagline || "", world);
    $("entry-tagline").textContent = taglineText;
    $("entry-tagline").classList.toggle("hidden", !taglineText);
    $("entry-genre").textContent = noWorld ? "世界大厅" : (world.genre || "世界模拟");
    $("entry-season").textContent = world.season || "—";
    $("entry-instance").textContent = noWorld ? "未创建" : (world.instance_id || "当前实例");
    $("hud-world-title").textContent = noWorld ? "世界大厅" : (world.title || "世界");
    $("entry-no-world").classList.toggle("hidden", !noWorld);
    $("entry-play").classList.toggle("hidden", noWorld);
    $("entry-continue").classList.toggle("hidden", noWorld || !hasPlayer);
    $("entry-new-character").classList.toggle("hidden", noWorld || hasPlayer);
    $("entry-origin-section").classList.toggle("hidden", noWorld || hasPlayer);
    $("entry-submit").classList.toggle("hidden", noWorld || hasPlayer);
    $("entry-return").classList.toggle("hidden", !state.playerId);
    $("entry-screen").classList.toggle("has-player", hasPlayer);

    renderEntryStakes(noWorld);
    renderEntryCast(noWorld);
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


export function renderEntryStakes(noWorld = false) {
    const root = $("entry-stakes");
    if (!root) return;
    root.replaceChildren();
    const metrics = noWorld ? [] : crisisMetrics(state.world).slice(0, 4);
    root.classList.toggle("hidden", !metrics.length);
    if (!metrics.length) return;
    for (const metric of metrics) {
      const card = el("article", `entry-stake${metric.level ? ` ${metric.level}` : ""}`);
      card.append(el("span", "entry-stake-label", metric.label));
      card.append(el("strong", "entry-stake-value", metric.display));
      if (metric.hint) card.append(el("small", "entry-stake-hint", metric.hint));
      root.append(card);
    }
  }


export function renderEntryCast(noWorld = false) {
    const section = $("entry-cast");
    const list = $("entry-cast-list");
    if (!section || !list) return;
    list.replaceChildren();
    const cast = noWorld || !Array.isArray(state.world?.cast) ? [] : state.world.cast;
    section.classList.toggle("hidden", !cast.length);
    for (const person of cast) {
      const chip = el("div", "entry-cast-chip");
      chip.append(el("span", "entry-cast-avatar", String(person.name || person.id || "?").slice(0, 1)));
      const copy = el("div", "entry-cast-copy");
      copy.append(el("strong", "", person.name || person.id || "角色"));
      if (person.role) copy.append(el("small", "", humanizeKey(person.role)));
      chip.append(copy);
      list.append(chip);
    }
  }


export function renderEntryInstances() {
    const list = $("entry-instance-list");
    if (!list) return;
    list.replaceChildren();
    if (!state.instances.length) {
      list.append(el("p", "entry-empty", "还没有世界存档。"));
      return;
    }
    for (const item of state.instances) {
      const card = el("article", `instance-card${item.active ? " active" : ""}`);
      card.append(el("strong", "", item.world_title || item.world_id || item.instance_id || "世界"));
      const day = Math.floor(Number(item.world_minute || 0) / 1440) + 1;
      card.append(el("small", "", `${item.instance_id || "实例"} · 第 ${day} 天 · ${item.runtime_status || "未知状态"}`));
      const players = Array.isArray(state.entry?.players) ? state.entry.players : [];
      const resumePlayer = item.active ? players[0] : null;
      const label = item.active
        ? (state.playerId ? "返回游戏" : (resumePlayer ? `继续 ${resumePlayer.name || resumePlayer.id}` : "当前世界"))
        : "切换";
      const button = el("button", "button ghost", label);
      button.type = "button";
      if (item.active) {
        button.disabled = !state.playerId && !resumePlayer;
        if (state.playerId) button.addEventListener("click", () => returnToGame());
        else if (resumePlayer) button.addEventListener("click", () => submitEntry(button, resumePlayer.id));
      } else {
        button.addEventListener("click", () => activateWorldInstance(item.instance_id, button));
      }
      const actions = el("div", "instance-actions");
      actions.append(button);
      if (!item.active) {
        const remove = el("button", "text-button danger", "删除");
        remove.type = "button";
        remove.title = `删除存档 ${item.instance_id}`;
        remove.addEventListener("click", () => deleteWorldInstance(item.instance_id, remove));
        actions.append(remove);
      }
      card.append(actions);
      list.append(card);
    }
  }


export function renderContinue(players) {
    const root = $("entry-continue");
    if (!root) return;
    root.replaceChildren();
    if (!players.length) return;
    root.append(el("span", "eyebrow", "继续游戏"));
    for (const player of players) {
      const card = el("div", "continue-card-inner");
      card.append(el("strong", "", player.name || player.id));
      const meta = player.location ? `位于 ${player.location}` : "";
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


export function renderOrigins(entry) {
    const origins = Array.isArray(entry.origins) ? entry.origins : [];
    const list = $("origin-list");
    list.replaceChildren();
    if (!origins.length) {
      list.append(el("p", "hint", "该世界没有配置出身；输入角色名即可进入。"));
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


export function renderWorldTemplates() {
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
      if (item.kind === "probe" || item.kind === "test") badges.append(el("span", "badge", "测试"));
      if (item.source === "authored") badges.append(el("span", "badge accent", "自建包"));
      card.append(badges);
      card.addEventListener("click", () => selectWorldPackage(item, card));
      list.append(card);
    }
  }


export function selectWorldPackage(item, card = null) {
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


export function openCreatePanel() {
    $("entry-create-panel").classList.remove("hidden");
    renderWorldTemplates();
    const input = $("new-instance-id");
    if (input && !input.value && state.selectedWorldPackage) {
      input.value = `${state.selectedWorldPackage}_${Date.now().toString(36).slice(-4)}`;
    }
    $("new-instance-id")?.focus({ preventScroll: true });
  }
