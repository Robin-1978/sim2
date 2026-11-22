import { el, state, toast, uiConfig } from "./core.js";


const celebrated = new Set();
const pending = [];
let showing = false;


function markFor(kind) {
    return { coin: "＋", stamp: "✓", confetti: "✦", chapter: "◈" }[kind] || "✓";
}


function playCue(kind) {
    if (!state.soundOn) return;
    try {
      const AudioCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtor) return;
      const ctx = state.audio?.ctx || new AudioCtor();
      if (ctx.state === "suspended") ctx.resume();
      const now = ctx.currentTime;
      const notes = kind === "coin"
        ? [660, 880]
        : kind === "stamp"
          ? [180, 120]
          : kind === "chapter"
            ? [392, 523, 659]
            : [523, 784];
      notes.forEach((frequency, index) => {
        const oscillator = ctx.createOscillator();
        const gain = ctx.createGain();
        oscillator.type = kind === "stamp" ? "triangle" : "sine";
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0.0001, now + index * 0.08);
        gain.gain.exponentialRampToValueAtTime(0.11, now + index * 0.08 + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + index * 0.08 + 0.28);
        oscillator.connect(gain);
        gain.connect(ctx.destination);
        oscillator.start(now + index * 0.08);
        oscillator.stop(now + index * 0.08 + 0.3);
      });
    } catch (error) {
      // Audio is optional; a blocked context must not break the celebration.
    }
}


function particles(kind) {
    if (kind !== "coin" && kind !== "confetti") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
    const layer = document.createElement("div");
    layer.className = "celebration-particles";
    for (let index = 0; index < 18; index += 1) {
      const particle = document.createElement("i");
      particle.className = "celebration-particle";
      particle.style.left = `${10 + Math.random() * 80}%`;
      particle.style.top = `${30 + Math.random() * 30}%`;
      particle.style.animationDelay = `${Math.random() * 0.35}s`;
      particle.style.background = index % 3 === 0 ? "var(--cyan)" : "var(--amber)";
      layer.append(particle);
    }
    document.body.append(layer);
    window.setTimeout(() => layer.remove(), 2400);
}


export function showCelebration({ kind = "stamp", title = "完成", detail = "", share = false, duration = 4600, onClose = null }) {
    const layer = document.getElementById("celebration-layer");
    if (!layer) {
      if (onClose) onClose();
      return;
    }
    layer.replaceChildren();
    const card = el("article", `celebration ${kind}`);
    card.append(el("span", "celebration-mark", markFor(kind)));
    card.append(el("strong", "", title));
    if (detail) card.append(el("p", "", detail));
    if (share && kind === "chapter") {
      const button = el("button", "button ghost celebration-dismiss", "分享这一刻");
      button.type = "button";
      button.addEventListener("click", () => shareChapter().catch(() => {}));
      card.append(button);
    }
    let finished = false;
    let timer = null;
    const finish = () => {
      if (finished) return;
      finished = true;
      if (timer) window.clearTimeout(timer);
      card.remove();
      if (onClose) onClose();
    };
    const close = el("button", "button primary celebration-dismiss", kind === "chapter" ? "继续" : "收下");
    close.type = "button";
    close.addEventListener("click", finish);
    card.append(close);
    layer.append(card);
    particles(kind);
    playCue(kind);
    if (kind !== "chapter") {
      timer = window.setTimeout(finish, duration);
    }
}


function drainCelebrations() {
    if (showing) return;
    const next = pending.shift();
    if (!next) return;
    showing = true;
    showCelebration({
      ...next,
      onClose: () => {
        showing = false;
        drainCelebrations();
      },
    });
  }


function enqueueCelebration(spec) {
    pending.push(spec);
    drainCelebrations();
  }

export function maybeCelebrateEvents(events) {
    const config = uiConfig().celebrations || {};
    for (const event of Array.isArray(events) ? events : []) {
      const eventId = String(event.event_id ?? event.id ?? "");
      const eventType = String(event.event_type || "");
      const definition = config[eventType];
      const actor = String(event.actor_id || event.activity?.actor_id || "");
      if (definition?.player_only && actor && actor !== state.playerId) continue;
      if (!eventType || !definition || celebrated.has(eventId || `${eventType}:${event.world_minute}`)) continue;
      celebrated.add(eventId || `${eventType}:${event.world_minute}`);
      const price = Number(event.public?.price ?? event.public?.quantity ?? 0);
      const detail = [
        String(definition.detail || event.narrative || ""),
        price > 0 ? `金币 +${price}` : "",
      ].filter(Boolean).join(" · ");
      enqueueCelebration({
        kind: String(definition.kind || "stamp"),
        title: String(definition.title || event.narrative || eventType),
        detail,
        share: Boolean(definition.share),
      });
    }
}

export async function shareChapter() {
    const chapter = state.view?.chapter || {};
    const title = String(chapter.title || document.getElementById("ending-title")?.textContent || "我的世界");
    const summary = String(chapter.summary || document.getElementById("ending-summary")?.textContent || "").trim();
    const goal = String(chapter.goal_hint || document.getElementById("ending-goal")?.textContent || "").trim();
    const statItems = [...document.querySelectorAll("#ending-stats .ending-stat")]
      .map((item) => item.textContent.trim())
      .filter(Boolean);
    const stats = statItems.join(" · ");
    const text = [title, summary, goal, stats].filter(Boolean).join("\n");
    try {
      const blob = await shareCardBlob({ title, summary, goal, stats });
      if (blob) {
        const file = new File([blob], "sim2-chapter.png", { type: "image/png" });
        if (navigator.canShare?.({ files: [file] })) {
          await navigator.share({ title, text, files: [file] });
          return;
        }
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "sim2-chapter.png";
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast("分享卡已下载。", "ok");
        return;
      }
    } catch (error) {
      if (error?.name === "AbortError") return;
    }
    if (navigator.share) {
      try {
        await navigator.share({ title, text });
        return;
      } catch (error) {
        if (error?.name === "AbortError") return;
      }
    }
    try {
      await navigator.clipboard.writeText(text);
      toast("章节文字已复制，可以分享给朋友。", "ok");
    } catch (error) {
      toast("当前浏览器不支持自动分享，请手动复制章节文字。", "error");
    }
}
