import { $, OPERATOR_KEY, setOnline, state } from "./core.js";



export function tokenFor(path) {
    if (path.startsWith("/api/v1/runtime/") || path.startsWith("/api/v1/instances") || path.startsWith("/api/v1/authoring/") || path.startsWith("/api/v1/operator/")) {
      return state.operatorToken || state.playerToken;
    }
    if (path.startsWith("/api/v1/player/") || path.startsWith("/api/v1/observer/")
        || path.startsWith("/api/v1/events/") || path.startsWith("/api/v1/map")) {
      return state.playerToken || state.operatorToken;
    }
    return state.operatorToken || state.playerToken;
  }


export async function request(path, options = {}) {
    const token = tokenFor(path);
    const method = String(options.method || "GET").toUpperCase();
    const isGet = method === "GET";
    path = isGet
      ? `${path}${path.includes("?") ? "&" : "?"}_sim2_ts=${Date.now()}`
      : path;
    let response;
    try {
      response = await fetch(path, {
        ...options,
        cache: isGet ? "no-store" : options.cache,
        headers: {
          "content-type": "application/json",
          ...(token ? { "x-sim2-session": token } : {}),
          ...(options.headers || {}),
        },
      });
    } catch (error) {
      setOnline(false);
      throw error;
    }
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      setOnline(false);
      throw new Error(`服务端返回了 ${response.status} ${type || "未知内容"}`);
    }
    const value = await response.json();
    setOnline(response.ok || response.status < 500);
    if (!response.ok) {
      const error = new Error(value.message || `请求失败 (${response.status})`);
      error.status = response.status;
      error.code = value.failed_check || value.status || null;
      error.payload = value;
      throw error;
    }
    return value;
  }


export function newIdempotencyKey() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return `web-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e12).toString(36)}`;
  }


export async function write(path, body = {}) {
    const key = newIdempotencyKey();
    const attempt = () => request(path, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ ...body, idempotency_key: key }),
    });
    try {
      return await attempt();
    } catch (error) {
      if (error instanceof TypeError) return await attempt();
      throw error;
    }
  }


export async function unlockFromOwnerLink() {
    const match = String(location.hash || "").match(/^#owner=([^&]+)/);
    if (!match) return;
    const token = decodeURIComponent(match[1]);
    try {
      history.replaceState(null, "", `${location.pathname}${location.search}`);
    } catch (error) {
      /* history can be restricted; the token is still consumed once */
    }
    try {
      const result = await write("/api/v1/session", { role: "operator", operator_token: token });
      if (result.status === "ok" && result.session_token) {
        state.operatorToken = result.session_token;
        try { sessionStorage.setItem(OPERATOR_KEY, state.operatorToken); } catch (error) { /* ignore */ }
        return;
      }
      // A direct master token is also accepted by the local session boundary.
      state.operatorToken = token;
      try { sessionStorage.setItem(OPERATOR_KEY, token); } catch (error) { /* ignore */ }
    } catch (error) {
      state.operatorToken = token;
      try { sessionStorage.setItem(OPERATOR_KEY, token); } catch (error) { /* ignore */ }
    }
  }
