import { request } from "./admin_api.js";
import { loadPackageCatalog } from "./admin_editor.js";
import { renderEvolution } from "./admin_render.js";


export const state = {
    after: 0,
    includeTicks: false,
    events: new Map(),
    schema: null,
    template: null,
    sourcePath: null,
    editorMode: "guided",
    draftRevision: 1,
    poll: null,
    lastObserver: null,
    lastMap: null,
    selectedLocationId: null,
    selectedAgentId: null,
    packages: [],
    playerId: null,
    playerData: null,
    playerEntry: null,
    observerToken: null,
    operatorToken: null,
    playerToken: null,
    sessionToken: null,
    runtime: null,
    instances: [],
    activeDb: null,
    operatorResolve: null,
    stream: null,
    streamFailures: 0,
    streamCursor: 0,
    advancing: false,
    expandedCollections: new Set(),
    openItem: null,
    draftDirty: false,
    catalogRequest: 0,
  };


export const $ = (id) => document.getElementById(id);

export const OPERATOR_KEY = "sim2.game.operator";

export const LEGACY_OPERATOR_KEY = "sim2_operator_token";

export const VIEW_ALIASES = {
    play: "play-view",
    runtime: "runtime-view",
    observer: "runtime-view",
    authoring: "authoring-view",
    editor: "authoring-view",
    design: "authoring-view",
    console: "console-view",
  };

export const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };


export function newIdempotencyKey() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return `web-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e12).toString(36)}`;
  }


export async function withPending(button, fn) {
    if (!button) return fn();
    const wasDisabled = button.disabled;
    button.disabled = true;
    try { return await fn(); }
    finally { button.disabled = wasDisabled; }
  }


export function setOperatorState(unlocked) {
    $("operator-unlock").textContent = unlocked ? "管理已解锁" : "解锁管理";
    $("operator-unlock").classList.toggle("unlocked", unlocked);
  }


export function readStoredOperatorToken() {
    try {
      return sessionStorage.getItem(OPERATOR_KEY) || sessionStorage.getItem(LEGACY_OPERATOR_KEY);
    } catch (error) {
      return null;
    }
  }


export function rememberOperatorToken(token) {
    // Storage may be blocked (hardened browser/policy). The session is
    // already valid server-side; never let a storage failure strand the dialog.
    try {
      sessionStorage.setItem(OPERATOR_KEY, token);
      sessionStorage.setItem(LEGACY_OPERATOR_KEY, token);
    } catch (error) { /* memory-only session for this tab */ }
  }


export function forgetOperatorToken() {
    try {
      sessionStorage.removeItem(OPERATOR_KEY);
      sessionStorage.removeItem(LEGACY_OPERATOR_KEY);
    } catch (error) { /* ignore */ }
  }


export function readHandoff() {
    const raw = String(location.hash || "").replace(/^#/, "");
    if (!raw) return { token: null, view: null };
    if (!raw.includes("=")) return { token: null, view: raw };
    const params = new URLSearchParams(raw);
    const token = params.get("operator");
    const view = params.get("view");
    if (token || view) {
      // Keep the view so an in-editor reload (create/open instance) returns to
      // the same mode, but never keep the session token in browser history.
      const next = view ? `#view=${encodeURIComponent(view)}` : "";
      try { history.replaceState(null, "", `${location.pathname}${location.search}${next}`); }
      catch (error) { /* history can be restricted */ }
    }
    return { token, view };
  }


export function resolveView(view) {
    if (!view) return "";
    const key = String(view).trim().toLowerCase();
    if (key.endsWith("-view")) return key;
    return VIEW_ALIASES[key] || "";
  }


export function storageWritable() {
    try {
      sessionStorage.setItem("sim2_probe", "1");
      sessionStorage.removeItem("sim2_probe");
      return true;
    } catch (error) { return false; }
  }


export function clearOperatorSession() {
    state.operatorToken = null;
    forgetOperatorToken();
    setOperatorState(false);
  }


export function openOperatorDialog(force = false) {
    if (state.operatorToken && !force) return Promise.resolve(true);
    if ($("operator-dialog").open) return Promise.resolve(false);
    if (typeof $("operator-dialog").showModal === "function") {
      $("operator-dialog").showModal();
      const input = $("operator-token");
      // Autofill + Enter submits the form; focus here so paste/keyboard flow lands in the input.
      if (input && typeof input.focus === "function") { input.focus(); input.select(); }
    }
    return new Promise((resolve) => { state.operatorResolve = resolve; });
  }


export async function unlockOperator(rawToken) {
    const token = String(rawToken || "").trim();
    if (!token) throw new Error("请输入 Operator Token");
    const result = await request("/api/v1/session", { method: "POST", body: JSON.stringify({ role: "operator", operator_token: token }) });
    if (result.status !== "ok" || result.role !== "operator") throw new Error(result.message || "解锁失败");
    state.operatorToken = result.session_token;
    rememberOperatorToken(result.session_token);
    setOperatorState(true);
    loadPackageCatalog();
    if (typeof renderEvolution === "function") renderEvolution();
    const resolve = state.operatorResolve;
    state.operatorResolve = null;
    if (resolve) resolve(true);
    $("operator-dialog").close();
    return true;
  }


export async function ensureOperator() {
    if (state.operatorToken) return true;
    return openOperatorDialog();
  }


export function operatorAction(handler) {
    return async (...args) => {
      if (!(await ensureOperator())) return;
      try { return await handler(...args); }
      catch (error) {
        if (/operator|session|forbidden|权限/i.test(error.message || "")) {
          clearOperatorSession();
          openOperatorDialog(true);
        }
        throw error;
      }
    };
  }


export function setConnection(online, text = online ? "已连接" : "连接中断") {
    $("connection-dot").className = `status-dot ${online ? "" : "offline"}`;
    $("connection-label").textContent = text;
  }


export function showNotice(message) {
    const notice = $("runtime-notice");
    notice.textContent = message || "";
    notice.classList.toggle("hidden", !message);
  }


export function setPlayerResult(message, ok) { const notice = $("play-notice"); notice.textContent = message || ""; notice.className = `notice ${ok ? "notice-ok" : ""}`; notice.classList.toggle("hidden", !message); }


export function describeRejection(result, fallback) {
    if (!result || typeof result !== "object") return fallback;
    const parts = [result.message || fallback];
    if (result.missing) parts.push(`缺失: ${result.missing}`);
    if (Array.isArray(result.allowed_values) && result.allowed_values.length) parts.push(`可用: ${result.allowed_values.slice(0, 12).join(", ")}`);
    return parts.join(" ");
  }


export function setInteractionResult(message, ok) { const target = $("interaction-result"); target.textContent = message || ""; target.className = `interaction-result ${ok ? "ok" : "error"}`; }


export function setInstanceResult(message, ok) {
    const box = $("instance-result");
    if (!box) return;
    box.textContent = message || "";
    box.className = `interaction-result ${ok ? "ok" : "error"}`;
  }


export function setConsoleResult(value) {
    const box = $("console-result");
    if (!box) return;
    box.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }


export function consoleNotice(message, ok) {
    const notice = $("console-notice");
    if (!notice) return;
    notice.textContent = message || "";
    notice.className = `notice ${ok ? "notice-ok" : ""}`;
    notice.classList.toggle("hidden", !message);
  }
