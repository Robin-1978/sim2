import { renderSceneWorldCover } from "./art.js";
import { applyAmbientProfile } from "./audio.js";
import { $, clamp, crisisMetrics, crisisMood, el, formatWorldProperty, humanizeKey, state, toast, uiConfig } from "./core.js";
import { nearbyParticipants, openCharacter } from "./render_panels.js";


export function sceneTheme(location) {
    const tags = new Set(location?.tags || []);
    if (tags.has("craft")) return "workshop";
    if (tags.has("research")) return "observatory";
    if (tags.has("resource") && tags.has("exploration")) return "marsh";
    if (tags.has("defense") || tags.has("infrastructure")) return "gatehouse";
    if (tags.has("home")) return "lantern";
    if (tags.has("market") || tags.has("social")) return "quay";
    return "harbor";
  }


export function renderSceneAlert() {
    const alert = $("scene-alert");
    const art = $("scene-art");
    if (!alert || !art) return;
    const metrics = crisisMetrics();
    art.dataset.crisis = crisisMood();
    if (!metrics.length) {
      alert.classList.add("hidden");
      return;
    }
    const urgent = metrics.find((metric) => metric.level === "danger")
      || metrics.find((metric) => metric.level === "warning")
      || metrics[0];
    const countdown = metrics.find((metric) => metric.key !== urgent.key && metric.meta?.unit === "hours");
    const chapter = state.view?.chapter || {};
    $("scene-alert-eyebrow").textContent = chapter.title || "当前态势";
    $("scene-alert-title").textContent = `${urgent.label} ${urgent.display}`;
    const lines = [urgent.hint];
    if (countdown) lines.push(`${countdown.label} ${countdown.display}`);
    $("scene-alert-hint").textContent = lines.filter(Boolean).join(" · ");
    alert.classList.remove("hidden");
  }


export function renderCountdown() {
    const root = $("scene-countdown");
    if (!root) return;
    const config = uiConfig().countdown;
    const property = String(config?.property || "");
    const value = property ? state.view?.world_properties?.[property] : undefined;
    if (!config || !property || value === undefined) {
      root.classList.add("hidden");
      return;
    }
    const meta = {
      ...(state.view?.world_property_labels?.[property] || {}),
      ...config,
      label: config.label || state.view?.world_property_labels?.[property]?.label || humanizeKey(property),
    };
    root.replaceChildren();
    root.append(el("span", "", meta.label));
    root.append(el("strong", "", `${formatWorldProperty(value, meta)}${config.suffix || ""}`));
    if (config.hint) root.append(el("span", "hint", config.hint));
    root.classList.remove("hidden");
}


export function renderScene() {
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
      const active = person.status === "executing" || person.status === "paused";
      copy.append(el("strong", "", person.name || person.id));
      copy.append(el("span", "", active ? (person.active_behavior_label || "正在忙碌") : "可以交谈"));
      if (active) {
        const progress = Number(person.initial_state?.action_progress);
        if (Number.isFinite(progress)) {
          const track = el("span", "person-mini-track");
          const fill = el("i");
          fill.style.width = `${Math.max(0, Math.min(100, progress * 100))}%`;
          track.append(fill);
          copy.append(track);
        }
      }
      chip.classList.toggle("person-busy", active);
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
    renderSceneAlert();
    renderCountdown();
  }


export function renderCrisisStrip() {
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
