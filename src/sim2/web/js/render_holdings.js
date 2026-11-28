import { advanceTask, beginAction, showGameScreen } from "./actions.js";
import { $, el, formatWorldProperty, state, translateTime } from "./core.js";


function valueFor(spec, view) {
    return spec.item ? view.inventory?.[spec.item] : view.world_properties?.[spec.property];
}


function metrics(container, definitions, view) {
    container.replaceChildren();
    for (const spec of definitions || []) {
        const value = valueFor(spec, view) ?? 0;
        if (spec.when_nonzero && !value) continue;
        const pair = el("div", "venture-metric");
        pair.append(el("dt", "", spec.label), el("dd", "", `${formatWorldProperty(value, spec)}${spec.suffix || ""}`));
        container.append(pair);
    }
}


function actionList(specs, view, active) {
    const list = el("div", "holding-actions");
    const props = view.world_properties || {};
    for (const spec of specs || []) {
        if (spec.show_when && !props[spec.show_when]) continue;
        if (spec.hide_when && props[spec.hide_when]) continue;
        if (spec.when_item && !view.inventory?.[spec.when_item]) continue;
        const ref = `behavior:${spec.id}`;
        const action = (view.actions || []).find((row) => row.ref === ref);
        const hint = (view.unavailable_actions || []).find((row) => row.ref === ref)?.reasons?.[0];
        const button = el("button", "holding-choice");
        button.type = "button";
        button.disabled = Boolean(!action || state.busy || active);
        button.append(el("strong", "", action?.choice || spec.label));
        if (action?.summary) button.append(el("span", "holding-choice-copy", action.summary));
        else if (hint && !active) button.append(el("span", "holding-choice-copy", hint));
        if (action?.time) button.append(el("small", "", translateTime(action.time)));
        if (action) button.addEventListener("click", () => beginAction(action, button).finally(renderHoldings));
        list.append(button);
    }
    return list;
}


function updateTask(view) {
    const task = view.active_task || {};
    const active = Boolean(task.bt || task.action);
    const panel = $("holdings-task");
    panel.replaceChildren();
    panel.classList.toggle("hidden", !active && !state.busy);
    if (active || state.busy) {
        const progress = task.remaining_minutes > 0 ? ` · 还需约 ${task.remaining_minutes} 分钟` : "";
        panel.append(el("strong", "", `${task.action || "正在结算行动"}${progress}`));
        if (active && !state.busy && !state.advancing && !state.runtime?.running) {
            const next = el("button", "button primary", "推进到完成");
            next.type = "button";
            next.addEventListener("click", () => advanceTask("继续：").finally(renderHoldings));
            panel.append(next);
        }
    }
    return active;
}


export function renderHoldings() {
    const view = state.view;
    const config = view?.ui?.holdings;
    $("holdings-nav").classList.toggle("hidden", !config);
    $("open-holdings").classList.toggle("hidden", !config);
    if (!config) {
        if ($("game").dataset.screen === "holdings") showGameScreen("scene");
        return;
    }
    if ($("game").dataset.screen !== "holdings") return;
    $("holdings-title").textContent = config.title;
    $("holdings-summary").textContent = config.summary;
    metrics($("holdings-totals"), config.metrics, view);
    const active = updateTask(view);
    const treasury = $("holdings-treasury");
    const plots = config.plots || [];
    const accountOpen = treasury.querySelector("details")?.open;
    treasury.replaceChildren();
    if (plots.some((plot) => view.world_properties?.[plot.opened])) {
        const account = el("details", "holding-account");
        account.open = Boolean(accountOpen);
        account.append(el("summary", "", "存取经营金"), actionList(config.treasury_actions, view, active));
        treasury.append(account);
    }
    const grid = $("holdings-grid");
    grid.replaceChildren();
    const map = new Map((view.map?.nodes || []).map((node) => [node.id, node.name]));
    for (const plot of plots) {
        const isOpen = Boolean(view.world_properties?.[plot.opened]);
        const managed = isOpen && view.world_properties?.[plot.management?.property];
        const card = el("article", "panel holding-plot");
        card.dataset.plot = plot.id;
        const header = el("header", "holding-plot-head");
        header.append(el("span", "holding-place", map.get(plot.location) || ""));
        header.append(el("h2", "", plot.name));
        header.append(el("span", "holding-status", managed ? plot.management.label : isOpen ? "经营中" : `开办需 ${plot.cost} 枚`));
        card.append(header, el("p", "holding-description", plot.description));
        if (managed) card.append(el("p", "holding-upgrade", plot.management.detail));
        if (plot.upgrade && view.world_properties?.[plot.upgrade]) {
            card.append(el("p", "holding-upgrade", plot.upgrade_label));
        }
        if (isOpen) {
            const stats = el("dl", "venture-metrics holding-metrics");
            metrics(stats, plot.metrics, view);
            card.append(stats);
        }
        card.append(actionList(isOpen ? plot.actions : [plot.build], view, active));
        grid.append(card);
    }
}
