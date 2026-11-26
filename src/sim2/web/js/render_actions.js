import { beginAction, selectedProvider } from "./actions.js";
import { $, clamp, el, formatMinute, formatWorldProperty, humanizeKey, icon, itemLabel, resultLabel, state, toast, translateCost, translateImpact, translateTime, uiConfig } from "./core.js";


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
    const live = state.advancing ? state.taskProgress : null;
    const completed = Math.max(0, Number(live?.completed_minutes || 0));
    const baseRemaining = Number(task.remaining_minutes || 0);
    const remaining = live ? Math.max(0, baseRemaining - completed) : baseRemaining;
    const total = Math.max(1, Number(task.total_minutes || state.taskTotal || baseRemaining || 1));
    $("task-name").textContent = task.bt || "行动中";
    const provider = selectedProvider();
    const manualHint = state.runtime?.running ? "" : " · 手动模式：点“推进到完成”走完剩余时间";
    const advancingHint = state.advancing ? ` · 同步推进世界${provider ? `（${provider}）` : ""}` : manualHint;
    $("task-detail").textContent = `${task.action || "推进中"} · 还需 ${remaining} 分钟${advancingHint}`;
    const ratio = Number(task.progress);
    const baseProgress = Number.isFinite(ratio) && ratio > 0
      ? ratio
      : (total - baseRemaining) / total;
    const liveProgress = live ? (total - remaining) / total : baseProgress;
    const progress = clamp(Math.max(baseProgress, liveProgress) * 100, 2, 100);
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

export const CATEGORY_ORDER = ["移动", "采集", "制作", "公共", "决策", "交易", "社交", "恢复", "其他"];


export function actionCategory(action) {
    if (action.kind === "travel" || action.ref === "behavior:travel") return "移动";
    const tags = new Set([
      ...(Array.isArray(action.goal_tags) ? action.goal_tags : []),
      ...(Array.isArray(action.drive_tags) ? action.drive_tags : []),
      ...(Array.isArray(action.tags) ? action.tags : []),
    ].map((item) => String(item).toLowerCase()));
    if (tags.has("decision")) return "决策";
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
    if (category === "恢复") return String(action.ref || "").includes("draw") ? "领取" : "立即使用";
    if (category === "公共") return "执行";
    if (category === "决策") return "决定";
    return "开始";
  }


export function activeOpportunity() {
    const view = state.view;
    if (view?.active_task?.bt || view?.active_task?.action) return null;
    const spotlights = uiConfig().spotlights || {};
    const actions = new Map((view?.actions || []).map((action) => [action.ref, action]));
    for (const goal of view?.goals || []) {
      if (!Object.hasOwn(spotlights, goal.id)) continue;
      const choices = (goal.ready_behavior_refs || []).map((ref) => actions.get(ref)).filter(Boolean);
      if (choices.length) return { goal, choices, presentation: spotlights[goal.id] || {} };
    }
    return null;
}


function renderOpportunity(opportunity) {
    const panel = $("opportunity-panel");
    panel.classList.toggle("hidden", !opportunity);
    const list = $("opportunity-choices");
    list.replaceChildren();
    if (!opportunity) return;
    const { goal, choices, presentation } = opportunity;
    $("opportunity-title").textContent = goal.label;
    $("opportunity-brief").textContent = goal.summary;
    const deadline = $("opportunity-deadline");
    const minutes = presentation.deadline_property
      ? state.view.world_properties?.[presentation.deadline_property]
      : presentation.deadline_clock_minute - state.view.world_time?.minute_of_day;
    const hasDeadline = Number.isFinite(minutes) && minutes > 0;
    deadline.classList.toggle("hidden", !hasDeadline);
    deadline.textContent = hasDeadline ? `剩余 ${Math.ceil(minutes)} 分钟` : "";
    for (const action of choices) {
      const button = el("button", "opportunity-choice");
      button.type = "button";
      const head = el("span", "opportunity-choice-head");
      head.append(el("strong", "", action.choice || action.name || "行动"));
      head.append(el("span", "opportunity-arrow", "→"));
      head.lastChild.setAttribute("aria-hidden", "true");
      button.append(head);
      button.append(el("span", "opportunity-choice-detail", action.summary || action.purpose || ""));
      button.append(el("small", "", translateTime(action.time || "时间未知")));
      button.addEventListener("click", () => beginAction(action, button));
      list.append(button);
    }
}


export function renderActions() {
    const view = state.view;
    const actions = Array.isArray(view?.actions) ? view.actions : [];
    const opportunity = activeOpportunity();
    renderOpportunity(opportunity);
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

    const genericTravel = actions.find((action) => action.ref === "behavior:travel" && action.kind !== "travel");
    const filtered = actions
      .filter((action) => state.actionFilter === "全部" || actionCategory(action) === state.actionFilter)
      // Route cards are useful as direct recommendations, but nine separate
      // destination cards bury shops and work. Keep the generic travel card
      // with its destination dialog when the view offers one.
      .filter((action) => !(genericTravel && action.kind === "travel"));
    const taskActive = Boolean(view?.active_task?.bt || view?.active_task?.action);
    const featured = !taskActive && !opportunity && filtered.length ? chooseRecommendedAction(filtered) : null;
    const recommended = $("recommended-action");
    recommended.replaceChildren();
    recommended.classList.toggle("hidden", !featured);
    if (featured) recommended.append(recommendedActionCard(featured));

    const grid = $("action-grid");
    grid.replaceChildren();
    const rest = filtered.filter((action) => action !== featured && !opportunity?.choices.includes(action));
    if (!actions.length) {
      grid.append(el("div", "inventory-empty", "现在没有平台认可的行动；可以在地图上换一个地点。"));
    } else if (!rest.length && !featured) {
      grid.append(el("div", "inventory-empty", opportunity && filtered.length
        ? "这个分类的行动在上方抉择中。"
        : "这个分类下暂时没有可执行行动。"));
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
    if (!Array.isArray(actions) || !actions.length) return null;
    const view = state.view || {};
    const onboardingRef = String(view.onboarding?.action_ref || "");
    const desiredCategory = onboardingRef ? actionCategory({ ref: onboardingRef }) : "";
    if (onboardingRef) {
      const onboarding = actions.find((action) => action.ref === onboardingRef);
      if (onboarding) return onboarding;
      const sameCategory = actions.find((action) => actionCategory(action) === desiredCategory);
      if (sameCategory) return sameCategory;
    }
    const goals = Array.isArray(view.goals) ? view.goals : [];
    const readyRefs = new Set(
      goals.flatMap((goal) => Array.isArray(goal.ready_behavior_refs) ? goal.ready_behavior_refs : []),
    );
    const ready = actions.find((action) => readyRefs.has(action.ref));
    if (ready) return ready;

    const needs = view.player?.needs || {};
    const need = (id) => Number(needs[id] ?? 100);
    const attributes = Array.isArray(view.player?.attributes) ? view.player.attributes : [];
    const attribute = (id) => {
      const item = attributes.find((entry) => String(entry.id || "") === id);
      return Number(item?.value ?? item?.current ?? 100);
    };
    const urgent = need("hydration") < 30
      || need("satiety") < 30
      || attribute("stamina") < 25
      || attribute("energy") < 25
      || attribute("integrity") < 30;
    if (urgent) {
      const recovery = actions.find((action) => actionCategory(action) === "恢复");
      if (recovery) return recovery;
    }

    const travel = actions.find((action) => actionCategory(action) === "移动");
    if (desiredCategory && desiredCategory !== "其他") {
      return travel || actions[0] || null;
    }

    // A consumable is not a good default when nothing is low: it spends time
    // without advancing a shop, the fair or the current onboarding step.
    const productive = actions.find((action) => !["移动", "恢复"].includes(actionCategory(action)));
    if (productive) return productive;
    return travel || actions[0] || null;
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
    if (state.view?.onboarding?.action_ref === action.ref) {
      meta.append(el("span", "badge accent", "上手目标"));
    }
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
      ? `行动未完成${result ? ` · ${resultLabel(result)}` : ""}`
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
  }


export function renderMobileActionBar() {
    const bar = $("mobile-action-bar");
    if (!bar) return;
    const task = state.view?.active_task || {};
    const taskActive = Boolean(task.bt || task.action);
    const opportunity = activeOpportunity();
    const actions = Array.isArray(state.view?.actions) ? state.view.actions : [];
    const action = taskActive || opportunity ? null : chooseRecommendedAction(actions);
    bar._action = action;
    bar._opportunity = Boolean(opportunity);
    if (!taskActive && !action && !opportunity) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");
    if (taskActive) {
      $("mobile-primary-label").textContent = `推进：${task.bt || task.action || "当前行动"}`;
      $("mobile-primary-meta").textContent = `还需 ${Number(task.remaining_minutes || 0)} 分钟`;
    } else if (opportunity) {
      $("mobile-primary-label").textContent = opportunity.goal.label;
      $("mobile-primary-meta").textContent = "查看抉择";
    } else {
      $("mobile-primary-label").textContent = action.choice || action.name || "下一步";
      $("mobile-primary-meta").textContent = `${actionCategory(action)} · ${translateTime(action.time || "时间未知")}`;
    }
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
    let match = text.match(/^(?:Location tag|location\.tag\.)\s*([A-Za-z0-9_-]+)\.?$/i);
    if (match) {
      const tag = match[1].toLowerCase();
      const labels = {
        resource: "资源点",
        shop: "店铺",
        craft: "工坊或店铺",
        safe: "安全地点",
        water: "水源",
        market: "集市",
        social: "社交场所",
        home: "住屋",
        defense: "防线",
        infrastructure: "公共设施",
        research: "研究点",
      };
      return `需要到达${labels[tag] || humanizeKey(tag)}`;
    }
    match = text.match(/^cooldown until world minute (\d+)$/i);
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

    let missingChoice = false;
    for (const spec of Array.isArray(action.parameters) ? action.parameters : []) {
      const match = String(spec).match(/^(\w+)\s*\(([^)]+)\):\s*(.*)$/);
      if (!match) continue;
      const [, name, kind, description] = match;
      if (Array.isArray(action.parameter_options?.[name])) {
        const options = action.parameter_options[name];
        if (!options.length) {
          missingChoice = true;
          fields.append(el("p", "hint", "当前地点没有可用选项。"));
          continue;
        }
        const parameterLabels = { location_id: "目的地", entry_id: "选择", recipe_id: "配方", item_id: "物品", source_id: "研究来源" };
        const label = el("label", "", parameterLabels[name] || name);
        const select = document.createElement("select");
        select.dataset.param = name;
        for (const option of options) {
          const value = option.value || option.name || "";
          select.append(new Option(option.name || value || "选项", value));
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
      if (missingChoice) {
        toast("当前地点没有可用选项。", "error");
        return;
      }
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
