import { actionForRef, beginAction } from "./actions.js";
import { $, ENDING_KEY, clamp, el, formatMinute, formatWorldProperty, humanizeKey, state, storageGet, storageSet, translateNarrative, uiConfig } from "./core.js";
import { humanizeReason, renderMobileActionBar } from "./render_actions.js";


export function renderOnboarding() {
    const card = $("onboarding-card");
    if (!card) return;
    const step = state.view?.onboarding || null;
    if (!step) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    const eyebrow = $("onboarding-eyebrow");
    if (eyebrow) eyebrow.textContent = uiConfig().one_thing_label || "今天只做一件事";
    $("onboarding-step").textContent = `${step.step || 1}/${step.total || 1}`;
    $("onboarding-title").textContent = step.title || "序章";
    $("onboarding-body").textContent = step.body || "";
    const checklistRoot = $("onboarding-checklist");
    if (checklistRoot) {
      checklistRoot.replaceChildren();
      const checklist = Array.isArray(state.view?.onboarding_checklist) ? state.view.onboarding_checklist : [];
      checklistRoot.classList.toggle("hidden", !checklist.length);
      const progress = state.view?.onboarding_progress || {};
      if (Number(progress.done || 0) > 0) {
        const doneRow = el("div", "onboarding-step done");
        doneRow.append(el("i", "", "✓"));
        doneRow.append(el("span", "", `已完成 ${progress.done} / ${progress.total} 步`));
        checklistRoot.append(doneRow);
      }
      for (const item of checklist) {
        const row = el("div", `onboarding-step ${item.status || "upcoming"}${item.locked ? " locked" : ""}`);
        row.append(el("i", "", item.status === "done" ? "✓" : String(item.step || "")));
        row.append(el("span", "", item.title || item.id || ""));
        checklistRoot.append(row);
      }
    }
    const action = step.action_ref ? actionForRef(step.action_ref) : null;
    const button = $("onboarding-action");
    button.classList.toggle("hidden", !action);
    if (action) {
      button.textContent = `去做：${action.choice || action.name || step.title}`;
      button.onclick = () => beginAction(action, button);
    }
  }


export function renderRecap() {
    const card = $("recap-card");
    if (!card) return;
    const recap = state.view?.recap || {};
    const events = Array.isArray(recap.events) ? recap.events : [];
    const latest = Number(recap.latest_event_id || 0);
    const key = `sim2.game.recap.${state.playerId || "anonymous"}`;
    const seen = Number(storageGet(key, 0) || 0);
    if (!events.length || !latest || latest <= seen) {
      card.classList.add("hidden");
      return;
    }
    if (!seen) {
      // The first visit is onboarding, not a return session.
      storageSet(key, latest);
      card.classList.add("hidden");
      return;
    }
    const prefixes = Array.isArray(uiConfig().recap_event_types) ? uiConfig().recap_event_types : [];
    const interesting = events.filter((item) => {
      const eventType = String(item.event_type || "");
      return !prefixes.length || prefixes.some((prefix) => eventType.startsWith(String(prefix)));
    });
    if (!interesting.length) {
      card.classList.add("hidden");
      return;
    }
    $("recap-title").textContent = uiConfig().recap_label || "最近发生";
    const list = $("recap-list");
    list.replaceChildren();
    for (const item of interesting.slice(-4)) {
      const row = el("div", "recap-row");
      row.append(el("time", "", formatMinute(item.world_minute)));
      row.append(el("span", "", translateNarrative(item.narrative || item.event_type || "")));
      list.append(row);
    }
    card.classList.remove("hidden");
}

export function dismissRecap() {
    const latest = Number(state.view?.recap?.latest_event_id || 0);
    if (latest) storageSet(`sim2.game.recap.${state.playerId || "anonymous"}`, latest);
    $("recap-card")?.classList.add("hidden");
}

export function renderGoals() {
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


export function appendWorldProperty(root, key, value, meta) {
    const label = meta.label || humanizeKey(key);
    const numeric = Number(value);
    let display = formatWorldProperty(value, meta);
    if (meta.max && !["percent", "hours", "bool"].includes(meta.unit)) display += ` / ${meta.max}`;
    const row = el("div", "world-prop");
    if (meta.prompt) row.title = String(meta.prompt);
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


export function renderWorldProperties() {
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


export const FACILITY_STATUS = {
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


export function renderFacilities() {
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


export function renderEnding() {
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


export function closeEnding(markSeen = true) {
    const overlay = $("ending-overlay");
    if (!overlay) return;
    if (markSeen && state.endingKey && state.playerId) {
      storageSet(`${ENDING_KEY}.${state.playerId}`, state.endingKey);
    }
    overlay.classList.add("hidden");
    document.body.classList.remove("ending-open");
  }
