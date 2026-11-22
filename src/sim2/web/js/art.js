import { $, crisisMood, state } from "./core.js";


  /* ---------- Entry / world hub ---------- */

export function hashString(value) {
    let hash = 2166136261;
    for (const char of String(value || "sim2")) {
      hash ^= char.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return Math.abs(hash);
  }


export function worldPalette(worldId) {
    const palettes = [
      { skyA: "#0b2c31", skyB: "#061416", a: "#2b6d6a", b: "#153c3c", c: "#3f8f86", sun: "#efb26f" },
      { skyA: "#2b2038", skyB: "#0d0a18", a: "#6a4d86", b: "#352547", c: "#8b6aa8", sun: "#f0c27a" },
      { skyA: "#1c3325", skyB: "#08140d", a: "#3f6f4b", b: "#1b3a25", c: "#5f9367", sun: "#d9d38a" },
      { skyA: "#37251c", skyB: "#140b08", a: "#7d5037", b: "#42291b", c: "#a06c49", sun: "#f2b277" },
    ];
    const palette = palettes[hashString(worldId) % palettes.length];
    return {
      ...palette,
      hue: hashString(worldId) % 360,
    };
  }


export function renderEntryCover() {
    const cover = $("entry-cover");
    if (!cover) return;
    cover.dataset.crisis = crisisMood(state.world);
    const worldId = state.world?.id || state.world?.world_id || state.world?.title || "sim2";
    const palette = worldPalette(worldId);
    const seed = hashString(worldId);
    const stars = Array.from({ length: 18 }, (_, index) => {
      const x = ((seed >> (index % 12)) + index * 137) % 1200;
      const y = ((seed >> ((index + 3) % 10)) + index * 53) % 84;
      const r = index % 4 === 0 ? 1.5 : 0.8;
      return `<circle cx="${x}" cy="${y}" r="${r}" fill="rgba(255,255,255,${0.18 + (index % 5) * 0.08})"/>`;
    }).join("");
    const ridges = [
      { y: 92, amp: 14, phase: seed % 19, color: palette.c, opacity: 0.32 },
      { y: 108, amp: 18, phase: (seed >> 3) % 23, color: palette.a, opacity: 0.5 },
      { y: 126, amp: 11, phase: (seed >> 6) % 17, color: palette.b, opacity: 0.85 },
    ].map((ridge) => {
      const points = Array.from({ length: 9 }, (_, index) => {
        const x = index * 150;
        const y = ridge.y - Math.sin((index + ridge.phase) * 1.37) * ridge.amp - ((seed >> (index % 8)) % 7);
        return `${x},${Math.max(54, Math.min(136, y)).toFixed(1)}`;
      });
      return `<path d="M0 140 L${points[0]} L${points.slice(1).join(" L")} L1200 140 Z" fill="${ridge.color}" opacity="${ridge.opacity}"/>`;
    }).join("");
    const rain = `<g class="art-rain">${Array.from({ length: 30 }, (_, index) => {
      const x = (index * 53 + (seed % 37)) % 1200;
      const y = (index * 19) % 140;
      return `<line x1="${x}" y1="${y}" x2="${x - 7}" y2="${y + 36}" stroke="rgba(210,230,235,0.42)" stroke-width="1"/>`;
    }).join("")}</g>`;
    cover.innerHTML = `
      <svg viewBox="0 0 1200 140" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="entry-sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${palette.skyA}"/>
            <stop offset="100%" stop-color="${palette.skyB}"/>
          </linearGradient>
        </defs>
        <rect width="1200" height="140" fill="url(#entry-sky)"/>
        ${stars}
        <circle cx="${180 + (seed % 720)}" cy="${42 + (seed % 18)}" r="17" fill="${palette.sun}" opacity="0.72"/>
        <circle cx="${180 + (seed % 720)}" cy="${42 + (seed % 18)}" r="30" fill="${palette.sun}" opacity="0.08"/>
        ${ridges}
        ${rain}
      </svg>`;
  }


export function paintWorldCard(card, worldId) {
    const palette = worldPalette(worldId);
    card.style.setProperty("--cover-a", palette.a);
    card.style.setProperty("--cover-b", palette.b);
  }


export function artStars(seed, count = 30, maxY = 190) {
    return Array.from({ length: count }, (_, index) => {
      const x = ((seed >> (index % 11)) + index * 151) % 1200;
      const y = ((seed >> ((index + 2) % 9)) + index * 41) % maxY;
      const r = index % 5 === 0 ? 1.6 : 0.85;
      const delay = ((seed + index * 37) % 80) / 10;
      return `<circle class="art-star" style="animation-delay:${delay}s" cx="${x}" cy="${y}" r="${r}" fill="rgba(255,255,255,${0.16 + (index % 5) * 0.08})"/>`;
    }).join("");
  }


export function artReeds(seed, baseY, color, count = 22) {
    return Array.from({ length: count }, (_, index) => {
      const x = 30 + index * 55 + ((seed >> (index % 7)) % 26);
      const height = 42 + ((seed >> (index % 9)) % 74);
      const lean = index % 2 ? 8 : -7;
      return `<path d="M${x} ${baseY} Q${x + lean * 0.4} ${baseY - height * 0.58} ${x + lean} ${baseY - height}" stroke="${color}" stroke-width="${index % 4 === 0 ? 2.2 : 1.2}" fill="none" opacity="${0.32 + (index % 3) * 0.12}"/>`;
    }).join("");
  }


export function renderSceneWorldCover(theme = "harbor") {
    const root = $("scene-world-cover");
    if (!root) return;
    const worldId = state.world?.world_id || state.world?.title || "sim2";
    const cacheKey = `${worldId}|${theme}`;
    if (root.dataset.world === cacheKey) return;
    root.dataset.world = cacheKey;
    const p = theme === "sky"
      ? { skyA: "#9fd4ea", skyB: "#2e607c", a: "#7aa77c", b: "#355f58", c: "#f4d9a8", sun: "#ffe08a", hue: 198 }
      : worldPalette(worldId);
    const seed = hashString(worldId);
    const moonX = 860 + (seed % 140);
    const moonY = 86 + (seed % 38);
    const stars = artStars(seed);
    const mist = `<ellipse class="art-mist" cx="${260 + (seed % 500)}" cy="250" rx="360" ry="26" fill="url(#scene-mist)"/>`;
    const rain = `<g class="art-rain">${Array.from({ length: 18 }, (_, index) => {
      const x = (index * 71 + (seed % 40)) % 1200;
      return `<line x1="${x}" y1="${(index * 23) % 180}" x2="${x - 8}" y2="${((index * 23) % 180) + 58}" stroke="rgba(190,220,232,0.22)" stroke-width="1"/>`;
    }).join("")}</g>`;
    const water = `<path d="M0 250 C160 242 280 256 420 250 S760 240 900 250 S1100 256 1200 246 L1200 420 L0 420Z" fill="url(#scene-water)"/>`;
    const seaGlints = Array.from({ length: 12 }, (_, index) => {
      const y = 270 + index * 11;
      const width = 34 + (index % 4) * 24;
      const x = 80 + ((seed >> (index % 8)) + index * 97) % 980;
      return `<rect x="${x}" y="${y}" width="${width}" height="1.2" rx="0.6" fill="${p.sun}" opacity="${0.05 + (index % 4) * 0.025}"/>`;
    }).join("");
    let scene = "";
    if (theme === "sky") {
      const cloud = (x, y, scale, opacity) => `<g opacity="${opacity}" fill="rgba(255,255,255,0.72)"><ellipse cx="${x}" cy="${y}" rx="${70 * scale}" ry="${16 * scale}"/><ellipse cx="${x - 46 * scale}" cy="${y + 5 * scale}" rx="${42 * scale}" ry="${12 * scale}"/><ellipse cx="${x + 52 * scale}" cy="${y + 6 * scale}" rx="${48 * scale}" ry="${13 * scale}"/></g>`;
      const island = (x, y, scale, dark) => `
        <g transform="translate(${x} ${y}) scale(${scale})" opacity=".95">
          <path d="M-120 0 C-88 -22 -40 -28 0 -22 C42 -32 92 -20 122 0 C86 18 42 24 0 18 C-42 24 -88 18 -120 0Z" fill="${dark ? p.b : p.a}"/>
          <path d="M-120 0 C-88 -22 -40 -28 0 -22 C42 -32 92 -20 122 0" stroke="rgba(255,255,255,0.35)" stroke-width="2" fill="none"/>
          <path d="M-86 8 C-52 18 -18 22 18 18 C56 22 82 14 100 6" stroke="rgba(0,0,0,0.18)" stroke-width="3" fill="none"/>
          <path d="M-62 -6 L-54 -34 L-42 -8Z" fill="${dark ? p.c : p.a}" opacity=".8"/>
          <path d="M18 -10 L28 -44 L42 -12Z" fill="${dark ? p.c : p.a}" opacity=".75"/>
        </g>`;
      scene = `
        <rect width="1200" height="420" fill="url(#scene-sky)"/>
        <circle cx="${700 + (seed % 180)}" cy="${72 + (seed % 30)}" r="46" fill="${p.sun}" opacity=".72"/>
        <circle cx="${700 + (seed % 180)}" cy="${72 + (seed % 30)}" r="108" fill="${p.sun}" opacity=".1"/>
        ${cloud(160, 74, 1.2, 0.7)}${cloud(980, 116, 0.9, 0.55)}${cloud(520, 54, 0.65, 0.5)}
        ${island(180, 300, 1.05, true)}
        ${island(920, 268, 0.85, false)}
        ${island(1080, 170, 0.42, true)}
        ${island(360, 140, 0.36, false)}
        <g class="art-airship" transform="translate(560 150)">
          <ellipse cx="0" cy="-34" rx="78" ry="34" fill="${p.sun}" opacity=".28"/>
          <ellipse cx="0" cy="-38" rx="70" ry="30" fill="rgba(255,247,225,0.82)"/>
          <path d="M-70 -38 C-34 -66 34 -66 70 -38" stroke="rgba(255,255,255,0.5)" stroke-width="2" fill="none"/>
          <path d="M-44 0 L-32 34 L32 34 L44 0Z" fill="#3d514c"/>
          <path d="M-24 0 L-18 20 L18 20 L24 0Z" fill="${p.c}" opacity=".65"/>
          <path d="M-44 -4 L-76 26 M44 -4 L76 26" stroke="#3d514c" stroke-width="2"/>
          <path d="M-4 -8 L-4 34" stroke="#3d514c" stroke-width="2"/>
          <path d="M-30 -18 L-30 -4 M30 -18 L30 -4" stroke="#3d514c" stroke-width="2"/>
          <circle cx="-24" cy="10" r="3" fill="${p.sun}"/><circle cx="24" cy="10" r="3" fill="${p.sun}"/>
        </g>
        <g stroke="rgba(255,255,255,0.55)" stroke-width="1.4" fill="none" class="art-birds">
          <path d="M340 110 Q350 102 360 110 Q370 102 380 110"/>
          <path d="M430 88 Q438 81 446 88 Q454 81 462 88"/>
          <path d="M1010 214 Q1020 207 1030 214 Q1040 207 1050 214"/>
        </g>
        <path d="M0 420 L0 382 C160 362 300 392 460 380 C620 368 760 396 920 382 C1060 370 1140 390 1200 380 L1200 420Z" fill="rgba(28,54,60,0.18)"/>
        <ellipse class="art-mist" cx="${300 + (seed % 500)}" cy="360" rx="420" ry="38" fill="url(#scene-mist)"/>`;
    } else if (theme === "gatehouse") {
      scene = `
        <path d="M0 0 L1200 0 L1200 130 C1020 168 900 96 760 126 C620 156 500 122 380 138 C250 154 120 128 0 150Z" fill="${p.skyB}" opacity=".78"/>
        ${stars}
        <circle cx="${moonX}" cy="${moonY}" r="24" fill="${p.sun}" opacity=".5"/>
        <circle cx="${moonX}" cy="${moonY}" r="58" fill="${p.sun}" opacity=".07"/>
        ${water}
        <path d="M0 262 C180 250 320 266 470 258 S780 246 940 260 S1110 264 1200 254" stroke="${p.c}" stroke-opacity=".28" stroke-width="2" fill="none"/>
        ${seaGlints}
        <g fill="#071017" opacity=".96">
          <path d="M250 420 L250 128 L330 84 L420 128 L420 420Z"/>
          <path d="M780 420 L780 116 L870 70 L970 116 L970 420Z"/>
          <rect x="285" y="150" width="100" height="240" fill="${p.b}" opacity=".55"/>
          <rect x="820" y="142" width="110" height="248" fill="${p.b}" opacity=".55"/>
          <path d="M420 196 L780 184 L780 228 L420 238Z" fill="#09141a"/>
          <path d="M420 238 L780 228 L780 420 L420 420Z" fill="#041014" opacity=".55"/>
        </g>
        <g stroke="${p.sun}" stroke-opacity=".35" fill="none">
          <path d="M470 196 L470 420 M505 194 L505 420 M540 192 L540 420 M575 190 L575 420 M610 188 L610 420 M645 186 L645 420 M680 184 L680 420 M715 184 L715 420" stroke-width="2"/>
          <circle cx="600" cy="300" r="58" stroke-width="3"/>
          <circle cx="600" cy="300" r="18" stroke-width="2"/>
          <path d="M600 242 L600 358 M542 300 L658 300 M559 259 L641 341 M641 259 L559 341" stroke-width="1.4"/>
        </g>
        <g class="art-beam" opacity=".55">
          <path d="M952 158 L1200 112 L1200 196 Z" fill="${p.sun}" opacity=".18"/>
          <path d="M952 158 L1200 132 L1200 172 Z" fill="${p.sun}" opacity=".14"/>
        </g>
        <g>
          <rect x="930" y="150" width="24" height="86" rx="5" fill="#0a1a20" stroke="${p.c}" stroke-opacity=".5"/>
          <circle cx="942" cy="150" r="15" fill="${p.sun}" opacity=".85"/>
          <circle cx="942" cy="150" r="28" fill="${p.sun}" opacity=".12"/>
        </g>
        ${rain}
        ${mist}
        <path d="M0 380 C120 368 220 386 320 378 S520 366 620 380 S840 386 940 372 S1100 366 1200 380 L1200 420 L0 420Z" fill="${p.skyB}" opacity=".6"/>`;
    } else if (theme === "workshop") {
      scene = `
        <rect width="1200" height="420" fill="url(#scene-interior)"/>
        <g stroke="${p.b}" stroke-opacity=".6" fill="none">
          <path d="M0 56 L1200 18" stroke-width="10"/>
          <path d="M0 96 L1200 58" stroke-width="6"/>
          <path d="M120 0 L200 100 M380 0 L440 92 M660 0 L700 84 M920 0 L960 76" stroke-width="4"/>
        </g>
        <rect x="828" y="54" width="250" height="176" rx="6" fill="#07131b" stroke="${p.c}" stroke-opacity=".42" stroke-width="2"/>
        <path d="M953 54 L953 230 M828 142 L1078 142" stroke="${p.b}" stroke-width="5" opacity=".8"/>
        <g class="art-rain" clip-path="url(#scene-window-clip)">
          ${Array.from({ length: 10 }, (_, index) => `<line x1="${850 + index * 23}" y1="${64 + (index % 3) * 8}" x2="${844 + index * 23}" y2="${130 + (index % 4) * 22}" stroke="rgba(190,220,232,0.25)" stroke-width="1"/>`).join("")}
        </g>
        <g opacity=".9">
          <path d="M0 332 L1200 318 L1200 420 L0 420Z" fill="#0a1512"/>
          <path d="M0 332 L1200 318" stroke="${p.sun}" stroke-opacity=".18" stroke-width="2"/>
          <rect x="90" y="260" width="340" height="22" rx="4" fill="#1a2018"/>
          <rect x="118" y="282" width="18" height="86" fill="#111812"/>
          <rect x="382" y="282" width="18" height="86" fill="#111812"/>
          <g fill="#263027">
            <rect x="150" y="236" width="36" height="24" rx="3"/>
            <rect x="198" y="228" width="18" height="32" rx="2"/>
            <rect x="230" y="240" width="46" height="20" rx="3"/>
          </g>
        </g>
        <g stroke="${p.c}" stroke-opacity=".55" fill="none">
          <path d="M760 0 L760 330 M760 330 L900 330" stroke-width="9"/>
          <path d="M790 330 L790 388 M860 330 L860 388" stroke-width="5"/>
          <rect x="740" y="318" width="176" height="70" rx="10" fill="#12201e" stroke-width="2"/>
        </g>
        <g fill="${p.sun}" opacity=".8">
          <circle cx="708" cy="160" r="9"/>
          <circle cx="650" cy="188" r="6"/>
          <circle cx="790" cy="150" r="7"/>
        </g>
        <circle cx="708" cy="160" r="50" fill="${p.sun}" opacity=".08"/>
        <g stroke="${p.c}" stroke-opacity=".4" fill="none">
          <path d="M520 88 L520 220 M540 100 L540 214 M560 108 L560 208" stroke-width="2"/>
          <circle cx="540" cy="224" r="14"/>
          <path d="M610 92 L610 196" stroke-width="3"/>
          <path d="M596 196 L624 196 L620 224 L600 224Z" fill="#0a1718"/>
        </g>
        ${mist}`;
    } else if (theme === "observatory") {
      scene = `
        ${stars}
        <circle cx="${moonX}" cy="${moonY}" r="34" fill="${p.sun}" opacity=".55"/>
        <circle cx="${moonX}" cy="${moonY}" r="82" fill="${p.sun}" opacity=".06"/>
        <ellipse cx="700" cy="120" rx="260" ry="110" fill="${p.c}" opacity=".08"/>
        <path d="M0 280 C160 268 280 284 420 278 S760 266 900 280 S1100 286 1200 274 L1200 420 L0 420Z" fill="url(#scene-water)" opacity=".82"/>
        <path d="M0 292 C180 280 320 296 460 288 S780 278 940 292 S1110 296 1200 286" stroke="${p.c}" stroke-opacity=".24" stroke-width="2" fill="none"/>
        <path d="M0 420 L0 338 C120 314 210 330 300 318 C390 306 470 328 560 332 C660 336 760 314 860 322 C980 332 1080 318 1200 338 L1200 420Z" fill="#071017"/>
        <g>
          <path d="M790 330 L810 150 L900 104 L990 150 L1010 330Z" fill="#0a1820" stroke="${p.c}" stroke-opacity=".3"/>
          <path d="M810 150 L990 150 L960 108 L840 108Z" fill="#0d2028"/>
          <circle cx="900" cy="128" r="30" fill="#0a141a" stroke="${p.c}" stroke-opacity=".55" stroke-width="2"/>
          <circle cx="900" cy="128" r="17" fill="${p.sun}" opacity=".8"/>
          <circle cx="900" cy="128" r="50" fill="${p.sun}" opacity=".1"/>
          <path d="M900 128 L1200 88 L1200 162 Z" class="art-beam" fill="${p.sun}" opacity=".11"/>
          <path d="M830 330 L830 180 M970 330 L970 180" stroke="${p.c}" stroke-opacity=".24"/>
          <path d="M848 330 L848 210 M952 330 L952 210" stroke="${p.c}" stroke-opacity=".18"/>
        </g>
        <g fill="${p.c}" opacity=".5">
          <path d="M700 380 L720 350 L738 380Z"/>
          <path d="M740 388 L758 362 L776 388Z"/>
        </g>
        ${mist}`;
    } else if (theme === "marsh") {
      scene = `
        <circle cx="${760 + (seed % 160)}" cy="${132 + (seed % 30)}" r="34" fill="${p.sun}" opacity=".38"/>
        <circle cx="${760 + (seed % 160)}" cy="${132 + (seed % 30)}" r="82" fill="${p.sun}" opacity=".06"/>
        <path d="M0 238 C140 224 260 244 400 232 S700 218 860 236 S1080 242 1200 226 L1200 420 L0 420Z" fill="url(#scene-marsh-water)"/>
        <path d="M0 252 C180 240 320 258 470 248 S780 236 940 252 S1110 256 1200 246" stroke="${p.c}" stroke-opacity=".26" stroke-width="2" fill="none"/>
        <path d="M0 300 C160 286 280 308 430 298 S760 284 920 302 S1110 306 1200 294 L1200 420 L0 420Z" fill="#0a1a15" opacity=".7"/>
        <path d="M260 420 C300 370 340 350 390 330 C450 306 500 336 540 360 C580 386 620 372 680 340 C740 308 800 342 850 380 L900 420Z" fill="#0c211a"/>
        <path d="M80 420 C120 388 160 372 210 360 C250 350 280 370 300 390 L320 420Z" fill="#0d241c"/>
        ${artReeds(seed + 11, 400, p.c, 24)}
        ${artReeds(seed + 29, 410, p.a, 18)}
        <g fill="#071410" opacity=".85">
          <path d="M930 300 L940 260 L950 300Z"/>
          <path d="M960 304 L970 268 L980 304Z"/>
          <path d="M910 306 L918 276 L926 306Z"/>
        </g>
        <g stroke="rgba(220,235,225,0.35)" stroke-width="1.2" fill="none" class="art-birds">
          <path d="M520 160 Q530 152 540 160 Q550 152 560 160"/>
          <path d="M600 136 Q608 129 616 136 Q624 129 632 136"/>
        </g>
        ${mist}`;
    } else if (theme === "lantern") {
      scene = `
        <rect width="1200" height="420" fill="url(#scene-interior-warm)"/>
        <g stroke="#3a2418" stroke-opacity=".9" fill="none">
          <path d="M0 52 L1200 20" stroke-width="12"/>
          <path d="M0 86 L1200 54" stroke-width="7"/>
          <path d="M180 0 L260 104 M520 0 L560 96 M900 0 L940 88" stroke-width="5"/>
        </g>
        <g fill="#2a1a12" opacity=".7">
          <rect x="60" y="120" width="300" height="210" rx="8"/>
          <rect x="60" y="120" width="300" height="14" fill="#3b2619"/>
          <rect x="860" y="110" width="280" height="220" rx="8"/>
          <rect x="860" y="110" width="280" height="14" fill="#3b2619"/>
        </g>
        <rect x="870" y="152" width="200" height="120" rx="4" fill="#07131b" stroke="#4a3020" stroke-width="4"/>
        <g class="art-rain">
          ${Array.from({ length: 9 }, (_, index) => `<line x1="${890 + index * 20}" y1="${162 + (index % 3) * 6}" x2="${884 + index * 20}" y2="${220 + (index % 4) * 14}" stroke="rgba(180,210,225,0.22)" stroke-width="1"/>`).join("")}
        </g>
        <g class="art-ember">
          <circle cx="250" cy="300" r="3" fill="${p.sun}" opacity=".8"/>
          <circle cx="285" cy="274" r="2" fill="${p.sun}" opacity=".65"/>
          <circle cx="220" cy="250" r="1.6" fill="${p.sun}" opacity=".6"/>
        </g>
        <g>
          <path d="M180 420 L180 356 C220 334 300 334 340 356 L340 420Z" fill="#1b120c"/>
          <ellipse cx="260" cy="360" rx="54" ry="24" fill="${p.sun}" opacity=".18"/>
          <path d="M235 360 C240 334 252 326 260 314 C270 332 284 340 282 360 C278 378 248 382 235 360Z" fill="${p.sun}" opacity=".75"/>
          <path d="M250 362 C254 348 260 344 264 338 C270 350 272 356 268 366 C264 376 252 374 250 362Z" fill="#fff1cf" opacity=".7"/>
        </g>
        <g>
          <path d="M520 0 L520 58" stroke="#2a1a12" stroke-width="3"/>
          <rect x="500" y="58" width="40" height="54" rx="8" fill="${p.sun}" opacity=".55"/>
          <rect x="506" y="64" width="28" height="42" rx="6" fill="#ffe5b8" opacity=".35"/>
          <path d="M700 0 L700 38" stroke="#2a1a12" stroke-width="3"/>
          <rect x="684" y="38" width="32" height="44" rx="7" fill="${p.sun}" opacity=".5"/>
        </g>
        <g opacity=".9">
          <path d="M380 420 L380 380 L760 380 L760 420Z" fill="#1a110b"/>
          <path d="M400 380 L400 352 L520 352 L520 380Z" fill="#2a1a12"/>
          <path d="M560 380 L560 342 L700 342 L700 380Z" fill="#241610"/>
        </g>
        ${mist}`;
    } else {
      const quayExtra = theme === "quay" ? `
        <g>
          <path d="M90 300 L90 250 L250 250 L250 300Z" fill="#1b1710"/>
          <path d="M80 250 L260 250 L240 226 L100 226Z" fill="${p.sun}" opacity=".18"/>
          <rect x="120" y="258" width="34" height="34" rx="3" fill="#2a2116"/>
          <rect x="164" y="264" width="28" height="28" rx="3" fill="#33271a"/>
          <path d="M250 300 L250 210 M250 220 L300 240 L250 260" stroke="#0b1414" stroke-width="3" fill="none"/>
          <circle cx="300" cy="244" r="5" fill="${p.sun}" opacity=".8"/>
        </g>` : `
        <g>
          <path d="M790 300 L850 300 L866 326 L774 326Z" fill="#0b1414"/>
          <path d="M820 300 L820 230" stroke="#0b1414" stroke-width="4"/>
          <path d="M820 232 L880 280 L820 280Z" fill="${p.c}" opacity=".22"/>
          <path d="M820 242 L776 280 L820 280Z" fill="${p.c}" opacity=".15"/>
        </g>`;
      scene = `
        ${stars}
        <circle cx="${moonX}" cy="${moonY}" r="30" fill="${p.sun}" opacity=".62"/>
        <circle cx="${moonX}" cy="${moonY}" r="72" fill="${p.sun}" opacity=".08"/>
        <path d="M0 246 C140 238 240 252 360 246 S620 236 760 248 S1020 254 1200 242 L1200 420 L0 420Z" fill="url(#scene-water)"/>
        <path d="M0 258 C160 248 300 264 440 256 S740 244 880 258 S1080 262 1200 252" stroke="${p.c}" stroke-opacity=".28" stroke-width="2" fill="none"/>
        ${seaGlints}
        <path d="M0 258 L110 226 L150 258Z" fill="${p.b}" opacity=".8"/>
        <path d="M1010 258 L1080 214 L1150 258Z" fill="${p.b}" opacity=".72"/>
        <path d="M940 258 L986 232 L1032 258Z" fill="${p.b}" opacity=".6"/>
        <g opacity=".95">
          <path d="M-20 348 L540 306 L640 420 L-20 420Z" fill="#071416"/>
          <path d="M-20 348 L540 306" stroke="${p.c}" stroke-opacity=".34" stroke-width="2"/>
          <path d="M40 348 L60 420 M160 340 L180 420 M280 332 L300 420 M400 322 L420 420 M520 308 L540 420" stroke="${p.b}" stroke-opacity=".65" stroke-width="3"/>
          <rect x="500" y="286" width="18" height="112" rx="4" fill="#0a1718"/>
          <rect x="626" y="272" width="18" height="126" rx="4" fill="#0a1718"/>
          <circle cx="509" cy="286" r="18" fill="${p.sun}" opacity=".1"/>
          <circle cx="509" cy="286" r="5" fill="${p.sun}" opacity=".8"/>
          <circle cx="635" cy="272" r="24" fill="${p.sun}" opacity=".08"/>
          <circle cx="635" cy="272" r="6" fill="${p.sun}" opacity=".7"/>
        </g>
        ${quayExtra}
        ${mist}
        <path d="M0 374 C120 364 220 382 320 374 S520 362 620 376 S840 382 940 368 S1100 362 1200 378 L1200 420 L0 420Z" fill="${p.skyB}" opacity=".62"/>
        <g stroke="rgba(210,235,230,0.15)" stroke-width="1" fill="none">
          <path d="M40 400 Q120 394 200 400 T360 400 T520 400 T680 400 T840 400 T1000 400 T1160 400"/>
          <path d="M80 410 Q160 404 240 410 T400 410 T560 410 T720 410 T880 410 T1040 410"/>
        </g>`;
    }
    root.innerHTML = `
      <svg viewBox="0 0 1200 420" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="scene-sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${p.skyA}"/>
            <stop offset="100%" stop-color="${p.skyB}"/>
          </linearGradient>
          <linearGradient id="scene-water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${p.c}" stop-opacity=".42"/>
            <stop offset="100%" stop-color="${p.skyB}" stop-opacity=".94"/>
          </linearGradient>
          <linearGradient id="scene-marsh-water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${p.a}" stop-opacity=".55"/>
            <stop offset="100%" stop-color="${p.skyB}" stop-opacity=".9"/>
          </linearGradient>
          <linearGradient id="scene-interior" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#241a12"/>
            <stop offset="62%" stop-color="#15100c"/>
            <stop offset="100%" stop-color="#090b08"/>
          </linearGradient>
          <linearGradient id="scene-interior-warm" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#2d1c12"/>
            <stop offset="58%" stop-color="#1a110b"/>
            <stop offset="100%" stop-color="#0b0907"/>
          </linearGradient>
          <radialGradient id="scene-mist" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="rgba(214,236,232,0.2)"/>
            <stop offset="100%" stop-color="rgba(214,236,232,0)"/>
          </radialGradient>
          <radialGradient id="scene-vignette" cx="50%" cy="48%" r="72%">
            <stop offset="54%" stop-color="#03100f" stop-opacity="0"/>
            <stop offset="100%" stop-color="#03100f" stop-opacity=".5"/>
          </radialGradient>
          <clipPath id="scene-window-clip"><rect x="828" y="54" width="250" height="176" rx="6"/></clipPath>
        </defs>
        ${scene}
        <rect width="1200" height="420" fill="url(#scene-vignette)"/>
      </svg>`;
  }
