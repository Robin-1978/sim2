import { $, clearOperatorSession, newIdempotencyKey, openOperatorDialog, setConnection, state } from "./admin_core.js";
import { renderNpcDecisions, renderNpcDialogue, renderRuntime, renderTimeline } from "./admin_render.js";


export async function request(path, options = {}) {
    const usedOperatorToken = Boolean(state.operatorToken);
    const sessionToken = state.operatorToken || state.playerToken || state.observerToken || state.sessionToken;
    const method = String(options.method || "GET").toUpperCase();
    let response;
    try {
      response = await fetch(path, {
        ...options,
        // Spread options first: per-call headers (Idempotency-Key) must extend,
        // never replace, the session and content-type headers.
        headers: { "content-type": "application/json", ...(sessionToken ? { "x-sim2-session": sessionToken } : {}), ...(options.headers || {}) },
      });
    } catch (error) {
      if (typeof console !== "undefined" && console.error) console.error(`[sim2] ${method} ${path} 网络错误`, error);
      throw error;
    }
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw new Error(`服务端返回了 ${response.status} ${type || "未知内容"}`);
    }
    const value = await response.json();
    if (!response.ok) {
      if (typeof console !== "undefined" && console.error) {
        console.error(`[sim2] ${method} ${path} -> ${response.status} ${value.message || ""}`, {
          failed_check: value.failed_check || value.status || null,
          session: sessionToken ? "present" : "missing",
        });
      }
      const error = new Error(value.message || `请求失败 (${response.status})`);
      error.status = response.status;
      error.code = value.failed_check || value.status || null;
      if (usedOperatorToken && response.status === 403 && path !== "/api/v1/session") {
        clearOperatorSession();
        openOperatorDialog(true);
      }
      throw error;
    }
    return value;
  }


export async function write(path, body = {}) {
    // POST with an idempotency key; retries once on network failure with the same key.
    const key = newIdempotencyKey();
    const attempt = () => request(path, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ ...body, idempotency_key: key }) });
    try {
      return await attempt();
    } catch (error) {
      if (error instanceof TypeError) return await attempt();
      throw error;
    }
  }


export function mergeTimelineEvents(events) {
    let advanced = false;
    for (const event of events || []) {
      if (event == null || event.event_id == null) continue;
      if (!state.events.has(event.event_id)) advanced = true;
      state.events.set(event.event_id, event);
      state.after = Math.max(state.after, Number(event.event_id || state.after));
    }
    if (advanced) renderTimeline();
  }


export function startStream() {
    if (state.stream || typeof EventSource === "undefined") return;
    let source;
    try {
      source = new EventSource(`/api/v1/stream?after=${state.streamCursor || 0}${state.includeTicks ? "&ticks=1" : ""}&session_token=${encodeURIComponent(state.operatorToken || state.playerToken || state.observerToken || state.sessionToken || "")}`);
    } catch (error) { return; }
    state.stream = source;
    state.streamFailures = 0;
    source.addEventListener("timeline", (event) => {
      try {
        const envelope = JSON.parse(event.data || "{}");
        state.streamCursor = Math.max(Number(state.streamCursor || 0), Number(envelope.cursor || 0));
        const payload = envelope.data || {};
        mergeTimelineEvents(payload.events);
        if (payload.next_after_event_id) state.after = Math.max(state.after, Number(payload.next_after_event_id));
        setConnection(true, "实时连接");
      } catch (error) { /* keep polling as the fallback */ }
    });
    source.addEventListener("runtime", (event) => {
      try {
        const envelope = JSON.parse(event.data || "{}");
        state.streamCursor = Math.max(Number(state.streamCursor || 0), Number(envelope.cursor || 0));
        const runtime = envelope.data || {};
        renderRuntime(runtime);
        if (runtime.world_minute !== undefined) $("world-clock").textContent = `minute ${runtime.world_minute}`;
        $("last-update").textContent = `updated ${new Date().toLocaleTimeString()}`;
        setConnection(true, "实时连接");
      } catch (error) { /* keep polling as the fallback */ }
    });
    source.addEventListener("dialogue", (event) => {
      try { state.streamCursor = Math.max(Number(state.streamCursor || 0), Number(JSON.parse(event.data || "{}").cursor || 0)); } catch (error) { /* ignore */ }
      renderNpcDialogue();
    });
    source.addEventListener("decisions", (event) => {
      try { state.streamCursor = Math.max(Number(state.streamCursor || 0), Number(JSON.parse(event.data || "{}").cursor || 0)); } catch (error) { /* ignore */ }
      renderNpcDecisions();
    });
    source.onerror = () => {
      state.streamFailures = (state.streamFailures || 0) + 1;
      if (state.streamFailures >= 3 && state.stream) {
        try { state.stream.close(); } catch (error) { /* ignore */ }
        state.stream = null;
      }
    };
  }
