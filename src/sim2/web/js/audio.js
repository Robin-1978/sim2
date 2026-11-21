import { $, SOUND_KEY, icon, state, storageSet, toast } from "./core.js";


export function sceneSoundProfile() {
    const theme = $("scene-art")?.dataset.scene || "harbor";
    const profiles = {
      harbor: { filter: 520, drone: 55, volume: 0.3 },
      quay: { filter: 620, drone: 58, volume: 0.3 },
      gatehouse: { filter: 430, drone: 46, volume: 0.34 },
      workshop: { filter: 340, drone: 62, volume: 0.24 },
      observatory: { filter: 760, drone: 44, volume: 0.2 },
      marsh: { filter: 560, drone: 50, volume: 0.26 },
      lantern: { filter: 300, drone: 65, volume: 0.22 },
    };
    return profiles[theme] || profiles.harbor;
  }


export function renderSoundButton() {
    const button = $("hud-sound");
    if (!button) return;
    button.innerHTML = icon(state.soundOn ? "sound" : "soundOff");
    button.classList.toggle("active", state.soundOn);
    button.setAttribute("aria-pressed", state.soundOn ? "true" : "false");
    button.title = state.soundOn ? "关闭环境音" : "开启环境音";
  }


export function startAmbient() {
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return false;
    if (!state.audio) {
      try {
        const ctx = new AudioCtor();
        const master = ctx.createGain();
        master.gain.value = 0;
        master.connect(ctx.destination);
        const buffer = ctx.createBuffer(1, ctx.sampleRate * 4, ctx.sampleRate);
        const data = buffer.getChannelData(0);
        let last = 0;
        for (let index = 0; index < data.length; index += 1) {
          const white = Math.random() * 2 - 1;
          last = (last + 0.02 * white) / 1.02;
          data[index] = last * 3.2;
        }
        const noise = ctx.createBufferSource();
        noise.buffer = buffer;
        noise.loop = true;
        const filter = ctx.createBiquadFilter();
        filter.type = "lowpass";
        filter.Q.value = 0.8;
        const noiseGain = ctx.createGain();
        noiseGain.gain.value = 0.5;
        const drone = ctx.createOscillator();
        drone.type = "sine";
        const droneGain = ctx.createGain();
        droneGain.gain.value = 0.035;
        const lfo = ctx.createOscillator();
        lfo.frequency.value = 0.06;
        const lfoGain = ctx.createGain();
        lfoGain.gain.value = 160;
        lfo.connect(lfoGain);
        lfoGain.connect(filter.frequency);
        noise.connect(filter);
        filter.connect(noiseGain);
        noiseGain.connect(master);
        drone.connect(droneGain);
        droneGain.connect(master);
        noise.start();
        drone.start();
        lfo.start();
        state.audio = { ctx, master, filter, drone };
      } catch (error) {
        state.audio = null;
        return false;
      }
    }
    const audio = state.audio;
    if (audio.ctx.state === "suspended") audio.ctx.resume();
    applyAmbientProfile(audio);
    return true;
  }


export function applyAmbientProfile(audio = state.audio) {
    if (!audio) return;
    const profile = sceneSoundProfile();
    const now = audio.ctx.currentTime;
    audio.filter.frequency.setTargetAtTime(profile.filter, now, 0.8);
    audio.drone.frequency.setTargetAtTime(profile.drone, now, 0.8);
    audio.master.gain.setTargetAtTime(profile.volume, now, 1.2);
  }


export function stopAmbient() {
    if (!state.audio) return;
    const now = state.audio.ctx.currentTime;
    state.audio.master.gain.setTargetAtTime(0, now, 0.35);
  }


export function toggleSound() {
    state.soundOn = !state.soundOn;
    storageSet(SOUND_KEY, state.soundOn);
    renderSoundButton();
    if (!state.soundOn) {
      stopAmbient();
      toast("环境音已关闭", "ok");
      return;
    }
    if (!startAmbient()) {
      state.soundOn = false;
      storageSet(SOUND_KEY, false);
      renderSoundButton();
      toast("当前浏览器不支持环境音", "error");
      return;
    }
    toast("环境音已开启", "ok");
  }


export function armSoundOnGesture() {
    if (!state.soundOn || state.audio) return;
    const resume = () => {
      window.removeEventListener("pointerdown", resume);
      if (state.soundOn) startAmbient();
    };
    window.addEventListener("pointerdown", resume, { once: true });
  }
