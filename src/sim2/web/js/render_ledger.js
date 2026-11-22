import { $, el, formatWorldProperty, state, uiConfig } from "./core.js";


function ledgerCard(title, detail, done = false) {
    const card = el("article", `ledger-card${done ? " done" : ""}`);
    card.append(el("strong", "", title));
    if (detail) card.append(el("small", "", detail));
    return card;
}


export function renderLedger() {
    const title = $("ledger-title");
    if (title) title.textContent = uiConfig().ledger_label || "账本";

    const shops = $("ledger-shops");
    if (shops) {
      shops.replaceChildren();
      const facilities = Array.isArray(state.view?.facilities) ? state.view.facilities : [];
      if (!facilities.length) {
        shops.append(ledgerCard("还没有公共设施", "世界会在这里展示店铺与公共账目。"));
      }
      for (const facility of facilities) {
        const values = (facility.properties || [])
          .map((item) => `${item.label} ${formatWorldProperty(item.value, item)}`)
          .join(" · ");
        shops.append(ledgerCard(
          facility.name || facility.id,
          [facility.location, values].filter(Boolean).join(" · "),
          ["good", "stocked", "working", "open", "ready"].includes(String(facility.status || "")),
        ));
      }
    }

    const recipes = $("ledger-recipes");
    if (recipes) {
      recipes.replaceChildren();
      const list = Array.isArray(state.view?.recipes) ? state.view.recipes : [];
      if (!list.length) {
        recipes.append(ledgerCard("还没有配方", "世界会在这里展示可制作的东西。"));
      }
      for (const recipe of list) {
        const inputs = (recipe.inputs || []).map((item) => `${item.label || item.item}×${item.quantity}`).join(" + ");
        const outputs = (recipe.outputs || []).map((item) => `${item.label || item.item}×${item.quantity}`).join(" + ");
        recipes.append(ledgerCard(
          recipe.name || recipe.id,
          [inputs ? `需要 ${inputs}` : "", outputs ? `得到 ${outputs}` : "", `${recipe.duration_minutes || 0} 分钟`].filter(Boolean).join(" · "),
          Boolean(recipe.produced),
        ));
      }
    }

    const goals = $("ledger-goals");
    if (goals) {
      goals.replaceChildren();
      const list = Array.isArray(state.view?.goals) ? state.view.goals : [];
      if (!list.length) {
        goals.append(ledgerCard("还没有公开目标", "世界会在这里展示当前进度。"));
      }
      for (const goal of list.slice(0, 6)) {
        const current = Number(goal.progress?.current || 0);
        const required = Number(goal.progress?.required || 0);
        const progress = required > 0 ? `${current} / ${required}` : (goal.status || "");
        goals.append(ledgerCard(
          goal.label || goal.id,
          [goal.summary || "", progress].filter(Boolean).join(" · "),
          String(goal.status || "") === "completed",
        ));
      }
    }
}


export function openLedger() {
    renderLedger();
    $("ledger-drawer")?.classList.remove("hidden");
}


export function closeLedger() {
    $("ledger-drawer")?.classList.add("hidden");
}
