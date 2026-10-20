"use strict";

// Reference overlay renderer: reads timed overlay JSON (schema/overlay.schema.json) from the feed and draws it.
// All text from the feed is inserted with textContent; only validated numbers and enum values reach innerHTML.

const PITCH_L = 105;
const PITCH_W = 68;
const MAX_CARDS = 4;
// Overlay durations are in match time. When replaying faster than real time they would flash by, so a card also
// stays up for at least this long in real time.
const HOLD_MS = 7000;
const REFRESH_MS = 400;

// Everything the page says itself, in both languages. The sentences on the cards come from the feed already written.
const I18N = {
  en: {
    subtitle: "Evidence-cited match insight on a synthetic feed",
    seed: "Match seed", speed: "Speed", explainer: "Explainer", view: "View", language: "Language", replay: "Replay",
    instant: "Instant", fan: "Fan", analyst: "Analyst", template: "Template",
    live: "AI model, live (checked)", liveNA: "AI model, live (not available here)",
    recorded: (model) => `AI model, recorded run (${model})`, recordedNA: "AI model, recorded run (not available here)",
    caption: "Home attacks right. A filled marker is the home team, a ring is the away team.",
    footer: "Synthetic data only. Every club, player and number is generated. Click a sentence to see the events behind it.",
    connecting: "Connecting to the synthetic feed...", liveStatus: "Live.",
    recordedStatus: (model, date) => `Live. Replaying sentences a real model wrote (${model}, recorded ${date}); the Verifier checks every one again now.`,
    fullTime: "Full time. Click a sentence to see the events behind it, or replay another seed.",
    stopped: (why) => `The feed stopped: ${why}`, recordedSeeds: (seeds) => `Recorded seeds: ${seeds}`,
    detail: "Detail", moment: "Moment", recap: "Full-time recap", kmh: "km/h", covered: "km covered",
    kinds: { goal: "Goal", big_chance: "Big chance", sprint_burst: "Sprint", pressing_spell: "Pressing spell", momentum_swing: "Momentum swing" },
    sources: {
      template: "Template, checked by the Verifier",
      model: "Written by an AI model, checked by the Verifier",
      model_retry: "Written by an AI model after a correction, checked by the Verifier",
    },
  },
  id: {
    subtitle: "Wawasan pertandingan beserta buktinya, dari data sintetis",
    seed: "Seed pertandingan", speed: "Kecepatan", explainer: "Penulis", view: "Tampilan", language: "Bahasa", replay: "Putar ulang",
    instant: "Langsung", fan: "Penggemar", analyst: "Analis", template: "Templat",
    live: "Model AI, langsung (diperiksa)", liveNA: "Model AI, langsung (tidak tersedia di sini)",
    recorded: (model) => `Model AI, hasil rekaman (${model})`, recordedNA: "Model AI, hasil rekaman (tidak tersedia di sini)",
    caption: "Tim tuan rumah menyerang ke kanan. Penanda terisi adalah tuan rumah, cincin adalah tim tamu.",
    footer: "Hanya data sintetis. Setiap klub, pemain, dan angka dibuat otomatis. Klik sebuah kalimat untuk melihat kejadian di baliknya.",
    connecting: "Menghubungkan ke data sintetis...", liveStatus: "Langsung.",
    recordedStatus: (model, date) => `Langsung. Memutar kalimat yang ditulis model sungguhan (${model}, direkam ${date}); Verifier memeriksa setiap kalimat lagi sekarang.`,
    fullTime: "Peluit akhir. Klik sebuah kalimat untuk melihat kejadian di baliknya, atau putar seed lain.",
    stopped: (why) => `Siaran berhenti: ${why}`, recordedSeeds: (seeds) => `Seed yang direkam: ${seeds}`,
    detail: "Rincian", moment: "Momen", recap: "Ringkasan akhir", kmh: "km/jam", covered: "km ditempuh",
    kinds: { goal: "Gol", big_chance: "Peluang besar", sprint_burst: "Lari cepat", pressing_spell: "Tekanan", momentum_swing: "Perubahan momentum" },
    sources: {
      template: "Templat, diperiksa Verifier",
      model: "Ditulis model AI, diperiksa Verifier",
      model_retry: "Ditulis model AI setelah dikoreksi, diperiksa Verifier",
    },
  },
};

const els = {
  form: document.getElementById("controls"),
  seed: document.getElementById("seed"),
  speed: document.getElementById("speed"),
  persona: document.getElementById("persona"),
  explainer: document.getElementById("explainer"),
  language: document.getElementById("language"),
  home: document.getElementById("home"),
  away: document.getElementById("away"),
  score: document.getElementById("score"),
  clock: document.getElementById("clock"),
  markers: document.getElementById("markers"),
  cards: document.getElementById("cards"),
  status: document.getElementById("status"),
  live: document.getElementById("live"),
};

const state = {
  now: 0, scoreboard: null, items: [], cards: new Map(), controller: null,
  lang: "en", capabilities: { model: false, recorded: null },
};

const t = () => I18N[state.lang];

function setStatus(text) {
  els.status.textContent = text;
}

function applyLanguage() {
  document.documentElement.lang = state.lang;
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t()[node.dataset.i18n];
  const { model, recorded } = state.capabilities;
  const liveOption = els.explainer.querySelector('option[value="model"]');
  const recordedOption = els.explainer.querySelector('option[value="recorded"]');
  liveOption.textContent = model ? t().live : t().liveNA;
  recordedOption.textContent = recorded ? t().recorded(recorded.model) : t().recordedNA;
  els.seed.title = recorded ? t().recordedSeeds((recorded.seeds[state.lang] || []).join(", ")) : "";
}

function reset() {
  state.now = 0;
  state.scoreboard = null;
  state.items = [];
  state.cards.clear();
  els.cards.textContent = "";
  els.markers.innerHTML = "";
  els.score.textContent = "0 - 0";
  els.clock.textContent = "00:00";
}

function feedUrl() {
  const seed = Math.min(9999, Math.max(0, Number.parseInt(els.seed.value, 10) || 0));
  const params = new URLSearchParams({
    speed: els.speed.value, persona: els.persona.value, explainer: els.explainer.value, lang: state.lang,
  });
  return `/overlays/seed/${seed}?${params}`;
}

async function readFrames(body, onFrame) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = frame.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("\n");
      if (data) onFrame(JSON.parse(data));
      cut = buffer.indexOf("\n\n");
    }
  }
}

async function start(event) {
  if (event) event.preventDefault();
  if (state.controller) state.controller.abort();
  state.lang = els.language.value;
  applyLanguage();
  reset();
  state.controller = new AbortController();
  setStatus(t().connecting);
  try {
    const response = await fetch(feedUrl(), { signal: state.controller.signal });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      throw new Error(problem.detail || `the feed answered ${response.status}`);
    }
    const { recorded } = state.capabilities;
    setStatus(recorded && els.explainer.value === "recorded"
      ? t().recordedStatus(recorded.model, recorded.recorded_at) : t().liveStatus);
    await readFrames(response.body, handleOverlay);
    setStatus(t().fullTime);
  } catch (error) {
    if (error.name !== "AbortError") setStatus(t().stopped(error.message));
  }
}

function handleOverlay(overlay) {
  state.now = Math.max(state.now, overlay.start_s);
  if (overlay.type === "scoreboard") {
    state.scoreboard = overlay;
  } else {
    state.items.push({ overlay, shownAt: performance.now() });
    if (overlay.type === "key_moment") els.live.textContent = overlay.a11y_text;
  }
  render();
}

function isActive(item, realNow) {
  const { overlay, shownAt } = item;
  const inMatchTime = overlay.start_s <= state.now && state.now < overlay.end_s;
  return inMatchTime || realNow - shownAt < HOLD_MS;
}

function render() {
  renderScoreboard();
  const realNow = performance.now();
  state.items = state.items.filter((item) => isActive(item, realNow));
  const active = state.items
    .map((item) => item.overlay)
    .sort((a, b) => b.priority - a.priority || b.start_s - a.start_s)
    .slice(0, MAX_CARDS);
  renderCards(active);
  renderMarkers(active);
}

function renderScoreboard() {
  const board = state.scoreboard;
  if (!board) return;
  els.home.textContent = board.payload.home;
  els.away.textContent = board.payload.away;
  els.score.textContent = `${board.payload.score.H} - ${board.payload.score.A}`;
  els.clock.textContent = board.payload.clock;
}

function renderCards(active) {
  const ids = new Set(active.map((o) => o.overlay_id));
  for (const [id, node] of state.cards) {
    if (!ids.has(id)) {
      node.remove();
      state.cards.delete(id);
    }
  }
  for (const overlay of active) {
    if (!state.cards.has(overlay.overlay_id)) state.cards.set(overlay.overlay_id, buildCard(overlay));
    els.cards.appendChild(state.cards.get(overlay.overlay_id));
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function idList(eventIds) {
  const list = element("div", "ids");
  list.hidden = true;
  for (const id of eventIds) list.appendChild(element("code", "", id));
  return list;
}

function evidenceButton(label, eventIds) {
  const button = element("button", "sentence", label);
  button.type = "button";
  const list = idList(eventIds);
  button.setAttribute("aria-expanded", "false");
  button.addEventListener("click", () => {
    list.hidden = !list.hidden;
    button.setAttribute("aria-expanded", String(!list.hidden));
  });
  const wrap = element("div");
  wrap.append(button, list);
  return wrap;
}

function buildCard(overlay) {
  const words = I18N[overlay.lang] || t();
  const card = element("article", `card ${overlay.type}`);
  const { payload } = overlay;
  if (overlay.type === "key_moment") {
    const badge = element("span", `badge ${payload.source === "template" ? "template" : "model"}`, words.sources[payload.source] || "");
    card.append(element("h2", "", words.kinds[payload.kind] || words.moment), evidenceButton(payload.headline, overlay.evidence_event_ids), badge);
  } else if (overlay.type === "explanation") {
    card.appendChild(element("h2", "", words.detail));
    const list = element("ul");
    for (const sentence of payload.sentences) {
      const item = element("li");
      item.appendChild(evidenceButton(sentence.text, sentence.evidence_event_ids));
      list.appendChild(item);
    }
    card.appendChild(list);
  } else if (overlay.type === "recap") {
    card.appendChild(element("h2", "", words.recap));
    for (const section of payload.sections) {
      card.appendChild(element("h3", "", section.title));
      const list = element("ul");
      for (const sentence of section.sentences) {
        const item = element("li");
        item.appendChild(evidenceButton(sentence.text, sentence.evidence_event_ids));
        list.appendChild(item);
      }
      card.appendChild(list);
    }
  } else if (overlay.type === "player_tag") {
    card.appendChild(element("h2", "", `#${payload.shirt} ${payload.name}`));
    const chips = element("div", "chips");
    if (payload.speed_chip_kmh !== null) chips.appendChild(element("span", "chip", `${Math.round(payload.speed_chip_kmh)} ${words.kmh}`));
    if (payload.distance_chip_km !== null) chips.appendChild(element("span", "chip", `${Math.round(payload.distance_chip_km)} ${words.covered}`));
    card.appendChild(chips);
  }
  return card;
}

function renderMarkers(active) {
  let html = "";
  for (const overlay of active) {
    if (overlay.type !== "key_moment" || overlay.payload.x === null || overlay.payload.y === null) continue;
    const home = overlay.payload.team === "H";
    const x = home ? overlay.payload.x : PITCH_L - overlay.payload.x;
    const y = home ? overlay.payload.y : PITCH_W - overlay.payload.y;
    const team = home ? "H" : "A";
    html += `<circle class="mk-ring" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="2.6"/>`;
    html += `<circle class="mk ${team}" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="1.5"/>`;
  }
  els.markers.innerHTML = html;
}

async function loadCapabilities() {
  try {
    const response = await fetch("/capabilities");
    state.capabilities = await response.json();
  } catch (error) {
    // Without the capabilities call the page still works with the template explainer.
  }
  const { model, recorded } = state.capabilities;
  els.explainer.querySelector('option[value="model"]').disabled = !model;
  els.explainer.querySelector('option[value="recorded"]').disabled = !recorded;
  if (recorded) els.explainer.value = "recorded";
}

function startLanguage() {
  const wanted = (navigator.language || "en").toLowerCase().startsWith("id") ? "id" : "en";
  els.language.value = wanted;
  state.lang = wanted;
  applyLanguage();
}

els.form.addEventListener("submit", start);
els.language.addEventListener("change", () => start());
setInterval(render, REFRESH_MS);  // lets cards expire when the feed is quiet or has ended
startLanguage();
loadCapabilities().then(() => start());
