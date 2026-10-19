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
const KIND_LABEL = {
  goal: "Goal", big_chance: "Big chance", sprint_burst: "Sprint", pressing_spell: "Pressing spell",
  momentum_swing: "Momentum swing",
};

const els = {
  form: document.getElementById("controls"),
  seed: document.getElementById("seed"),
  speed: document.getElementById("speed"),
  persona: document.getElementById("persona"),
  home: document.getElementById("home"),
  away: document.getElementById("away"),
  score: document.getElementById("score"),
  clock: document.getElementById("clock"),
  markers: document.getElementById("markers"),
  cards: document.getElementById("cards"),
  status: document.getElementById("status"),
  live: document.getElementById("live"),
};

const state = { now: 0, scoreboard: null, items: [], cards: new Map(), controller: null };

function setStatus(text) {
  els.status.textContent = text;
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
  const params = new URLSearchParams({ speed: els.speed.value, persona: els.persona.value });
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
  reset();
  state.controller = new AbortController();
  setStatus("Connecting to the synthetic feed...");
  try {
    const response = await fetch(feedUrl(), { signal: state.controller.signal });
    if (!response.ok) throw new Error(`the feed answered ${response.status}`);
    setStatus("Live.");
    await readFrames(response.body, handleOverlay);
    setStatus("Full time. Click a sentence to see the events behind it, or replay another seed.");
  } catch (error) {
    if (error.name !== "AbortError") setStatus(`The feed stopped: ${error.message}`);
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
  const card = element("article", `card ${overlay.type}`);
  const { payload } = overlay;
  if (overlay.type === "key_moment") {
    card.append(element("h2", "", KIND_LABEL[payload.kind] || "Moment"), evidenceButton(payload.headline, overlay.evidence_event_ids));
  } else if (overlay.type === "explanation") {
    card.appendChild(element("h2", "", "Detail"));
    const list = element("ul");
    for (const sentence of payload.sentences) {
      const item = element("li");
      item.appendChild(evidenceButton(sentence.text, sentence.evidence_event_ids));
      list.appendChild(item);
    }
    card.appendChild(list);
  } else if (overlay.type === "player_tag") {
    card.appendChild(element("h2", "", `#${payload.shirt} ${payload.name}`));
    const chips = element("div", "chips");
    if (payload.speed_chip_kmh !== null) chips.appendChild(element("span", "chip", `${Math.round(payload.speed_chip_kmh)} km/h`));
    if (payload.distance_chip_km !== null) chips.appendChild(element("span", "chip", `${Math.round(payload.distance_chip_km)} km covered`));
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

els.form.addEventListener("submit", start);
setInterval(render, REFRESH_MS);  // lets cards expire when the feed is quiet or has ended
start();
