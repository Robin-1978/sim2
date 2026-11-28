import { activateInstance, advanceTask, beginAction, closeEditor, closeSettings, createWorldInstance, loadDialogue, loadEntryContext, loadFeed, loadInstances, loadRuntime, loadTrades, loadView, openAdmin, openEditor, openMap, openSettings, openWorldHub, refreshAll, restorePlayer, returnToGame, runtimeCommand, selectedProvider, sendDialogue, sendTrade, showEntry, showGameScreen, startStream, submitEntry, unlockOperator, unlockOperatorFromEntry, waitFor, waitOneMinute } from "./actions.js";
import { unlockFromOwnerLink } from "./api.js";
import { armSoundOnGesture, renderSoundButton, toggleSound } from "./audio.js";
import { shareChapter } from "./celebration.js";
import { $, OPERATOR_KEY, SOUND_KEY, icon, state, storageGet, toast } from "./core.js";
import { renderActions, renderMobileActionBar, renderResultBeat, renderTask, renderTaskHint, renderVenture } from "./render_actions.js";
import { renderCharacter, renderClock } from "./render_character.js";
import { closeLedger, openLedger, renderLedger } from "./render_ledger.js";
import { openCreatePanel } from "./render_entry.js";
import { closeCharacter, closePlayerSheet, nearbyParticipants, openPlayerSheet, renderFeed, renderMiniMap, renderNpcIntentions, renderPeople, renderTalkTargets, switchTab, updateCharacterSheet } from "./render_panels.js";
import { renderCrisisStrip, renderScene } from "./render_scene.js";
import { renderHoldings } from "./render_holdings.js";
import { closeEnding, dismissRecap, renderEnding, renderFacilities, renderGoals, renderOnboarding, renderRecap, renderWorldProperties } from "./render_world.js";


export function renderAll() {
    if (!state.view) return;
    renderClock();
    renderCharacter();
    renderVenture();
    renderHoldings();
    renderScene();
    renderNpcIntentions();
    renderCrisisStrip();
    renderMiniMap();
    renderTask();
    renderActions();
    renderResultBeat();
    renderMobileActionBar();
    renderOnboarding();
    renderRecap();
    renderGoals();
    renderWorldProperties();
    renderFacilities();
    renderLedger();
    renderEnding();
    renderTalkTargets();
    renderPeople();
    if (state.selectedCharacter) {
      const resident = nearbyParticipants().find((person) => person.id === state.selectedCharacter.id);
      if (resident) updateCharacterSheet(resident);
      else closeCharacter();
    }
    renderTaskHint();
    $("last-update").textContent = `同步于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  }


  /* ---------- Wiring ---------- */

export function wireUi() {
    for (const button of document.querySelectorAll(".game-nav button")) {
      button.addEventListener("click", () => showGameScreen(button.dataset.screen));
    }

    $("open-holdings").addEventListener("click", () => showGameScreen("holdings"));

    $("hud-worlds").innerHTML = icon("globe");
    $("hud-sound").innerHTML = icon("soundOff");
    $("hud-settings").innerHTML = icon("settings");
    $("settings-close").innerHTML = "";
    $("entry-worlds-refresh").innerHTML = icon("refresh");

    state.soundOn = Boolean(storageGet(SOUND_KEY, false));
    renderSoundButton();

    $("settings-refresh").addEventListener("click", () => refreshAll());
    $("hud-worlds").addEventListener("click", () => openWorldHub());
    $("hud-sound").addEventListener("click", toggleSound);
    $("hud-settings").addEventListener("click", openSettings);
    $("recap-dismiss").addEventListener("click", dismissRecap);
    $("open-ledger").addEventListener("click", openLedger);
    $("ledger-close").addEventListener("click", closeLedger);
    for (const scrim of document.querySelectorAll("[data-close-ledger]")) scrim.addEventListener("click", closeLedger);
    $("ending-continue").addEventListener("click", () => closeEnding(true));
    $("ending-share").addEventListener("click", () => shareChapter().catch(() => {}));
    $("ending-ledger").addEventListener("click", () => {
      closeEnding(true);
      openLedger();
    });
    $("ending-overlay").addEventListener("click", (event) => {
      if (event.target === $("ending-overlay")) closeEnding(true);
    });
    $("settings-close").addEventListener("click", closeSettings);
    for (const scrim of document.querySelectorAll("[data-close-drawer]")) scrim.addEventListener("click", closeSettings);
    $("settings-provider").addEventListener("change", () => {
      const value = String($("settings-provider").value || "");
      try { localStorage.setItem("sim2.game.provider", value || "default"); } catch (error) { /* ignore */ }
      if (state.playerId) toast(`同步推进将使用模型：${value || "默认"}`, "ok");
      renderTask();
    });

    $("entry-submit").addEventListener("click", (event) => submitEntry(event.currentTarget));
    $("entry-name").addEventListener("keydown", (event) => {
      if (event.key === "Enter") submitEntry($("entry-submit"));
    });
    $("entry-create-toggle").addEventListener("click", openCreatePanel);
    $("entry-browse-worlds").addEventListener("click", openCreatePanel);
    $("entry-open-editor").addEventListener("click", openEditor);
    $("new-instance-submit").addEventListener("click", (event) => createWorldInstance(event.currentTarget));
    $("entry-worlds-refresh").addEventListener("click", () => loadEntryContext());
    $("entry-operator-unlock").addEventListener("click", unlockOperatorFromEntry);
    $("entry-return").addEventListener("click", returnToGame);
    $("settings-open-worlds").addEventListener("click", () => {
      closeSettings();
      openWorldHub();
    });
    $("settings-open-editor").addEventListener("click", () => {
      closeSettings();
      openEditor();
    });
    $("settings-open-admin").addEventListener("click", () => openAdmin(""));
    $("editor-close").addEventListener("click", () => closeEditor());
    $("editor-external").addEventListener("click", () => openAdmin("authoring-view"));

    $("task-advance").addEventListener("click", (event) => {
      event.currentTarget.disabled = true;
      advanceTask("继续：").finally(() => renderTask());
    });
    $("task-wait").addEventListener("click", (event) => waitOneMinute(event.currentTarget));
    for (const button of document.querySelectorAll("[data-wait-minutes]")) {
      button.addEventListener("click", (event) => waitFor(Number(event.currentTarget.dataset.waitMinutes), event.currentTarget));
    }
    $("player-details").addEventListener("click", openPlayerSheet);
    $("scene-player-bag").addEventListener("click", openPlayerSheet);
    $("player-sheet-close").addEventListener("click", closePlayerSheet);
    for (const scrim of document.querySelectorAll("[data-close-player]")) scrim.addEventListener("click", closePlayerSheet);
    $("action-more").addEventListener("click", () => {
      state.actionExpanded = !state.actionExpanded;
      renderActions();
      renderMobileActionBar();
    });
    $("mobile-primary").addEventListener("click", (event) => {
      const bar = $("mobile-action-bar");
      if (state.view?.active_task?.bt || state.view?.active_task?.action) {
        advanceTask("继续：");
      } else if (bar._opportunity) {
        $("opportunity-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (bar._action) {
        beginAction(bar._action, event.currentTarget);
      }
    });
    $("mobile-more").addEventListener("click", () => {
      document.querySelector(".actions-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("player-leave").addEventListener("click", () => showEntry("已回到世界大厅。"));

    $("talk-form").addEventListener("submit", (event) => {
      event.preventDefault();
      sendDialogue();
    });
    $("talk-target").addEventListener("change", () => {
      const person = nearbyParticipants().find((item) => item.id === $("talk-target").value);
      if (!person) return;
      state.selectedCharacter = person;
      updateCharacterSheet(person);
      loadDialogue(person.id);
    });
    $("character-close").addEventListener("click", closeCharacter);
    for (const scrim of document.querySelectorAll("[data-close-character]")) scrim.addEventListener("click", closeCharacter);
    $("trade-form").addEventListener("submit", (event) => {
      event.preventDefault();
      sendTrade(event.currentTarget);
    });

    for (const button of $("right-tabs").querySelectorAll("[data-tab]")) {
      button.addEventListener("click", () => switchTab(button.dataset.tab));
    }
    $("open-map").addEventListener("click", openMap);
    $("mini-map-expand").addEventListener("click", openMap);
    $("feed-more").addEventListener("click", () => {
      state.feedLimit += 28;
      renderFeed();
    });

    $("instance-activate").addEventListener("click", activateInstance);
    $("instance-refresh").addEventListener("click", loadInstances);
    $("operator-unlock").addEventListener("click", unlockOperator);
    $("runtime-start").addEventListener("click", () => runtimeCommand("start", {
      provider: selectedProvider() || undefined,
      max_world_minutes: 1440,
      max_llm_calls: 120,
    }));
    $("runtime-pause").addEventListener("click", () => runtimeCommand("pause", { reason: "game-settings" }));
    $("runtime-stop").addEventListener("click", () => runtimeCommand("stop", { reason: "game-settings" }));
    $("runtime-step").addEventListener("click", () => runtimeCommand("step", {}));

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("ending-overlay").classList.contains("hidden")) {
        closeEnding(true);
        return;
      }
      if (!$("editor-overlay").classList.contains("hidden")) {
        closeEditor();
        return;
      }
      if (!$("player-drawer").classList.contains("hidden")) {
        closePlayerSheet();
        return;
      }
      if (!$("character-drawer").classList.contains("hidden")) {
        closeCharacter();
        return;
      }
      if (!$("settings-drawer").classList.contains("hidden")) closeSettings();
    });
    window.addEventListener("message", (event) => {
      if (event.origin !== location.origin) return;
      if (event.data?.type === "sim2:close-editor") closeEditor();
    });
  }


export async function boot() {
    if (state.booted) return;
    state.booted = true;
    try {
      state.operatorToken = sessionStorage.getItem(OPERATOR_KEY) || sessionStorage.getItem("sim2_operator_token") || null;
    } catch (error) {
      state.operatorToken = null;
    }
    wireUi();
    armSoundOnGesture();
    await unlockFromOwnerLink();
    await loadEntryContext();
    const restored = await restorePlayer();
    if (!restored) {
      $("entry-screen").classList.remove("hidden");
      $("game").classList.add("hidden");
    }
    if (restored) startStream();
    state.pollTimer = window.setInterval(() => {
      if (!state.playerId || document.hidden || state.advancing) return;
      if (state.streamConnected) {
        if (Array.isArray(state.view?.ui?.chronicle_events) && Number(state.runtime?.world_minute) > Number(state.view?.server_minute)) {
          loadView().catch(() => {});
        }
        return;
      }
      loadView().catch(() => {});
      loadRuntime();
      loadFeed();
    }, 15000);
    window.setInterval(() => {
      if (!state.playerId || document.hidden || state.advancing) return;
      loadTrades();
      if (!$("character-drawer").classList.contains("hidden")) loadDialogue();
    }, 30000);
  }


  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
