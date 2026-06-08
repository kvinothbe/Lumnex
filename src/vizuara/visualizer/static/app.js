// ---------- state ----------
let cy = null;
const state = { nodesById: new Map(), edges: [] };

// ---------- legend ----------
const LEGEND = [
  { color: "#ffd166", label: "Company" },
  { color: "#4cc9f0", label: "Product" },
  { color: "#9d4edd", label: "Integration" },
  { color: "#ff7b00", label: "Policy" },
  { color: "#06d6a0", label: "Category" },
];

function renderLegend() {
  const el = document.getElementById("legend");
  el.innerHTML = LEGEND.map(
    (x) => `<span class="lg"><span class="dot" style="background:${x.color}"></span>${x.label}</span>`
  ).join("");
}

// ---------- graph ----------
async function loadGraph() {
  const r = await fetch("/api/graph");
  const data = await r.json();
  data.nodes.forEach((n) => state.nodesById.set(n.id, n));
  state.edges = data.edges;

  const elements = [
    ...data.nodes.map((n) => ({
      data: { id: n.id, label: n.label, type: n.type, color: n.color, size: n.size },
    })),
    ...data.edges.map((e, i) => ({
      data: { id: `e${i}`, source: e.source, target: e.target, type: e.type },
    })),
  ];

  cy = cytoscape({
    container: document.getElementById("cy"),
    elements,
    minZoom: 0.25,
    maxZoom: 2.5,
    wheelSensitivity: 0.18,
    style: [
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          "label": "data(label)",
          "width": "data(size)",
          "height": "data(size)",
          "color": "#eef2ff",
          "font-family": "Inter, sans-serif",
          "font-size": 11,
          "font-weight": 500,
          "text-margin-y": -6,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-outline-color": "#07091a",
          "text-outline-width": 2,
          "border-width": 1,
          "border-color": "rgba(255,255,255,0.25)",
          "transition-property": "background-color, border-color, border-width, opacity, width, height",
          "transition-duration": "180ms",
        },
      },
      {
        selector: 'node[type = "company"]',
        style: {
          "border-color": "rgba(255,209,102,0.6)",
          "border-width": 3,
          "font-size": 14,
          "font-weight": 700,
        },
      },
      {
        selector: 'node[type = "product"]',
        style: { "font-size": 12 },
      },
      {
        selector: 'node[type = "integration"]',
        style: {
          shape: "round-rectangle",
          "font-size": 10,
          "color": "#dcd1ff",
        },
      },
      {
        selector: 'node[type = "policy"]',
        style: {
          shape: "round-diamond",
          "font-size": 10.5,
          "color": "#ffd6a8",
        },
      },
      {
        selector: 'node[type = "category"]',
        style: {
          shape: "round-pentagon",
          "font-size": 11.5,
          "background-opacity": 0.55,
          "color": "#cfeefe",
        },
      },
      {
        selector: "edge",
        style: {
          width: 1.1,
          "line-color": "rgba(255,255,255,0.13)",
          "curve-style": "bezier",
          "target-arrow-shape": "none",
          "transition-property": "line-color, width, opacity",
          "transition-duration": "180ms",
        },
      },
      {
        selector: 'edge[type = "integrates_with"]',
        style: { "line-color": "rgba(157,78,221,0.35)", "line-style": "dashed" },
      },
      {
        selector: 'edge[type = "belongs_to"]',
        style: { "line-color": "rgba(127,255,212,0.25)" },
      },
      {
        selector: 'edge[type = "part_of"]',
        style: { "line-color": "rgba(255,209,102,0.18)" },
      },
      {
        selector: 'edge[type = "policy_of"]',
        style: { "line-color": "rgba(255,123,0,0.40)", "line-style": "dotted", width: 1.6 },
      },
      {
        selector: ".dimmed",
        style: { opacity: 0.12 },
      },
      {
        selector: ".highlighted",
        style: {
          "border-color": "#7fffd4",
          "border-width": 4,
          "z-index": 10,
        },
      },
      {
        selector: ".cited",
        style: {
          "border-color": "#ffd166",
          "border-width": 4,
          "background-blacken": -0.05,
          "z-index": 9,
        },
      },
    ],
    layout: {
      name: "fcose",
      animate: true,
      animationDuration: 700,
      randomize: true,
      nodeRepulsion: 7500,
      idealEdgeLength: 110,
      gravity: 0.18,
      padding: 32,
    },
  });

  cy.on("tap", "node", (evt) => inspectNode(evt.target.id()));
  cy.on("tap", (evt) => {
    if (evt.target === cy) clearHighlights();
  });
}

function clearHighlights() {
  if (!cy) return;
  cy.nodes().removeClass("highlighted cited dimmed");
  cy.edges().removeClass("dimmed");
}

function highlightCitations(nodeIds) {
  if (!cy) return;
  clearHighlights();
  const set = new Set(nodeIds);
  cy.nodes().forEach((n) => {
    if (set.has(n.id())) n.addClass("cited");
    else n.addClass("dimmed");
  });
  cy.edges().forEach((e) => {
    if (set.has(e.source().id()) || set.has(e.target().id())) {
      // keep
    } else e.addClass("dimmed");
  });
  if (nodeIds.length) {
    cy.animate({ fit: { eles: cy.nodes().filter((n) => set.has(n.id())), padding: 90 } }, { duration: 600 });
  }
}

// ---------- inspector ----------
async function inspectNode(nodeId) {
  const node = state.nodesById.get(nodeId);
  if (!node) return;
  cy.nodes().removeClass("highlighted");
  cy.getElementById(nodeId).addClass("highlighted");

  const el = document.getElementById("inspector");
  const typeLabel = node.type[0].toUpperCase() + node.type.slice(1);
  let html = `<h3>${escapeHtml(node.label)}</h3>`;
  const badges = [`<span class="badge">${typeLabel}</span>`];
  if (node.category) badges.push(`<span class="badge">${escapeHtml(node.category)}</span>`);
  html += badges.join("");
  if (node.tagline) html += `<p class="tagline">${escapeHtml(node.tagline)}</p>`;

  const chunks = node.chunks || [];
  if (!chunks.length) {
    html += `<p class="muted">No wiki chunks attached to this node.</p>`;
    el.innerHTML = html;
    return;
  }
  el.innerHTML = html + `<p class="muted">Loading chunks…</p>`;

  const results = await Promise.all(
    chunks.map((c) =>
      fetch(`/api/chunk/${encodeURIComponent(c.chunk_id)}`).then((r) => r.json())
    )
  );
  const body = results
    .map(
      (c) => `
      <div class="chunk">
        <span class="chunk-id">${escapeHtml(c.chunk_id)}</span>
        <h4>${escapeHtml(c.title)}</h4>
        <div class="chunk-text">${escapeHtml(c.text)}</div>
      </div>`
    )
    .join("");
  el.innerHTML = html + body;
}

async function inspectChunk(chunkId) {
  const r = await fetch(`/api/chunk/${encodeURIComponent(chunkId)}`);
  if (!r.ok) return;
  const c = await r.json();
  inspectNode(c.node_id);
}

// ---------- chat ----------
const chatLog = document.getElementById("chat");
const chatForm = document.getElementById("chatForm");
const chatBox = document.getElementById("chatBox");
const sendBtn = document.getElementById("sendBtn");

function pushUser(text) {
  const div = document.createElement("div");
  div.className = "msg user";
  div.innerHTML = `<p>${escapeHtml(text)}</p>`;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function pushAssistant({ answer, abstained, citations }) {
  const div = document.createElement("div");
  div.className = "msg assistant" + (abstained ? " abstained" : "");
  let html = `<p>${escapeHtml(answer)}</p>`;
  if (citations && citations.length) {
    html += `<div class="citations">${citations
      .map(
        (cid) =>
          `<span class="citation" data-chunk="${escapeHtml(cid)}">${escapeHtml(cid)}</span>`
      )
      .join("")}</div>`;
  }
  div.innerHTML = html;
  chatLog.appendChild(div);
  div.querySelectorAll(".citation").forEach((el) => {
    el.addEventListener("click", () => inspectChunk(el.dataset.chunk));
  });
  chatLog.scrollTop = chatLog.scrollHeight;
}

function pushThinking() {
  const div = document.createElement("div");
  div.className = "msg assistant thinking";
  div.innerHTML = `<div class="dots"><span></span><span></span><span></span></div>`;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = chatBox.value.trim();
  if (!text) return;
  chatBox.value = "";
  sendBtn.disabled = true;
  pushUser(text);
  const thinking = pushThinking();
  try {
    const r = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text }),
    });
    const data = await r.json();
    thinking.remove();
    pushAssistant({
      answer: data.answer,
      abstained: data.abstained,
      citations: data.cited_chunk_ids,
    });
    highlightCitations(data.cited_node_ids || []);
  } catch (err) {
    thinking.remove();
    pushAssistant({
      answer: "Something went wrong: " + (err.message || err),
      abstained: true,
      citations: [],
    });
  } finally {
    sendBtn.disabled = false;
    chatBox.focus();
  }
});

// ---------- filter + reset ----------
document.getElementById("filterInput").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  if (!cy) return;
  if (!q) { clearHighlights(); return; }
  cy.nodes().forEach((n) => {
    const lbl = (n.data("label") || "").toLowerCase();
    if (lbl.includes(q)) {
      n.removeClass("dimmed");
      n.addClass("highlighted");
    } else {
      n.removeClass("highlighted");
      n.addClass("dimmed");
    }
  });
});

document.getElementById("resetBtn").addEventListener("click", () => {
  if (!cy) return;
  clearHighlights();
  cy.layout({
    name: "fcose",
    animate: true,
    animationDuration: 600,
    randomize: false,
    nodeRepulsion: 7500,
    idealEdgeLength: 110,
    gravity: 0.18,
    padding: 32,
  }).run();
});

// ---------- helpers ----------
function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// ---------- boot ----------
renderLegend();
loadGraph();
