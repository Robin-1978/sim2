import { request, write } from "./admin_api.js";
import { init } from "./admin_app.js";
import { $, ensureOperator, make, state, withPending } from "./admin_core.js";


export function defaultTemplate() {
    return { template_version: "draft-1", world: { id: "new_world", title: "", premise: "", locale: "zh-CN", properties: {} }, attributes: [], locations: [], resources: [], objects: [], factions: [], agents: [], capabilities: [], actions: [], behavior_trees: [], recipes: [], relationships: [], rules: {}, evolution: {}, story: {}, player_template: {}, initial_events: [], prompts: {} };
  }


export const listValue = (value) => (Array.isArray(value) ? value : []);

export const cloneValue = (value) => JSON.parse(JSON.stringify(value));

export const listText = (value) => listValue(value).map((item) => String(item)).join("，");

export const parseListText = (text) => String(text || "").split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);


export function uniqueId(items, base) {
    const used = new Set(listValue(items).map((item) => String(item && item.id ? item.id : "")));
    const wanted = String(base || "new_item");
    if (!used.has(wanted)) return wanted;
    let index = 2;
    while (used.has(`${wanted}_${index}`)) index += 1;
    return `${wanted}_${index}`;
  }


export const COLLECTION_GUIDE = {
    locations: {
      label: "地点",
      hint: "角色会在这里移动、采集和相遇。至少写两个地点，并用路线连起来。",
      emptyHint: "先加一个入口地点，再加一个可以前往的地方。",
      addLabel: "添加地点",
      fields: [
        { key: "name", label: "名字", placeholder: "例如：河桥" },
        { key: "id", label: "ID", mono: true, hint: "英文或拼音，规则用它引用" },
        { key: "routes", label: "通往", type: "list", options: (template) => listValue(template.locations).map((item) => item.id), hint: "填其他地点的 ID，用逗号分隔" },
        { key: "tags", label: "标签", type: "list", hint: "例如：入口，河边" },
        { key: "public", label: "公开地点", type: "bool" },
        { key: "safe", label: "安全地点", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.locations, "new_location"), name: "新地点", public: true, safe: false, tags: [], routes: [] }),
      title: (item, index) => item.name || item.id || `地点 ${index + 1}`,
      meta: (item) => (listValue(item.routes).length ? `${listValue(item.routes).length} 条路线` : "还没有路线"),
    },
    agents: {
      label: "人物",
      hint: "居民和 NPC。名字、身份和目标决定他们怎样行动。",
      emptyHint: "世界需要有人行动。先加一位居民。",
      addLabel: "添加人物",
      fields: [
        { key: "name", label: "名字", placeholder: "例如：林泊" },
        { key: "id", label: "ID", mono: true, hint: "英文或拼音，对话和事件用它引用" },
        { key: "role", label: "身份", placeholder: "例如：村里的守望者" },
        { key: "ultimate_goal", label: "长期目标", type: "textarea", wide: true, placeholder: "这个人最想守住或改变什么？" },
        { key: "goal_tags", label: "目标标签", type: "list", hint: "用于世界趋势统计，可留空" },
        { key: "faction_ids", label: "所属组织", type: "list", options: (template) => listValue(template.factions).map((item) => item.id), hint: "填组织 ID，用逗号分隔" },
      ],
      create: (template) => ({
        id: uniqueId(template.agents, "new_resident"),
        name: "新居民",
        role: "resident",
        ultimate_goal: "",
        traits: {},
        beliefs: [],
        needs: {},
        capabilities: {},
        initial_bt_ids: listValue(template.behavior_trees).length ? [template.behavior_trees[0].id] : [],
        initial_state: {},
        inventory: {},
      }),
      title: (item, index) => item.name || item.id || `人物 ${index + 1}`,
      meta: (item) => item.role || "居民",
    },
    objects: {
      label: "物品",
      hint: "可以被找到、修复或使用的物件。",
      emptyHint: "没有物品也可以玩；加一件会让世界更具体。",
      addLabel: "添加物品",
      fields: [
        { key: "name", label: "名字", placeholder: "例如：旧钥匙" },
        { key: "id", label: "ID", mono: true },
        { key: "type", label: "类型", placeholder: "例如：artifact" },
        { key: "location_id", label: "所在地点", options: (template) => listValue(template.locations).map((item) => item.id) },
        { key: "tags", label: "标签", type: "list" },
        { key: "public", label: "公开可见", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.objects, "new_object"), type: "artifact", name: "新物品", public: true, tags: [], initial_state: {} }),
      title: (item, index) => item.name || item.id || `物品 ${index + 1}`,
      meta: (item) => item.type || "",
    },
    resources: {
      label: "公共资源",
      hint: "村庄、据点或世界的共享库存。",
      emptyHint: "资源用于公共储备、短缺和交易。",
      addLabel: "添加资源",
      fields: [
        { key: "id", label: "资源 ID", mono: true },
        { key: "type", label: "类型", placeholder: "例如：shared_stock" },
        { key: "location_id", label: "所在地点", options: (template) => listValue(template.locations).map((item) => item.id) },
        { key: "amount", label: "初始数量", type: "number" },
      ],
      create: (template) => ({ id: uniqueId(template.resources, "new_resource"), type: "shared_stock", location_id: listValue(template.locations)[0]?.id || "", amount: 100 }),
      title: (item, index) => item.id || `资源 ${index + 1}`,
      meta: (item) => [item.amount !== undefined ? `${item.amount}` : "", item.location_id].filter((value) => value !== undefined && value !== "").join(" · "),
    },
    factions: {
      label: "组织",
      hint: "有名字、领袖和规矩的群体。",
      emptyHint: "组织会让角色之间的关系更有结构。",
      addLabel: "添加组织",
      fields: [
        { key: "name", label: "名字" },
        { key: "id", label: "ID", mono: true },
        { key: "leader_id", label: "领袖", options: (template) => listValue(template.agents).map((item) => item.id) },
        { key: "norms", label: "规矩", type: "list", hint: "用逗号分隔" },
        { key: "public", label: "公开组织", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.factions, "new_faction"), name: "新组织", public: true, leader_id: "", norms: [] }),
      title: (item, index) => item.name || item.id || `组织 ${index + 1}`,
      meta: (item) => (listValue(item.norms).length ? `${listValue(item.norms).length} 条规矩` : ""),
    },
    actions: {
      label: "行动",
      hint: "角色能主动做的事。新手建议复制一个现有行动再修改，避免漏掉必填字段。",
      emptyHint: "当前模板还没有行动。从已验证模板复制一个世界会自带基础行动。",
      addLabel: "复制一个行动",
      clone: true,
      fields: [
        { key: "description", label: "描述", type: "textarea", wide: true, placeholder: "玩家会看到的一句话" },
        { key: "id", label: "ID", mono: true },
        { key: "preconditions", label: "前置条件", type: "list", hint: "例如：actor.mobile" },
      ],
      create: (template) => {
        const last = listValue(template.actions).slice(-1)[0];
        if (!last) return null;
        return { ...cloneValue(last), id: uniqueId(template.actions, `${last.id || "action"}_copy`) };
      },
      title: (item, index) => item.description || item.id || `行动 ${index + 1}`,
      meta: () => "",
    },
    recipes: {
      label: "配方",
      hint: "把材料变成物品。输入、输出和时长决定制作节奏。",
      emptyHint: "当前模板还没有配方。复制一个现有配方最容易上手。",
      addLabel: "复制一个配方",
      clone: true,
      fields: [
        { key: "name", label: "名字" },
        { key: "id", label: "ID", mono: true },
        { key: "duration_minutes", label: "制作分钟", type: "number" },
        { key: "station_tags", label: "需要的工作台", type: "list", hint: "例如：kitchen，fireplace" },
      ],
      create: (template) => {
        const last = listValue(template.recipes).slice(-1)[0];
        if (!last) return null;
        return { ...cloneValue(last), id: uniqueId(template.recipes, `${last.id || "recipe"}_copy`) };
      },
      title: (item, index) => item.name || item.id || `配方 ${index + 1}`,
      meta: (item) => (item.duration_minutes ? `${item.duration_minutes} 分钟` : ""),
    },
    behavior_trees: {
      label: "行为树",
      hint: "NPC 自动行动的步骤。新手保留模板自带的结构，只改名字和预计时长。",
      emptyHint: "当前模板还没有行为树。建议从已验证模板复制一个世界。",
      addLabel: "复制一棵行为树",
      clone: true,
      fields: [
        { key: "summary", label: "摘要", type: "textarea", wide: true },
        { key: "id", label: "ID", mono: true },
        { key: "estimated_minutes", label: "预计分钟", type: "number" },
        { key: "entry_conditions", label: "进入条件", type: "list" },
      ],
      create: (template) => {
        const last = listValue(template.behavior_trees).slice(-1)[0];
        if (!last) return null;
        return { ...cloneValue(last), id: uniqueId(template.behavior_trees, `${last.id || "behavior"}_copy`) };
      },
      title: (item, index) => item.summary || item.id || `行为树 ${index + 1}`,
      meta: () => "",
    },
    capabilities: {
      label: "能力",
      hint: "可以随行动增长的能力值。",
      emptyHint: "能力用于制作、研究和采集的成功率。",
      addLabel: "添加能力",
      fields: [
        { key: "name", label: "名字" },
        { key: "id", label: "ID", mono: true },
        { key: "uses", label: "用于哪些行动", type: "list", options: (template) => listValue(template.actions).map((item) => item.id) },
        { key: "xp_rule", label: "经验规则", placeholder: "例如：one_successful_recipe" },
      ],
      create: (template) => ({ id: uniqueId(template.capabilities, "new_capability"), name: "新能力", value_path: `agent.capabilities.new_capability`, uses: [], xp_rule: "one_successful_action" }),
      title: (item, index) => item.name || item.id || `能力 ${index + 1}`,
      meta: (item) => (listValue(item.uses).length ? `${listValue(item.uses).length} 个行动` : ""),
    },
    attributes: {
      label: "属性",
      hint: "生命、精力等可变化的数值。",
      emptyHint: "属性定义角色的状态上限和初始值。",
      addLabel: "添加属性",
      fields: [
        { key: "label", label: "显示名" },
        { key: "id", label: "ID", mono: true },
        { key: "kind", label: "类型", placeholder: "state 或 need" },
        { key: "min", label: "最小值", type: "number" },
        { key: "max", label: "最大值", type: "number" },
        { key: "default", label: "初始值", type: "number" },
        { key: "prompt", label: "说明", type: "textarea", wide: true, hint: "给模型看的含义说明" },
        { key: "vital", label: "关键属性", type: "bool" },
      ],
      create: (template) => ({ id: uniqueId(template.attributes, "new_attribute"), kind: "state", label: "新属性", unit: "points", min: 0, max: 100, default: 50, prompt: "" }),
      title: (item, index) => item.label || item.id || `属性 ${index + 1}`,
      meta: (item) => item.kind || "",
    },
    relationships: {
      label: "初始关系",
      hint: "角色之间一开始就存在的信任或敌意。",
      emptyHint: "关系会影响对话、交易和结盟。",
      addLabel: "添加关系",
      fields: [
        { key: "from", label: "从", options: (template) => listValue(template.agents).map((item) => item.id) },
        { key: "to", label: "到", options: (template) => listValue(template.agents).map((item) => item.id) },
        { key: "type", label: "关系", placeholder: "例如：trust" },
        { key: "value", label: "数值", type: "number" },
      ],
      create: (template) => ({ from: listValue(template.agents)[0]?.id || "", to: listValue(template.agents)[1]?.id || "", type: "trust", value: 0.5 }),
      title: (item, index) => [item.from, item.to].filter(Boolean).join(" → ") || `关系 ${index + 1}`,
      meta: (item) => [item.type, item.value].filter((value) => value !== undefined && value !== "").join(" · "),
    },
    initial_events: {
      label: "开局事件",
      hint: "世界开始时发生的公开事件。",
      emptyHint: "开局事件会写进世界日志，适合交代背景。",
      addLabel: "添加事件",
      fields: [
        { key: "id", label: "ID", mono: true },
        { key: "type", label: "事件类型", placeholder: "例如：story.note" },
        { key: "world_minute", label: "第几分钟", type: "number" },
      ],
      create: (template) => ({ id: uniqueId(template.initial_events, "new_event"), type: "story.note", world_minute: 0, payload: {} }),
      title: (item, index) => item.type || item.id || `事件 ${index + 1}`,
      meta: (item) => (item.world_minute !== undefined ? `第 ${item.world_minute} 分钟` : ""),
    },
  };


export const WRITE_SECTIONS = [

    { id: "world", no: "01", title: "世界概览", hint: "玩家第一眼看到的内容。先写清楚这里是什么地方。", collections: [] },
    { id: "home", no: "02", title: "地点", hint: "角色会在这里移动、采集和相遇。至少两个地点，并用路线连起来。", collections: ["locations"] },
    { id: "people", no: "03", title: "人物", hint: "居民和 NPC。名字、身份和目标决定他们怎样行动。", collections: ["agents"] },
    { id: "things", no: "04", title: "物品与组织", hint: "让世界更具体的物件、公共资源和群体。全部可选。", collections: ["objects", "resources", "factions"] },
    { id: "verbs", no: "05", title: "行动与配方", hint: "玩家和 NPC 能主动做的事。复制现有条目最稳妥。", collections: ["actions", "recipes"] },
  ];
export const EXTRA_COLLECTIONS = ["behavior_trees", "capabilities", "attributes", "relationships", "initial_events"];

export const OBJECT_SECTIONS = ["rules", "evolution", "story", "prompts"];

export const OBJECT_LABELS = { rules: "世界规则", evolution: "演化节奏", story: "故事生成", prompts: "提示词" };


export function setDraft(value, { increment = false, sourcePath = undefined, clearSource = false } = {}) {
    state.template = cloneValue(value || defaultTemplate());
    state.expandedCollections = new Set();
    state.openItem = null;
    if (clearSource) state.sourcePath = null;
    else if (sourcePath !== undefined) state.sourcePath = sourcePath;
    $("template-editor").value = JSON.stringify(state.template, null, 2);
    if (increment) state.draftRevision += 1;
    $("draft-revision").textContent = `revision ${state.draftRevision}`;
    $("authoring-source").textContent = state.sourcePath ? `来源：${state.sourcePath}` : "未保存的草稿";
    renderGuidedEditor();
    for (const field of [$("publish-output"), $("publish-instance")]) {
      if (field) delete field.dataset.touched;
    }
    fillPublishDefaults(true);
    document.querySelector(".write-rail")?.scrollTo({ top: 0 });
  }


export function worldField(key, labelText, kind, options = {}) {
    const label = make("label", `write-field${options.wide ? " wide" : ""}`);
    label.append(make("span", "write-field-label", labelText));
    const input = kind === "textarea" ? document.createElement("textarea") : document.createElement("input");
    if (kind === "number") input.type = "number";
    if (options.mono) input.classList.add("mono");
    input.value = state.template.world[key] ?? "";
    if (options.placeholder) input.placeholder = options.placeholder;
    input.addEventListener("input", () => {
      state.template.world[key] = kind === "number" ? Number(input.value || 0) : input.value;
      markDraftDirty();
    });
    label.append(input);
    if (options.hint) label.append(make("small", "write-field-hint", options.hint));
    return label;
  }


export function renderWorldSection() {
    const template = state.template;
    template.world = template.world && typeof template.world === "object" ? template.world : {};
    const section = make("section", "write-section write-world");
    section.id = "write-world";
    section.append(writeSectionHeader("01", "世界概览", "玩家进入世界时最先看到的内容。先写清楚这里是什么地方。"));

    const card = make("div", "write-card");
    const grid = make("div", "write-world-grid");
    grid.append(worldField("title", "世界名字", "input", { wide: true, placeholder: "给你的世界起个名字" }));
    grid.append(worldField("premise", "一句话设定", "textarea", { wide: true, placeholder: "这里的人为什么需要彼此？他们正面临什么？" }));
    grid.append(worldField("id", "世界 ID", "input", { mono: true, placeholder: "my_world", hint: "英文或下划线；决定默认的包目录名" }));
    grid.append(worldField("locale", "语言", "input", { placeholder: "zh-CN" }));
    card.append(grid);

    const more = document.createElement("details");
    more.className = "write-inline-more";
    more.append(make("summary", "", "更多世界设置"));
    const moreGrid = make("div", "write-world-grid");
    moreGrid.append(worldField("genre", "类型", "input", { placeholder: "自定义类型，可留空" }));
    moreGrid.append(worldField("start_world_minute", "开局分钟", "number", { placeholder: "0" }));
    moreGrid.append(worldField("time_unit", "时间单位", "input", { placeholder: "minute" }));
    more.append(moreGrid);
    card.append(more);
    section.append(card);
    section.append(renderPlayerEntry());
    return section;
  }


export function renderPlayerEntry() {
    const template = state.template;
    template.player_template = template.player_template && typeof template.player_template === "object" ? template.player_template : {};
    const player = template.player_template;
    const wrap = make("div", "write-card write-entry-card");
    wrap.id = "write-player-entry";
    const head = make("div", "write-entry-head");
    head.append(make("span", "eyebrow", "玩家入口"));
    head.append(make("strong", "", "玩家从哪开始"));
    head.append(make("p", "hint", "入口地点决定玩家第一次出现的位置；出身决定可选的开局目标。"));
    wrap.append(head);

    const grid = make("div", "write-world-grid");
    const selectLabel = make("label", "write-field");
    selectLabel.append(make("span", "write-field-label", "入口地点"));
    const select = document.createElement("select");
    select.append(new Option("选择地点", ""));
    for (const location of listValue(template.locations)) select.append(new Option(location.name || location.id, location.id));
    select.value = player.entry_location_id || "";
    select.disabled = !listValue(template.locations).length;
    select.addEventListener("change", () => {
      player.entry_location_id = select.value;
      markDraftDirty();
    });
    selectLabel.append(select);
    if (!listValue(template.locations).length) selectLabel.append(make("small", "write-field-hint", "先添加地点，才能设置入口。"));
    grid.append(selectLabel);
    wrap.append(grid);

    const origins = Array.isArray(player.origins) ? player.origins : [];
    const list = make("div", "write-origin-list");
    origins.forEach((origin, index) => {
      const row = make("div", "write-origin");
      const idInput = document.createElement("input");
      idInput.value = origin.id || "";
      idInput.placeholder = "出身 ID";
      idInput.addEventListener("input", () => { origin.id = idInput.value; markDraftDirty(); });
      const goalInput = document.createElement("input");
      goalInput.value = origin.starting_goal || "";
      goalInput.placeholder = "开局目标，例如：找到一位愿意收徒的工匠";
      goalInput.addEventListener("input", () => { origin.starting_goal = goalInput.value; markDraftDirty(); });
      const remove = make("button", "text-button", "移除");
      remove.type = "button";
      remove.addEventListener("click", () => {
        origins.splice(index, 1);
        markDraftDirty();
        renderGuidedEditor();
      });
      row.append(idInput, goalInput, remove);
      list.append(row);
    });
    if (!origins.length) list.append(make("p", "write-empty-note", "还没有出身。至少添加一个，玩家才能进入世界。"));
    wrap.append(list);

    const add = make("button", "button ghost small", "添加出身");
    add.type = "button";
    add.addEventListener("click", () => {
      if (!Array.isArray(player.origins)) player.origins = [];
      player.origins.push({ id: uniqueId(player.origins, "traveler"), traits: {}, starting_goal: "" });
      markDraftDirty();
      renderGuidedEditor();
      document.getElementById("write-player-entry")?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    wrap.append(add);
    return wrap;
  }


export function writeSectionHeader(no, title, hint) {
    const head = make("div", "write-section-head");
    head.append(make("span", "write-section-no", no));
    const copy = make("div", "write-section-copy");
    copy.append(make("h3", "", title));
    copy.append(make("p", "hint", hint));
    head.append(copy);
    return head;
  }


export function renderCollectionSection(meta) {
    const section = make("section", "write-section");
    section.id = `write-section-${meta.id}`;
    section.append(writeSectionHeader(meta.no, meta.title, meta.hint));
    for (const collection of meta.collections) section.append(renderCollectionBlock(collection, meta.collections.length > 1));
    return section;
  }


export function renderCollectionBlock(collection, showSubtitle = false) {
    const guide = COLLECTION_GUIDE[collection];
    const items = Array.isArray(state.template[collection]) ? state.template[collection] : null;
    if (!items || !guide) return make("div", "hidden");
    const block = make("div", "write-collection");
    block.id = `write-collection-${collection}`;

    const bar = make("div", "write-collection-bar");
    const count = make("div", "write-collection-count");
    if (showSubtitle) count.append(make("strong", "", guide.label));
    count.append(make("span", "write-count", `${items.length} 个`));
    bar.append(count);
    const add = make("button", "button ghost small", guide.addLabel);
    add.type = "button";
    add.disabled = Boolean(guide.clone) && !items.length;
    add.addEventListener("click", () => addCollectionItem(collection));
    bar.append(add);
    block.append(bar);

    if (showSubtitle) block.append(make("p", "write-collection-hint hint", guide.hint));

    const list = make("div", "write-items");
    if (!items.length) {
      const empty = make("div", "write-empty");
      empty.append(make("strong", "", `还没有${guide.label}。`));
      empty.append(make("p", "hint", guide.emptyHint));
      if (guide.clone) {
        const pick = make("button", "text-button", "从已验证模板开始");
        pick.type = "button";
        pick.addEventListener("click", openStartTools);
        empty.append(pick);
      }
      list.append(empty);
    }
    for (const field of guide.fields) {
      const options = typeof field.options === "function" ? field.options(state.template) : [];
      if (!options.length) continue;
      const datalist = document.createElement("datalist");
      datalist.id = `write-options-${collection}-${field.key}`;
      for (const value of options) datalist.append(new Option(String(value), String(value)));
      list.append(datalist);
    }
    const expanded = state.expandedCollections.has(collection);
    const limit = expanded ? items.length : Math.min(6, items.length);
    items.slice(0, limit).forEach((item, index) => list.append(renderCollectionItem(collection, item, index)));
    block.append(list);
    if (items.length > 6) {
      const more = make("button", "text-button wide write-show-more", expanded ? "收起" : `显示全部 ${items.length} 个${guide.label}`);
      more.type = "button";
      more.addEventListener("click", () => {
        if (expanded) state.expandedCollections.delete(collection);
        else state.expandedCollections.add(collection);
        renderGuidedEditor();
      });
      block.append(more);
    }
    return block;
  }


export function renderCollectionItem(collection, item, index) {
    const guide = COLLECTION_GUIDE[collection];
    const card = make("article", "write-item");
    card.dataset.section = collection;
    card.dataset.index = String(index);
    const initiallyOpen = Boolean(state.openItem && state.openItem.section === collection && state.openItem.index === index);
    if (initiallyOpen) card.classList.add("open");

    const head = make("button", "write-item-head");
    head.type = "button";
    const name = make("strong", "write-item-name", guide.title(item, index));
    const id = make("span", "write-item-id", item.id || "");
    const meta = make("span", "write-item-meta", guide.meta(item) || "");
    const action = make("span", "write-item-action", initiallyOpen ? "收起" : "编辑");
    head.append(name);
    if (item.id && item.id !== name.textContent) head.append(id);
    head.append(meta, action);
    head.addEventListener("click", () => {
      card.classList.toggle("open");
      action.textContent = card.classList.contains("open") ? "收起" : "编辑";
    });
    card.append(head);

    const body = make("div", "write-item-body");
    const grid = make("div", "write-fields");
    for (const field of guide.fields) grid.append(renderItemField(collection, item, field));
    body.append(grid);

    const guidedKeys = new Set(guide.fields.map((field) => field.key));
    const rest = {};
    for (const [key, value] of Object.entries(item)) {
      if (!guidedKeys.has(key)) rest[key] = value;
    }
    if (Object.keys(rest).length) {
      const more = document.createElement("details");
      more.className = "write-item-more";
      more.append(make("summary", "", "更多字段（不常用）"));
      more.append(make("p", "hint", "这里保留模板的完整结构。修改后立即写回草稿。"));
      const editor = document.createElement("textarea");
      editor.value = JSON.stringify(rest, null, 2);
      const error = make("p", "write-field-error", "");
      const known = new Set(Object.keys(rest));
      editor.addEventListener("input", () => {
        try {
          const parsed = JSON.parse(editor.value || "{}");
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("需要一个 JSON 对象");
          for (const key of known) delete item[key];
          Object.assign(item, parsed);
          known.clear();
          for (const key of Object.keys(parsed)) known.add(key);
          error.textContent = "";
          markDraftDirty();
        } catch (rawError) {
          error.textContent = `JSON 错误：${rawError.message}`;
        }
      });
      more.append(editor, error);
      body.append(more);
    }

    const footer = make("div", "write-item-footer");
    const remove = make("button", "text-button", "移除");
    remove.type = "button";
    remove.addEventListener("click", () => removeCollectionItem(collection, index));
    footer.append(remove);
    body.append(footer);
    card.append(body);
    return card;
  }


export function renderItemField(collection, item, field) {
    const label = make("label", `write-field${field.wide ? " wide" : ""}${field.type === "bool" ? " write-checkbox" : ""}`);
    label.append(make("span", "write-field-label", field.label));
    let input;
    if (field.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = 2;
      input.value = item[field.key] ?? "";
    } else if (field.type === "list") {
      input = document.createElement("input");
      input.value = listText(item[field.key]);
    } else if (field.type === "number") {
      input = document.createElement("input");
      input.type = "number";
      input.value = item[field.key] ?? 0;
    } else if (field.type === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(item[field.key]);
    } else {
      input = document.createElement("input");
      input.value = item[field.key] ?? "";
    }
    if (field.mono) input.classList.add("mono");
    if (field.placeholder) input.placeholder = field.placeholder;
    if (field.options) {
      const id = `write-options-${collection}-${field.key}`;
      if (document.getElementById(id)) input.setAttribute("list", id);
    }
    const apply = () => {
      if (field.type === "bool") item[field.key] = input.checked;
      else if (field.type === "list") item[field.key] = parseListText(input.value);
      else if (field.type === "number") item[field.key] = Number(input.value || 0);
      else item[field.key] = input.value;
      const card = label.closest(".write-item");
      const name = card?.querySelector(".write-item-name");
      if (name) name.textContent = COLLECTION_GUIDE[collection].title(item, Number(card.dataset.index || 0));
      markDraftDirty();
    };
    input.addEventListener(field.type === "bool" ? "change" : "input", apply);
    label.append(input);
    if (field.hint) label.append(make("small", "write-field-hint", field.hint));
    return label;
  }


export function addCollectionItem(collection) {
    const guide = COLLECTION_GUIDE[collection];
    const items = state.template[collection];
    if (!guide || !Array.isArray(items)) return;
    const item = guide.create(state.template);
    if (!item) return;
    items.push(item);
    state.expandedCollections.add(collection);
    state.draftRevision += 1;
    $("draft-revision").textContent = `revision ${state.draftRevision}`;
    state.openItem = { section: collection, index: items.length - 1 };
    markDraftDirty();
    renderGuidedEditor();
    const card = document.querySelector(`.write-item[data-section="${collection}"][data-index="${items.length - 1}"]`);
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
  }


export function removeCollectionItem(collection, index) {
    const items = state.template[collection];
    const guide = COLLECTION_GUIDE[collection];
    if (!guide || !Array.isArray(items) || !items[index]) return;
    if (!window.confirm(`移除「${guide.title(items[index], index)}」？这个操作只影响草稿。`)) return;
    items.splice(index, 1);
    state.draftRevision += 1;
    $("draft-revision").textContent = `revision ${state.draftRevision}`;
    markDraftDirty();
    renderGuidedEditor();
  }


export function renderAdvancedGroup() {
    const details = document.createElement("details");
    details.className = "write-advanced";
    details.id = "write-advanced";
    details.append(make("summary", "", "更多内容：行为树、能力、属性、关系、开局事件、规则"));
    const body = make("div", "write-advanced-body");
    body.append(make("p", "hint", "这些结构通常由模板提供。新手可以只看不改；需要时在这里补充。"));
    for (const collection of EXTRA_COLLECTIONS) body.append(renderCollectionBlock(collection, true));
    for (const section of OBJECT_SECTIONS) body.append(renderObjectSection(section));
    details.append(body);
    return details;
  }


export function renderObjectSection(section) {
    const details = document.createElement("details");
    details.className = "write-object-card";
    details.id = `write-collection-${section}`;
    details.append(make("summary", "", OBJECT_LABELS[section] || section));
    const editor = document.createElement("textarea");
    editor.value = JSON.stringify(state.template[section] || {}, null, 2);
    const error = make("p", "write-field-error", "");
    editor.addEventListener("input", () => {
      try {
        const parsed = JSON.parse(editor.value || "{}");
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("需要一个 JSON 对象");
        state.template[section] = parsed;
        error.textContent = "";
        markDraftDirty();
      } catch (rawError) {
        error.textContent = `JSON 错误：${rawError.message}`;
      }
    });
    const body = make("div", "write-object-body");
    body.append(make("p", "hint", "对象结构，修改后立即写回草稿。"), editor, error);
    details.append(body);
    return details;
  }


export function renderGuidedEditor() {
    const root = $("guided-editor");
    if (!root) return;
    root.replaceChildren();
    if (!state.template) state.template = defaultTemplate();
    for (const meta of WRITE_SECTIONS) {
      root.append(meta.id === "world" ? renderWorldSection() : renderCollectionSection(meta));
    }
    root.append(renderAdvancedGroup());
    state.openItem = null;
    renderAuthoringState();
  }


export function readinessChecks() {
    const template = state.template || defaultTemplate();
    const world = template.world || {};
    const locations = listValue(template.locations);
    const locationIds = new Set(locations.map((item) => item.id));
    const routeTargets = locations.flatMap((item) => listValue(item.routes));
    const player = template.player_template || {};
    const origins = listValue(player.origins);
    const title = String(world.title || "").trim();
    const routesOk = routeTargets.length > 0 && routeTargets.every((id) => locationIds.has(id));
    const entryLocation = locations.find((item) => item.id === player.entry_location_id);
    const entryOk = Boolean(entryLocation) && origins.length > 0;
    return [
      { key: "title", label: "世界有名字", detail: "玩家第一眼看到的世界名。", ok: Boolean(title) && title !== "未命名世界", target: "write-world" },
      { key: "premise", label: "有一句话设定", detail: "用一句话说清这里发生什么。", ok: Boolean(String(world.premise || "").trim()), target: "write-world" },
      { key: "locations", label: "至少两个地点", detail: "一个落脚点，一个可以去的地方。", ok: locations.length >= 2, target: "write-collection-locations" },
      {
        key: "routes",
        label: "地点之间有路线",
        detail: routesOk
          ? `${routeTargets.length} 条路线，全部指向现有地点。`
          : (routeTargets.length ? "有路线指向不存在的地点，请检查地点 ID。" : "没有路线，角色无法移动。"),
        ok: routesOk,
        target: "write-collection-locations",
      },
      { key: "agents", label: "至少一位居民", detail: "世界需要有人行动。", ok: listValue(template.agents).length >= 1, target: "write-collection-agents" },
      { key: "actions", label: "至少一个行动", detail: "玩家需要有能做的事。", ok: listValue(template.actions).length >= 1, target: "write-collection-actions" },
      {
        key: "entry",
        label: "玩家入口有效",
        detail: entryOk
          ? `玩家会从「${entryLocation.name || entryLocation.id}」进入，共 ${origins.length} 个出身。`
          : (!locations.length ? "先加地点，再选择玩家入口。" : (!origins.length ? "至少添加一个出身。" : "入口地点需要在当前地点列表里。")),
        ok: entryOk,
        target: "write-player-entry",
      },
      { key: "bt", label: "至少一棵行为树", detail: "NPC 自动行动需要它；已验证模板通常自带。", ok: listValue(template.behavior_trees).length >= 1, target: "write-collection-behavior_trees" },
    ];
  }


export function renderAuthoringState() {
    if (!$("authoring-readiness")) return;
    const checks = readinessChecks();
    const byKey = Object.fromEntries(checks.map((check) => [check.key, check]));
    const progress = [
      { key: "world", done: byKey.title.ok && byKey.premise.ok },
      { key: "locations", done: byKey.locations.ok && byKey.routes.ok },
      { key: "agents", done: byKey.agents.ok },
      { key: "actions", done: byKey.actions.ok },
      { key: "entry", done: byKey.entry.ok },
    ];
    const doneCount = progress.filter((step) => step.done).length;
    $("authoring-progress-label").textContent = `${doneCount} / 5 项`;
    $("authoring-progress-note").textContent = doneCount === 5 ? "可以试玩了" : "按顺序补齐即可";
    $("authoring-progress-bar").style.width = `${(doneCount / 5) * 100}%`;
    for (const button of document.querySelectorAll("[data-authoring-step]")) {
      const step = progress.find((item) => item.key === button.dataset.authoringStep);
      button.classList.toggle("done", Boolean(step && step.done));
      const stateNode = button.querySelector(".write-step-state");
      if (stateNode) stateNode.textContent = step && step.done ? "●" : "○";
    }

    const next = checks.find((check) => !check.ok);
    const nextTitle = $("authoring-next-title");
    const nextDetail = $("authoring-next-detail");
    const nextButton = $("authoring-next-go");
    if (next) {
      nextTitle.textContent = `还差：${next.label}`;
      nextDetail.textContent = next.detail;
      nextButton.textContent = "去填写";
      nextButton.dataset.target = next.target;
    } else {
      nextTitle.textContent = "全部就绪";
      nextDetail.textContent = "可以运行完整检查，或直接生成并打开游戏。";
      nextButton.textContent = "运行完整检查";
      nextButton.dataset.target = "compile";
    }

    const readiness = $("authoring-readiness");
    readiness.replaceChildren();
    const passedCount = checks.filter((check) => check.ok).length;
    const score = make("div", "readiness-score");
    score.append(make("strong", "", `${passedCount} / ${checks.length}`));
    score.append(make("span", "", "项就绪"));
    readiness.append(score);
    const rows = make("div", "readiness-list");
    for (const check of checks) {
      const row = make("button", `readiness-item ${check.ok ? "ok" : "todo"}`);
      row.type = "button";
      row.append(make("span", "readiness-mark", check.ok ? "✓" : "○"));
      const copy = make("span", "readiness-copy");
      copy.append(make("strong", "", check.label));
      copy.append(make("small", "", check.detail));
      row.append(copy);
      if (!check.ok) row.append(make("span", "readiness-go", "去填写"));
      row.addEventListener("click", () => gotoAuthoring(check.target));
      rows.append(row);
    }
    if (passedCount === checks.length) {
      readiness.append(make("p", "readiness-done", "全部就绪，可以发布。"));
      const details = document.createElement("details");
      details.className = "readiness-details";
      details.append(make("summary", "", "查看检查项"));
      details.append(rows);
      readiness.append(details);
    } else {
      readiness.append(rows);
    }

    const strip = $("start-strip");
    if (strip) {
      const empty = !listValue(state.template?.locations).length
        && !listValue(state.template?.agents).length
        && !listValue(state.template?.actions).length;
      strip.classList.toggle("hidden", !empty);
    }
    fillPublishDefaults(false);
  }


export function gotoAuthoring(target) {
    if (!target) return;
    if (target === "compile") {
      $("compile-draft").click();
      return;
    }
    const node = document.getElementById(target);
    if (!node) return;
    const details = node.closest("details");
    if (details) details.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
    if (node.classList.contains("write-item")) node.classList.add("open");
  }


export function markDraftDirty() {
    state.draftDirty = true;
    const status = $("draft-status");
    if (status) status.textContent = "草稿有修改";
    const dot = $("draft-dot");
    if (dot) dot.className = "status-dot busy";
    renderAuthoringState();
  }


export function sourcePackageName() {
    const source = String(state.sourcePath || "").replace(/[\\/]+$/, "");
    if (!source) return "";
    return source.split(/[\\/]/).filter(Boolean).pop() || "";
  }


export function fillPublishDefaults(force = false) {
    const output = $("publish-output");
    const instance = $("publish-instance");
    if (!output || !instance) return;
    const worldId = String(state.template?.world?.id || "").trim();
    const sourceName = sourcePackageName();
    let fallback = worldId && worldId !== "new_world" ? worldId : "";
    if (fallback && sourceName && fallback === sourceName) fallback = `${fallback}_draft`;
    if (force || !output.dataset.touched) output.value = fallback;
    const instanceBase = output.dataset.touched ? output.value.trim() : fallback;
    if (force || !instance.dataset.touched) instance.value = instanceBase ? `${instanceBase}_01` : "";
  }


export function parseDraft() {
    try {
      if (state.editorMode === "guided") return { value: cloneValue(state.template || defaultTemplate()), error: null };
      return { value: JSON.parse($("template-editor").value), error: null };
    } catch (error) {
      return { value: null, error: `JSON 格式错误：${error.message}` };
    }
  }


export function renderSchema() {
    const list = $("schema-list");
    if (!list) return;
    list.replaceChildren();
    const schema = state.schema || {};
    const properties = schema.properties || {};
    const required = new Set(schema.required || []);
    for (const [key, value] of Object.entries(properties)) {
      const item = make("button", `schema-item ${required.has(key) ? "required" : ""}`, `${key} · ${value.type || "value"}`);
      item.type = "button";
      item.addEventListener("click", () => {
        if (state.editorMode !== "raw") setEditorMode("raw");
        const editor = $("template-editor");
        editor.focus();
        editor.setSelectionRange(0, 0);
        editor.scrollTop = Math.max(0, editor.value.indexOf(`"${key}"`) * 0.5);
      });
      list.append(item);
    }
  }


export function draftHasContent() {
    const template = state.template || {};
    const collections = ["locations", "agents", "actions", "objects", "resources", "factions", "recipes", "behavior_trees", "relationships", "initial_events"];
    return Boolean(
      String(template.world?.title || "").trim()
      || String(template.world?.premise || "").trim()
      || collections.some((key) => listValue(template[key]).length)
    );
  }


export function confirmDraftReplace(message = "当前草稿会被替换，继续？") {
    return !draftHasContent() || window.confirm(message);
  }


export function openStartTools() {
    const more = document.querySelector(".write-more");
    if (more) more.open = true;
    more?.scrollIntoView({ behavior: "smooth", block: "start" });
    window.setTimeout(() => $("start-package")?.focus(), 200);
  }


export async function copyTemplateFromPackage() {
    const selected = $("start-package")?.value || "";
    const pkg = listValue(state.packages).find((item) => item.id === selected);
    if (!pkg) {
      $("draft-status").textContent = "先选择一个模板";
      return;
    }
    if (!confirmDraftReplace("当前草稿会被模板替换，继续？")) return;
    await withPending($("start-copy"), async () => {
      try {
        const result = await request("/api/v1/authoring/open", { method: "POST", body: JSON.stringify({ path: pkg.path }) });
        if (result.status !== "passed") {
          renderDiagnostics(result);
          $("draft-status").textContent = "打开失败";
          return;
        }
        setDraft(result.template, { sourcePath: result.source_path, increment: true });
        $("draft-status").textContent = `已复制 ${result.description?.world_id || pkg.id}`;
        $("draft-dot").className = "status-dot";
        renderDiagnostics({ status: "passed", description: result.description });
        const more = document.querySelector(".write-more");
        if (more) more.open = false;
        document.querySelector(".write-rail")?.scrollTo({ top: 0 });
        $("write-world")?.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (error) {
        renderDiagnostics({ status: "rejected", errors: [error.message] });
        $("draft-status").textContent = "复制失败";
      }
    });
  }


export function renderDiagnostics(result, extra = null) {
    const box = $("diagnostics"); box.replaceChildren();
    const passed = result && result.status === "passed";
    const summary = make("div", `diagnostic-summary ${passed ? "" : "error"}`, passed ? "✓ 当前草稿通过编译检查" : "! 当前草稿需要修正"); box.append(summary);
    for (const error of (result && (result.diagnostics || result.errors)) || []) {
      const message = typeof error === "object" ? `${error.path || "template"}: ${error.message || "invalid"}` : error;
      box.append(make("div", "diagnostic-line", message));
    }
    for (const warning of (result && result.warnings) || []) {
      const text = String(warning).replace(
        /^Imported package passed package validation; (\d+) unified-template advisories are source-format specific\.$/,
        "已按导入包校验；$1 条模板格式提示不影响运行。",
      );
      box.append(make("div", "diagnostic-line warning", `兼容提示：${text}`));
    }
    if (extra) {
      const smoke = extra.smoke;
      if (smoke) box.append(make("div", "diagnostic-line", `试玩 ${smoke.status === "passed" ? "通过" : "未通过"} · ${smoke.minutes} 分钟 · ${(smoke.event_types || []).join("、") || "没有新事件"}`));
      if (extra.files && !extra.package_format) box.append(make("div", "diagnostic-line", `已生成 ${extra.files.length} 个世界包文件`));
    }
    if (passed && !(result.errors || []).length && !extra) box.append(make("div", "diagnostic-line", result.description ? `${result.description.world_id || "world"} · 配置可执行` : "结构、引用和执行性检查通过"));
  }


export async function compileDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    state.draftRevision += 1; $("draft-revision").textContent = `revision ${state.draftRevision}`;
    $("draft-dot").className = "status-dot busy"; $("draft-status").textContent = "编译中";
    try { const result = await request("/api/v1/authoring/compile", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision }) }); renderDiagnostics(result); state.draftDirty = false; $("draft-dot").className = `status-dot ${result.status === "passed" ? "" : "offline"}`; $("draft-status").textContent = result.status === "passed" ? "已通过检查" : "有待修正项"; }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-dot").className = "status-dot offline"; $("draft-status").textContent = "编译失败"; }
  }


export async function smokeDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    $("draft-status").textContent = "试玩运行中"; $("draft-dot").className = "status-dot busy";
    try { const result = await request("/api/v1/authoring/smoke", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision, minutes: 3 }) }); renderDiagnostics(result, result); $("draft-dot").className = `status-dot ${result.status === "passed" ? "" : "offline"}`; $("draft-status").textContent = result.status === "passed" ? "试玩通过" : "试玩失败"; }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-dot").className = "status-dot offline"; }
  }


export async function exportDraft() {
    const parsed = parseDraft(); if (parsed.error) { renderDiagnostics({ status: "rejected", errors: [parsed.error] }); return; }
    try { const result = await request("/api/v1/authoring/export", { method: "POST", body: JSON.stringify({ template: parsed.value, source_path: state.sourcePath, draft_revision: state.draftRevision }) }); if (result.status !== "passed") { renderDiagnostics(result); return; } const blob = new Blob([JSON.stringify(result.files, null, 2)], { type: "application/json" }); const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${result.world_id || "world"}-package-files.json`; link.click(); URL.revokeObjectURL(link.href); renderDiagnostics(result); }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); }
  }


export function switchView(viewId) {
    document.querySelectorAll(".mode-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === viewId));
    document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active-view", view.id === viewId));
  }


export function setEditorMode(mode) {
    if (mode === "raw") {
      $("template-editor").value = JSON.stringify(state.template || defaultTemplate(), null, 2);
    } else {
      try {
        const parsed = JSON.parse($("template-editor").value);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("模板必须是一个 JSON 对象");
        state.template = parsed;
        renderGuidedEditor();
      } catch (error) {
        renderDiagnostics({ status: "rejected", errors: [`JSON 格式错误：${error.message}`] });
        return;
      }
    }
    state.editorMode = mode;
    $("guided-editor").classList.toggle("hidden", mode !== "guided");
    $("template-editor").classList.toggle("hidden", mode !== "raw");
    $("guided-mode").classList.toggle("active", mode === "guided");
    $("raw-mode").classList.toggle("active", mode === "raw");
  }


export async function openPackage(path = "") {
    try {
      const result = await request("/api/v1/authoring/open", { method: "POST", body: JSON.stringify(path ? { path } : {}) });
      if (result.status !== "passed") { renderDiagnostics(result); $("draft-status").textContent = "打开失败"; return; }
      setDraft(result.template, { sourcePath: result.source_path }); $("draft-status").textContent = `已打开 ${result.description?.world_id || "world"}`; $("draft-dot").className = "status-dot"; renderDiagnostics({ status: "passed", description: result.description });
    } catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-status").textContent = "打开失败"; }
  }


export async function buildWorldFromBrief() {
    const brief = $("builder-brief").value.trim(); if (!brief) { renderDiagnostics({ status: "rejected", errors: ["请先输入世界简述"] }); return; }
    if (!confirmDraftReplace("AI 起草会替换当前草稿，继续？")) return;
    $("draft-status").textContent = "WorldBuilder 运行中"; $("draft-dot").className = "status-dot busy";
    try { const result = await request("/api/v1/authoring/build", { method: "POST", body: JSON.stringify({ brief }) }); if (result.status !== "passed") { renderDiagnostics(result); $("draft-status").textContent = "起草失败"; $("draft-dot").className = "status-dot offline"; return; } setDraft(result.template, { increment: true, clearSource: true }); $("draft-status").textContent = `已生成草稿 · ${result.audit?.iterations || "?"} 轮`; $("draft-dot").className = "status-dot"; renderDiagnostics({ status: "passed", description: { world_id: result.template?.world?.id || "draft" } }); }
    catch (error) { renderDiagnostics({ status: "rejected", errors: [error.message] }); $("draft-status").textContent = "起草失败"; $("draft-dot").className = "status-dot offline"; }
  }


export function renderPackageCatalog(response, message = "没有可用模板") {
    const packages = response?.packages || [];
    state.packages = packages;
    const valid = packages.filter((item) => item.status === "passed");
    for (const id of ["package-select", "start-package"]) {
      const select = $(id);
      if (!select) continue;
      const previous = select.value;
      select.replaceChildren();
      if (!valid.length) {
        select.append(new Option(message, ""));
        continue;
      }
      for (const item of valid) select.append(new Option(`${item.name || item.id} · ${item.id}`, item.id));
      if (previous && valid.some((item) => item.id === previous)) select.value = previous;
    }
  }


export async function loadPackageCatalog() {
    state.catalogRequest += 1;
    const requestId = state.catalogRequest;
    try {
      const response = await request("/api/v1/authoring/catalog");
      if (requestId !== state.catalogRequest) return;
      renderPackageCatalog(response);
    } catch (error) {
      if (requestId !== state.catalogRequest) return;
      if (!listValue(state.packages).length) renderPackageCatalog({ packages: [] }, "模板目录不可用");
    }
  }


export async function createNewInstance() {
    const packageId = $("package-select")?.value || "";
    const instanceId = $("instance-id")?.value.trim() || "";
    const resultBox = $("instance-result");
    if (!packageId || !instanceId) { resultBox.textContent = "请选择模板并填写实例 ID"; resultBox.className = "interaction-result error"; return; }
    if (!(await ensureOperator())) return;
    resultBox.textContent = "创建中…"; resultBox.className = "interaction-result";
    try {
      const result = await write("/api/v1/instances/init", { package_id: packageId, instance_id: instanceId, activate: true });
      if (result.status !== "passed") throw new Error(result.message || (result.errors || ["创建失败"]).join("; "));
      resultBox.textContent = `已创建 ${result.instance_id}，正在打开…`;
      resultBox.className = "interaction-result ok";
      if (result.activation && result.activation.status !== "accepted") {
        throw new Error(result.activation.message || "实例已创建，但切换失败");
      }
      window.location.reload();
    } catch (error) { resultBox.textContent = error.message || "创建失败"; resultBox.className = "interaction-result error"; }
  }
