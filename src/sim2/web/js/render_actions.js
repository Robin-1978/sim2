import { beginAction, selectedProvider } from "./actions.js";
import { $, clamp, el, formatMinute, humanizeKey, icon, itemLabel, resultLabel, state, toast, translateCost, translateImpact, translateTime, uiConfig } from "./core.js";


export function renderTask() {
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


export function renderTaskHint() {
    const root = $("task-detail");
    if (!root || !state.advancing) return;
  }


  /* ---------- Actions ---------- */

export const CATEGORY_ORDER = ["移动", "采集", "制作", "公共", "交易", "社交", "恢复", "其他"];


export function actionCategory(action) {
    if (action.kind === "travel" || action.ref === "behavior:travel") return "移动";
    const tags = new Set([
      ...(Array.isArray(action.goal_tags) ? action.goal_tags : []),
      ...(Array.isArray(action.drive_tags) ? action.drive_tags : []),
      ...(Array.isArray(action.tags) ? action.tags : []),
    ].map((item) => String(item).toLowerCase()));
    if (tags.has("trade") || tags.has("market")) return "交易";
    if (tags.has("recovery") || tags.has("rest") || tags.has("food") || tags.has("water")) return "恢复";
    if (tags.has("resource") || tags.has("supply") || tags.has("gathering") || tags.has("gather")) return "采集";
    if (tags.has("craft") || tags.has("crafting")) return "制作";
    if (tags.has("social") || tags.has("dialogue") || tags.has("conversation")) return "社交";
    if (tags.has("public") || tags.has("defense") || tags.has("infrastructure") || tags.has("research") || tags.has("repair")) return "公共";
    const ref = String(action.ref || "");
    if (ref.includes("market") || ref.includes("trade") || ref.includes("barter")) return "交易";
    if (ref.includes("gather")) return "采集";
    if (ref.includes("craft")) return "制作";
    if (ref.includes("communicate")) return "社交";
    if (ref.includes("eat") || ref.includes("drink")) return "恢复";
    if (ref.includes("inspect") || ref.includes("repair") || ref.includes("research") || ref.includes("explore") || ref.includes("stock")) return "公共";
    return "其他";
  }


export function actionButtonLabel(category, action) {
    if (category === "移动") return "前往";
    if (category === "采集") return "开始采集";
    if (category === "制作") return "开始制作";
    if (category === "社交") return "交谈";
    if (category === "交易") return "交换";
    if (category === "恢复") return "立即使用";
    if (category === "公共") return "执行";
    return "开始";
  }


export function renderActions() {
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


export function chooseRecommendedAction(actions) {
    return actions.find((action) => actionCategory(action) !== "移动") || actions[0] || null;
  }


export function actionChips(action, limit = 4) {
    const chips = [];
    for (const value of action.cost || []) chips.push(translateCost(value));
    for (const value of (action.impact || []).slice(0, 2)) chips.push(translateImpact(value));
    return [...new Set(chips.filter(Boolean))].slice(0, limit);
  }


export function recommendedActionCard(action) {
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


export function actionRow(action) {
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


export function renderResultBeat() {
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
      : (resultLabel(result) || humanizeKey(result || "completed"));

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
    for (const [key, change] of Object.entries(delta.needs || {})) {
      const diff = Number(change?.after || 0) - Number(change?.before || 0);
      if (Math.abs(diff) < 1) continue;
      chips.push(`${attributeLabel(key)} ${diff > 0 ? "+" : "−"}${Math.abs(Math.round(diff))}`);
    }
    for (const [key, change] of Object.entries(delta.attributes || {})) {
      const diff = Number(change?.after || 0) - Number(change?.before || 0);
      if (Math.abs(diff) < 0.01) continue;
      chips.push(`${attributeLabel(key)} ${diff > 0 ? "+" : "−"}${Math.abs(diff).toFixed(2)}`);
    }
    for (const chip of chips.slice(0, 4)) deltas.append(el("span", "badge", chip));
    if (!chips.length) deltas.append(el("span", "badge", result || "世界已记录"));
  }


export function renderMobileActionBar() {
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


export function attributeLabel(id) {
    const key = String(id || "");
    const attribute = (state.view?.player?.attributes || []).find((item) => String(item.id || "") === key);
    return attribute?.label || humanizeKey(key);
  }


export function humanizeReason(reason) {
    let text = String(reason || "").trim().replace(/^Configured condition:\s*/i, "").replace(/\.\s*$/, "");
    const configuredReasons = uiConfig().reason_labels || {};
    for (const [pattern, label] of Object.entries(configuredReasons)) {
      if (pattern && text.includes(pattern)) return text.replace(pattern, String(label));
    }
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
      const symbol = match[2].includes("<") ? "低于或等于" : "高于";
      return `需要${attributeLabel(match[1])}${symbol} ${match[3]}`;
    }
    match = text.match(/^(?:Actor|actor) inventory ([A-Za-z0-9_ .-]+?) (>=|<=) (-?\d+(?:\.\d+)?)$/);
    if (match) {
      const key = match[1].trim().replace(/\s+/g, "_");
      return `需要 ${itemLabel(key)} ${match[2] === ">=" ? "至少" : "不多于"} ${match[3]} 个`;
    }
    match = text.match(/^(?:Actor|actor) need (\w+) ([<>]=?) (-?\d+(?:\.\d+)?)$/);
    if (match) {
      return `需要${attributeLabel(match[1])}${match[2].includes("<") ? "低于或等于" : "高于"} ${match[3]}`;
    }
    match = text.match(/^actor\.inventory\.(\w+) >= (\d+(?:\.\d+)?)$/);
    if (match) return `需要 ${itemLabel(match[1])} 至少 ${match[2]} 个`;
    const words = [
      [/recipe\.available/, "配方当前不可用"],
      [/inputs\.available/, "随身材料不足"],
      [/station\.available/, "站点当前不可用"],
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


  /* ---------- Action dialog ---------- */

export function ensureActionDialog() {
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


export function openActionDialog(action) {
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
