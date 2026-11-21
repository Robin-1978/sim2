import { activateInstance, control, enterPlayer, loadInstances, loadPlayerEntry, loadPlayerView, parseBundle, publishWorld, refreshConsoleInstances, refreshRuntime, runConsole, sendDialogue, sendPlayerDialogue, startRuntime, submitPlayerTrade, updateOriginPreview } from "./admin_actions.js";
import { request, startStream, write } from "./admin_api.js";
import { $, make, openOperatorDialog, operatorAction, readHandoff, readStoredOperatorToken, rememberOperatorToken, resolveView, setConsoleResult, setOperatorState, setPlayerResult, showNotice, state, storageWritable, unlockOperator } from "./admin_core.js";
import { buildWorldFromBrief, compileDraft, confirmDraftReplace, copyTemplateFromPackage, createNewInstance, defaultTemplate, exportDraft, fillPublishDefaults, gotoAuthoring, loadPackageCatalog, openPackage, openStartTools, parseDraft, renderDiagnostics, renderSchema, setDraft, setEditorMode, smokeDraft, switchView } from "./admin_editor.js";
import { renderNpcDecisions, renderNpcDialogue, renderPlayerTrades } from "./admin_render.js";


export async function init() {
    const handoff = readHandoff();
    const initial = defaultTemplate();
    setDraft(initial);
    setEditorMode("guided");
    document.querySelectorAll(".mode-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
    $("refresh-button").addEventListener("click", () => refreshRuntime(true));
    $("start-button").addEventListener("click", () => startRuntime().catch((error) => showNotice(error.message || "自动运行未启动")));
    $("pause-button").addEventListener("click", () => control("pause"));
    $("resume-button").addEventListener("click", () => control("resume"));
    $("stop-button").addEventListener("click", () => control("stop"));
    $("step-button").addEventListener("click", () => control("step"));
    $("toggle-ticks").addEventListener("click", () => { state.includeTicks = !state.includeTicks; $("toggle-ticks").textContent = state.includeTicks ? "隐藏 tick" : "显示 tick"; refreshRuntime(true); });
    $("dialogue-refresh").addEventListener("click", renderNpcDialogue);
    $("decisions-refresh").addEventListener("click", renderNpcDecisions);
    $("instance-select").addEventListener("change", (event) => activateInstance(event.target.value).catch((error) => showNotice(error.message || "实例切换失败")));
    $("operator-unlock").addEventListener("click", () => openOperatorDialog(true));
    $("back-to-game").addEventListener("click", () => {
      if (window.parent !== window) {
        try { window.parent.postMessage({ type: "sim2:close-editor" }, location.origin); }
        catch (error) { /* fall through to navigation */ }
        return;
      }
      window.location.href = "/";
    });
    // Handle the form submit (not the button click): Enter/autofill/mobile-go
    // submit the method="dialog" form natively, which would close the dialog
    // without unlocking. Centralizing here covers click and keyboard alike.
    $("operator-dialog").querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      unlockOperator($("operator-token").value).catch((error) => { $("operator-error").textContent = error.message || "解锁失败"; });
    });
    $("operator-cancel").addEventListener("click", () => $("operator-dialog").close());
    $("operator-dialog").addEventListener("close", () => { $("operator-token").value = ""; $("operator-error").textContent = ""; if (state.operatorResolve) state.operatorResolve(false); state.operatorResolve = null; });
    $("open-player").addEventListener("click", () => switchView("play-view"));
    $("play-enter").addEventListener("click", enterPlayer);
    $("play-player-origin").addEventListener("change", updateOriginPreview);
    $("player-refresh").addEventListener("click", loadPlayerView);
    $("player-send-dialogue").addEventListener("click", (event) => sendPlayerDialogue(event.currentTarget));
    $("player-send-trade").addEventListener("click", (event) => submitPlayerTrade(event.currentTarget));
    $("player-trades-refresh").addEventListener("click", renderPlayerTrades);
    $("player-leave").addEventListener("click", () => {
      state.playerId = null; state.playerData = null; state.playerToken = null; state.sessionToken = state.observerToken;
      localStorage.removeItem("sim2_player_id"); localStorage.removeItem("sim2_player_token");
      $("player-world").classList.add("hidden"); $("player-setup").classList.remove("hidden"); setPlayerResult("已退出当前角色。", true);
    });
    $("send-dialogue").addEventListener("click", (event) => sendDialogue(event.currentTarget));
    $("create-instance").addEventListener("click", () => createNewInstance().catch((error) => { $("instance-result").textContent = error.message || "创建失败"; $("instance-result").className = "interaction-result error"; }));
    $("new-draft").addEventListener("click", () => {
      if (!confirmDraftReplace("当前草稿会被清空，继续？")) return;
      setDraft(defaultTemplate(), { increment: true, clearSource: true });
    });
    $("open-current").addEventListener("click", operatorAction(() => {
      if (!confirmDraftReplace()) return;
      return openPackage();
    }));
    $("open-path").addEventListener("click", operatorAction(() => {
      if (!confirmDraftReplace()) return;
      return openPackage($("authoring-path").value.trim());
    }));
    $("format-draft").addEventListener("click", () => { const parsed = parseDraft(); if (!parsed.error) setDraft(parsed.value); else renderDiagnostics({ status: "rejected", errors: [parsed.error] }); });
    $("guided-mode").addEventListener("click", () => setEditorMode("guided"));
    $("raw-mode").addEventListener("click", () => setEditorMode("raw"));
    $("start-copy").addEventListener("click", operatorAction(copyTemplateFromPackage));
    document.querySelector(".write-more > summary")?.addEventListener("click", () => { loadPackageCatalog(); });
    $("start-strip-open").addEventListener("click", openStartTools);
    for (const button of document.querySelectorAll("[data-authoring-step]")) {
      button.addEventListener("click", () => {
        const target = {
          world: "write-world",
          locations: "write-collection-locations",
          agents: "write-collection-agents",
          actions: "write-collection-actions",
          entry: "write-player-entry",
        }[button.dataset.authoringStep];
        gotoAuthoring(target);
      });
    }
    $("authoring-next-go").addEventListener("click", () => gotoAuthoring($("authoring-next-go").dataset.target || "write-world"));
    $("publish-output").addEventListener("input", () => {
      $("publish-output").dataset.touched = "1";
      fillPublishDefaults(false);
    });
    $("publish-instance").addEventListener("input", () => { $("publish-instance").dataset.touched = "1"; });
    $("publish-build").addEventListener("click", (event) => publishWorld(false, event.currentTarget));
    $("publish-play").addEventListener("click", (event) => publishWorld(true, event.currentTarget));
    $("build-world").addEventListener("click", operatorAction(buildWorldFromBrief));
    $("compile-draft").addEventListener("click", operatorAction(compileDraft));
    $("smoke-draft").addEventListener("click", operatorAction(smokeDraft));
    $("export-draft").addEventListener("click", operatorAction(exportDraft));
    $("console-refresh").addEventListener("click", refreshConsoleInstances);
    $("console-clear").addEventListener("click", () => setConsoleResult("等待命令…"));
    $("console-init").addEventListener("click", () => runConsole("初始化世界", async () => {
      const packageId = $("console-init-package").value.trim() || $("package-select")?.value || "";
      const instanceId = $("console-init-instance").value.trim();
      if (!packageId || !instanceId) throw new Error("需要填写世界模板 ID 和新实例 ID");
      const result = await write("/api/v1/instances/init", { package_id: packageId, instance_id: instanceId, activate: $("console-init-activate").checked });
      if (result.activation?.status === "accepted") window.location.reload();
      await loadInstances();
      return result;
    }));
    $("console-snapshot").addEventListener("click", () => runConsole("创建快照", async () => write("/api/v1/operator/snapshots", { label: $("console-snapshot-label").value.trim() || "manual" })));
    $("console-snapshots").addEventListener("click", refreshConsoleInstances);
    $("console-restore").addEventListener("click", () => runConsole("恢复快照", async () => {
      const snapshotId = $("console-restore-id").value.trim();
      if (!snapshotId) throw new Error("需要填写快照 ID");
      const result = await write(`/api/v1/operator/snapshots/${encodeURIComponent(snapshotId)}/restore`, { snapshot_id: snapshotId });
      await refreshRuntime(true);
      return result;
    }));
    $("console-simulate").addEventListener("click", () => runConsole("同步推演", async () => write("/api/v1/operator/simulate", { minutes: Number($("console-sim-minutes").value || 10), provider: $("console-sim-provider").value.trim() || null })));
    $("console-decide").addEventListener("click", () => runConsole("单步决策", async () => {
      const agentId = $("console-decide-agent").value.trim();
      if (!agentId) throw new Error("需要填写 Agent ID");
      return write("/api/v1/operator/decide", { agent_id: agentId, role: $("console-decide-role").value.trim() || "agent_decision" });
    }));
    $("console-status").addEventListener("click", () => runConsole("status", async () => request("/api/v1/status"), { operator: false }));
    $("console-health").addEventListener("click", () => runConsole("health", async () => request("/api/v1/health"), { operator: false }));
    $("console-metrics").addEventListener("click", () => runConsole("metrics", async () => request("/api/v1/operator/metrics"), { operator: false }));
    $("console-audit").addEventListener("click", () => runConsole("audit", async () => request("/api/v1/operator/audit")));
    $("console-validate").addEventListener("click", () => runConsole("校验世界包", async () => write("/api/v1/authoring/validate", { package_id: $("console-pkg-id").value.trim() || undefined, path: $("console-pkg-path").value.trim() || undefined })));
    $("console-describe").addEventListener("click", () => runConsole("世界包详情", async () => write("/api/v1/authoring/describe", { package_id: $("console-pkg-id").value.trim() || undefined, path: $("console-pkg-path").value.trim() || undefined })));
    $("console-schema").addEventListener("click", () => runConsole("world-schema", async () => request("/api/v1/authoring/schema"), { operator: false }));
    $("console-build-world").addEventListener("click", () => runConsole("创建世界包", async () => {
      const parsed = parseDraft();
      if (parsed.error) throw new Error(parsed.error);
      return write("/api/v1/authoring/build-world", { template: parsed.value, output: $("console-build-output").value.trim(), overwrite: $("console-build-overwrite").checked, instance_id: $("console-build-instance").value.trim() || undefined, source_path: state.sourcePath || undefined });
    }));
    $("console-trade-list").addEventListener("click", () => runConsole("列出交易", async () => {
      const agentId = $("console-trade-agent").value.trim() || state.playerId || "";
      if (!agentId) throw new Error("需要填写 Agent ID（或先进入世界）");
      return request(`/api/v1/player/${encodeURIComponent(agentId)}/trades?include_closed=1`);
    }, { operator: false }));
    const tradeAct = (kind) => runConsole(kind, async () => {
      const me = $("console-trade-me").value.trim() || state.playerId || "";
      const peer = $("console-trade-peer").value.trim();
      const offerId = $("console-offer-id").value.trim();
      if (!me) throw new Error("需要填写我方 Agent ID（或先进入世界）");
      if (kind === "offer") {
        if (!peer) throw new Error("发起交易需要对方 ID");
        return write(`/api/v1/player/${encodeURIComponent(me)}/trades`, { recipient_id: peer, give: parseBundle($("console-trade-give").value), want: parseBundle($("console-trade-want").value) });
      }
      if (!offerId) throw new Error("需要填写 Offer ID");
      if (kind === "counter") return write(`/api/v1/player/${encodeURIComponent(me)}/trades/${encodeURIComponent(offerId)}/counter`, { give: parseBundle($("console-trade-give").value), want: parseBundle($("console-trade-want").value) });
      if (kind === "accept") return write(`/api/v1/player/${encodeURIComponent(me)}/trades/${encodeURIComponent(offerId)}/accept`, {});
      return write(`/api/v1/player/${encodeURIComponent(me)}/trades/${encodeURIComponent(offerId)}/reject`, {});
    }, { operator: false });
    $("console-trade-offer").addEventListener("click", () => tradeAct("offer"));
    $("console-trade-counter").addEventListener("click", () => tradeAct("counter"));
    $("console-trade-accept").addEventListener("click", () => tradeAct("accept"));
    $("console-trade-reject").addEventListener("click", () => tradeAct("reject"));
    $("console-inject").addEventListener("click", () => runConsole("注入事件", async () => {
      const eventType = $("console-event-type").value.trim();
      if (!eventType) throw new Error("需要填写事件类型");
      let payload = {};
      try { payload = $("console-event-payload").value.trim() ? JSON.parse($("console-event-payload").value) : {}; }
      catch (error) { throw new Error(`Payload JSON 错误：${error.message}`); }
      return write("/api/v1/operator/events", { event_type: eventType, payload });
    }));
    state.operatorToken = handoff.token || readStoredOperatorToken();
    if (handoff.token) rememberOperatorToken(handoff.token);
    setOperatorState(Boolean(state.operatorToken));
    if (!storageWritable()) {
      showNotice("浏览器阻止了本地存储：解锁状态仅当前页面有效，刷新或重开后需重新解锁。");
    }
    try { const session = await request("/api/v1/session", { method: "POST", body: JSON.stringify({ role: "observer" }) }); state.observerToken = session.session_token || null; state.sessionToken = state.observerToken; } catch (error) { /* public projections remain readable in local read-only mode */ }
    let savedPlayer = null, savedPlayerToken = null;
    try { savedPlayer = localStorage.getItem("sim2_player_id"); savedPlayerToken = localStorage.getItem("sim2_player_token"); } catch (error) { /* ignore */ }
    if (savedPlayer && savedPlayerToken) { state.playerId = savedPlayer; state.playerToken = savedPlayerToken; state.sessionToken = savedPlayerToken; const restored = await loadPlayerView(); if (!restored) { state.playerId = null; state.playerToken = null; state.sessionToken = state.observerToken; try { localStorage.removeItem("sim2_player_id"); localStorage.removeItem("sim2_player_token"); } catch (error) { /* ignore */ } } }
    try { const response = await request("/api/v1/authoring/schema"); state.schema = response.schema; renderSchema(); } catch (error) { $("schema-list").replaceChildren(make("div", "empty-state", "Schema 暂不可用")); }
    await Promise.all([loadPackageCatalog(), loadPlayerEntry(), loadInstances()]);
    await refreshRuntime(true);
    startStream();
    const requestedView = resolveView(handoff.view);
    if (requestedView) switchView(requestedView);
    state.poll = window.setInterval(() => { if ($("runtime-view").classList.contains("active-view")) refreshRuntime(false); else if ($("play-view").classList.contains("active-view") && state.playerId) loadPlayerView(); }, 4000);
  }


  window.addEventListener("DOMContentLoaded", init);
