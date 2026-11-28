import { paintAvatar, renderSceneWorldCover } from "./art.js";
import { applyAmbientProfile } from "./audio.js";
import { $, formatMinute, clamp, crisisMetrics, crisisMood, el, formatWorldProperty, humanizeKey, state, toast, uiConfig } from "./core.js";
import { openCharacter } from "./render_panels.js";


export function sceneTheme(location) {
    const tags = new Set(location?.tags || []);
    if (tags.has("sky")) return "sky";
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
    renderSceneWorldCover(theme, location.id);
    if (state.soundOn && state.audio) applyAmbientProfile();
    $("scene-title").textContent = location.name || player.location || "未知地点";
    $("scene-description").textContent = location.description || "这里没有更多描述。";
    $("scene-eyebrow").textContent = player.location_safe ? "安全地点" : "你在这里";
    const tags = $("scene-tags");
    tags.replaceChildren();
    const tagNames = { public: "公共", social: "社交", market: "集市", resource: "资源", craft: "工坊", safe: "安全", research: "研究", exploration: "探索", defense: "防线", infrastructure: "基础设施", home: "住屋", sky: "天空", camp: "营地", woods: "林地", flats: "盐滩", lighthouse: "灯塔", ruins: "遗迹", copper: "矿谷", cliff: "崖地", whalefall: "墓场", faction: "阵营", endgame: "终局" };
    for (const tag of location.tags || []) {
      if (tagNames[tag]) tags.append(el("span", "badge", tagNames[tag]));
    }
    const properties = view.world_properties || {};
    const propertyLabels = view.world_property_labels || {};
    const levels = new Map(crisisMetrics().map((metric) => [metric.key, metric.level]));
    for (const [key, meta] of Object.entries(propertyLabels)) {
      if (!meta?.alert || properties[key] === undefined) continue;
      tags.append(el("span", `badge ${levels.get(key) || ""}`.trim(), `${meta.label || humanizeKey(key)} ${formatWorldProperty(properties[key], meta)}`));
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
    const street = $("street-life");
    const watch = view.city_watch;
    const hasWatch = Boolean(watch && Object.hasOwn(watch, "signals"));
    street.classList.toggle("hidden", !hasWatch);
    people.parentElement.classList.toggle("hidden", hasWatch);
    const streetPeople = $("street-people");
    const extra = $("street-extra-people");
    streetPeople.replaceChildren();
    extra.replaceChildren();
    people.replaceChildren();
    const roster = hasWatch ? streetPeople : people;
    const participants = (view.nearby_participants || []).filter((item) => item.id !== state.playerId);
    if (!participants.length) roster.append(el("span", "scene-empty", "此刻街上无人。可以去别处看看，或等一会儿。"));
    $("street-count").textContent = view.resident_count ? `${view.resident_count} 人在附近` : "";
    $("street-extra").classList.toggle("hidden", !hasWatch || participants.length <= 4);
    $("street-extra-label").textContent = `再看附近 ${Math.max(0, participants.length - 4)} 人`;
    for (const [index, person] of participants.entries()) {
      if (!hasWatch && index >= 8) break;
      const chip = el("button", "person-chip street-person");
      chip.type = "button";
      chip.append(paintAvatar(el("span", "person-avatar"), person));
      const copy = el("span", "person-copy");
      copy.append(el("strong", "", person.name || person.id));
      copy.append(el("span", "", person.active_behavior_label || person.role || "在附近"));
      if (hasWatch && !person.can_talk) copy.append(el("small", "", "忙完再谈"));
      chip.append(copy);
      chip.addEventListener("click", () => openCharacter(person));
      (hasWatch && index >= 4 ? extra : roster).append(chip);
    }
    if (hasWatch) {
      const signals = $("city-signals");
      signals.replaceChildren();
      for (const signal of watch.signals.slice(0, 2)) {
        const line = el("li");
        line.append(el("strong", "", signal.place), el("span", "", signal.text));
        signals.append(line);
      }
      if (!watch.signals.length) signals.append(el("li", "scene-empty", "各处照常开工。"));
      const milestones = $("city-milestone-list");
      milestones.replaceChildren();
      $("city-milestones").classList.toggle("hidden", !watch.milestones.length);
      for (const entry of watch.milestones) {
        const row = el("li");
        const clock = formatMinute(entry.minute);
        row.append(el("small", "", clock), el("strong", "", entry.title));
        row.append(el("p", "", entry.summary));
        if (entry.actor) row.append(el("small", "", entry.actor));
        milestones.append(row);
      }
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
    const metrics = crisisMetrics();
    const root = $("crisis-metrics");
    if (!root) return;
    if (!metrics.length) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    root.replaceChildren();
    for (const { meta, value, label, display, level, hint } of metrics) {
      const numeric = Number(value);
      const card = el("article", `crisis-metric ${level || "good"}`);
      if (hint) card.title = hint;
      card.append(el("span", "crisis-label", label));
      card.append(el("strong", "", display));
      const track = el("div", "crisis-track");
      const fill = el("span");
      const max = Number(meta.max || (meta.unit === "percent" ? 1 : Math.max(1, numeric)));
      fill.style.width = `${clamp((numeric / max) * 100, 0, 100)}%`;
      track.append(fill);
      card.append(track);
      if (hint) card.append(el("small", "", hint));
      root.append(card);
    }
  }
