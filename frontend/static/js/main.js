const API_BASE = "";
const POLL_MS = 1000;
const WINDOW_POINTS = 200;

const NODE_IDS = ["ground_1", "ground_2", "ground_3", "water_1"];
const GROUND_METRICS = {
  radiation_cpm: "Radiation (uSv/h)",
  pm25: "PM2.5 (ug/m3)",
  air_temp_c: "Air Temp (degC)",
  humidity: "Humidity (%)",
  pressure_hpa: "Pressure (hPa)",
  voc: "VOC (ppb)",
};
const WATER_METRICS = {
  tds: "TDS (ppm)",
  ph: "pH",
  turbidity: "Turbidity (NTU)",
  water_temp_c: "Water Temp (degC)",
};

const NODE_POS = {
  ground_1: { x: 25, y: 65 },
  ground_2: { x: 45, y: 60 },
  ground_3: { x: 65, y: 55 },
  water_1: { x: 50, y: 80 },
};

const FEATURE_LABELS = {
  radiation_cpm: "Radiation",
  pm25: "PM2.5",
  air_temp_c: "Air temperature",
  humidity: "Humidity",
  pressure_hpa: "Pressure",
  voc: "VOC",
  tds: "TDS",
  ph: "pH",
  turbidity: "Turbidity",
  water_temp_c: "Water temperature",
};

let groundChart;
let waterChart;
let showBaseline = false;
let latestStatus = {};
let latestRaw = [];
let advancedMode = false;

function displayNodeId(id) {
  if (id.startsWith("ground_")) return `Ground ${id.split("_")[1]}`;
  if (id.startsWith("water_")) return `Water ${id.split("_")[1]}`;
  return id;
}

function featureLabel(key) {
  return FEATURE_LABELS[key] || key;
}

function simplifyReason(reason) {
  if (!reason) return "";
  const nodeMatch = reason.match(/^(ground|water)_\d+:/i);
  const nodeLabel = nodeMatch ? `${displayNodeId(nodeMatch[0].slice(0, -1))} ` : "";
  let r = reason.replace(/^(ground|water)_\d+:\s*/i, "");
  const absolute = r.match(/^Absolute threshold exceeded:\s*([a-z0-9_]+).*(>|<)\s*=?\s*([0-9.]+)/i);
  if (absolute) {
    return `${nodeLabel}${featureLabel(absolute[1])} too ${absolute[2] === ">=" ? "high" : "low"}`.trim();
  }
  const high = r.match(/^High reading:\s*([a-z0-9_]+)\s+/i);
  if (high) return `${nodeLabel}${featureLabel(high[1])} too high`.trim();
  const low = r.match(/^Low reading:\s*([a-z0-9_]+)\s+/i);
  if (low) return `${nodeLabel}${featureLabel(low[1])} too low`.trim();
  const zReason = r.match(/^([a-z0-9_]+)\s+(high|low)\b/i);
  if (zReason) return `${nodeLabel}${featureLabel(zReason[1])} too ${zReason[2].toLowerCase()}`.trim();
  const jump = r.match(/^Sudden jump in\s+([a-z0-9_]+):/i);
  if (jump) return `${nodeLabel}Sudden jump in ${featureLabel(jump[1])}`.trim();
  if (/Multiple sensors abnormal/i.test(r)) return "Multiple sensors too high";
  if (/within expected range/i.test(r)) return "Within expected range";
  if (/No data received/i.test(r)) return "No recent data";
  return r.replace(/\s*\(z=[^)]+\)/i, "");
}

function displayTextWithNodeLabels(text) {
  if (!text) return text;
  return text.replace(/\b(ground|water)_\d+\b/gi, (match) => displayNodeId(match.toLowerCase()));
}

function switchTab(tabId) {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-section").forEach((sec) => {
    sec.classList.toggle("active", sec.id === `tab-${tabId}`);
  });
}

function setMode(isAdvanced) {
  advancedMode = isAdvanced;
  document.body.classList.toggle("advanced", isAdvanced);
  const toggle = document.getElementById("modeToggle");
  if (toggle) toggle.checked = isAdvanced;
  if (!isAdvanced) switchTab("main");
}

function setOverallStatus(status, abnormalProb, ts, context, humanSummary, overallReasons) {
  const badge = document.getElementById("statusBadge");
  badge.textContent = status;
  badge.className = `badge ${status.toLowerCase()}`;
  document.getElementById("confidence").textContent = advancedMode
    ? `Overall abnormal probability: ${(abnormalProb * 100).toFixed(1)}%`
    : "";
  document.getElementById("lastUpdated").textContent = advancedMode
    ? (ts ? `Last updated: ${new Date(ts).toLocaleString()}` : "Last updated: --")
    : "";
  const storm = document.getElementById("stormBadge");
  if (context?.storm_mode) {
    storm.style.display = "inline-block";
    storm.textContent = `Storm mode · ${context.reason || "context active"}`;
  } else {
    storm.style.display = "none";
  }
  const summary = humanSummary || (overallReasons && overallReasons[0]) || "";
  document.getElementById("humanSummary").textContent = advancedMode ? displayTextWithNodeLabels(summary) : "";
}

function setReasons(reasons, limit = null) {
  const list = document.getElementById("reasons");
  list.innerHTML = "";
  const items = reasons && reasons.length ? reasons : ["Within expected ranges"];
  const sliced = limit ? items.slice(0, limit) : items;
  sliced.forEach((r) => {
    const li = document.createElement("li");
    li.textContent = r;
    list.appendChild(li);
  });
}

function renderNodeCards(nodes) {
  const container = document.getElementById("nodeCards");
  container.innerHTML = "";
  NODE_IDS.forEach((id) => {
    const node = nodes[id] || {};
    const card = document.createElement("div");
    card.className = "card";
    const status = node.status || "Safe";
    const prob = node.abnormal_probability != null ? (node.abnormal_probability * 100).toFixed(1) : "--";
    const conf = node.confidence != null ? (node.confidence * 100).toFixed(1) : "--";
    const updated = node.latest?.ts ? new Date(node.latest.ts).toLocaleString() : "--";
    const reasons = node.reasons && node.reasons.length ? node.reasons.slice(0, 3) : ["No data yet"];
    const cardReasons = advancedMode ? reasons : reasons.map(simplifyReason).filter(Boolean);
    const summary = displayTextWithNodeLabels(node.human_summary || "");
    const detailLine = advancedMode
      ? `<p class="muted">Abnormal prob: ${prob}% · Confidence: ${conf}%</p>
      <p class="muted">Last updated: ${updated}</p>`
      : "";
    const summaryLine = advancedMode ? `<p class="muted small">${summary}</p>` : "";
    card.innerHTML = `
      <h3>${displayNodeId(id)}</h3>
      <div class="badge ${status.toLowerCase()}">${status}</div>
      ${detailLine}
      ${summaryLine}
      <ul class="reasons">${cardReasons.map((r) => `<li>${displayTextWithNodeLabels(r)}</li>`).join("")}</ul>
    `;
    container.appendChild(card);
  });
}

function withNoCache(url) {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}t=${Date.now()}`;
}

async function fetchStatus() {
  const res = await fetch(withNoCache(`${API_BASE}/api/status`), { cache: "no-store" });
  if (!res.ok) throw new Error(`Status error ${res.status}`);
  return res.json();
}

async function fetchLatestAll() {
  const res = await fetch(withNoCache(`${API_BASE}/api/latest_all`), { cache: "no-store" });
  if (!res.ok) throw new Error(`Latest error ${res.status}`);
  return res.json();
}

async function fetchRecent(nodeId, metric) {
  const includeBaseline = showBaseline ? "&include_baseline=1" : "";
  const res = await fetch(withNoCache(`${API_BASE}/api/recent?n=${WINDOW_POINTS}&node_id=${encodeURIComponent(nodeId)}${includeBaseline}`), { cache: "no-store" });
  if (!res.ok) throw new Error(`Recent error ${res.status}`);
  const body = await res.json();
  const rows = body.rows || [];
  if (!rows.length) return { rows: [], baseline: {} };

  const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
  let filtered = rows.filter((r) => new Date(r.ts).getTime() >= dayAgo);
  if (!filtered.length) filtered = rows;
  if (filtered.length > WINDOW_POINTS) filtered = filtered.slice(-WINDOW_POINTS);
  return { rows: filtered.filter((r) => r[metric] != null), baseline: body.baseline || {} };
}

function initCharts() {
  const baseOptions = {
    type: "line",
    data: { labels: [], datasets: [] },
    options: {
      responsive: true,
      animation: false,
      scales: { x: { display: false }, y: { beginAtZero: false } },
      plugins: { legend: { display: true, labels: { color: "#cbd5e1" } } },
    },
  };
  groundChart = new Chart(document.getElementById("groundChart"), JSON.parse(JSON.stringify(baseOptions)));
  waterChart = new Chart(document.getElementById("waterChart"), JSON.parse(JSON.stringify(baseOptions)));
}

function updateChart(chart, labels, datasets) {
  chart.data.labels = labels;
  chart.data.datasets = datasets;
  chart.update();
}

function setSelectOptions(selectEl, optionsMap) {
  selectEl.innerHTML = "";
  Object.entries(optionsMap).forEach(([value, label]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    selectEl.appendChild(opt);
  });
}

async function refreshStatusUI() {
  try {
    const data = await fetchStatus();
    latestStatus = data.nodes || {};
    const nodes = data.nodes || {};
    renderNodeCards(nodes);

    const worst = Object.values(nodes).sort((a, b) => (b?.abnormal_probability || 0) - (a?.abnormal_probability || 0))[0];
    const rawReasons = (data.overall_reasons && data.overall_reasons.length ? data.overall_reasons : (worst && worst.reasons)) || ["No data yet"];
    const reasons = advancedMode
      ? rawReasons.map(displayTextWithNodeLabels)
      : rawReasons.map(simplifyReason).filter(Boolean);
    const summary = advancedMode ? data.overall_human_summary : "";
    setOverallStatus(data.overall_status, data.overall_abnormal_probability, data.last_updated_ts, data.context, summary, reasons);
    setReasons(reasons, advancedMode ? 4 : 2);
    renderMapMarkers();
  } catch (err) {
    console.error(err);
    setOverallStatus("Safe", 0, null);
    setReasons(["Waiting for data..."]);
  }
}

function renderRawTable(rows) {
  const tbody = document.querySelector("#rawTable tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  const fields = ["air_temp_c","humidity","pressure_hpa","voc","pm25","radiation_cpm","tds","ph","turbidity","water_temp_c"];
  NODE_IDS.forEach((id) => {
    const match = rows.find((r) => r.node_id === id);
    const latest = match?.latest || {};
    const status = (latestStatus[id]?.status || "safe").toLowerCase();
    const tr = document.createElement("tr");
    const tsRaw = latest.ts || "-";
    const tsLocal = latest.ts ? new Date(latest.ts).toLocaleString() : "-";
    const ageSec = latest.ts ? ((Date.now() - Date.parse(latest.ts)) / 1000).toFixed(1) : "-";
    const rowId = latest.id ?? "-";
    tr.innerHTML = `<td>${displayNodeId(id)}</td><td>${rowId}</td><td>${tsRaw}</td><td>${tsLocal}</td><td>${ageSec}</td>${fields.map(f => `<td class="${status === 'danger' ? 'highlight-danger' : status === 'warning' ? 'highlight-warning' : ''}">${latest[f] ?? '-'}</td>`).join("")}`;
    tbody.appendChild(tr);
  });
}

async function refreshRaw() {
  try {
    const data = await fetchLatestAll();
    latestRaw = data.rows || [];
    renderRawTable(latestRaw);
  } catch (e) {
    console.error(e);
  }
}

function refreshGroundChart() {
  const metric = document.getElementById("groundMetricSelect").value;
  const nodeId = document.getElementById("groundNodeSelect").value;
  fetchRecent(nodeId, metric).then((result) => {
    const rows = result.rows;
    if (!rows || !rows.length) return;
    const labels = rows.map((r) => new Date(r.ts).toLocaleTimeString());
    const datasets = [
      {
        label: `${displayNodeId(nodeId)} · ${GROUND_METRICS[metric]}`,
        data: rows.map((r) => r[metric]),
        borderColor: "#22d3ee",
        tension: 0.2,
        fill: false,
        pointRadius: 0,
      },
    ];
    if (showBaseline && result.baseline && result.baseline[metric] !== undefined) {
      datasets.push({
        label: "Baseline",
        data: Array(rows.length).fill(result.baseline[metric]),
        borderColor: "#64748b",
        borderDash: [4, 4],
        pointRadius: 0,
        fill: false,
        tension: 0,
      });
    }
    updateChart(groundChart, labels, datasets);
  }).catch(console.error);
}

function refreshWaterChart() {
  const metric = document.getElementById("waterMetricSelect").value;
  fetchRecent("water_1", metric).then((result) => {
    const rows = result.rows;
    if (!rows || !rows.length) return;
    const labels = rows.map((r) => new Date(r.ts).toLocaleTimeString());
    const datasets = [
      {
        label: `${displayNodeId("water_1")} · ${WATER_METRICS[metric]}`,
        data: rows.map((r) => r[metric]),
        borderColor: "#f59e0b",
        tension: 0.2,
        fill: false,
        pointRadius: 0,
      },
    ];
    if (showBaseline && result.baseline && result.baseline[metric] !== undefined) {
      datasets.push({
        label: "Baseline",
        data: Array(rows.length).fill(result.baseline[metric]),
        borderColor: "#94a3b8",
        borderDash: [4, 4],
        pointRadius: 0,
        fill: false,
        tension: 0,
      });
    }
    updateChart(waterChart, labels, datasets);
  }).catch(console.error);
}

async function refreshEvents() {
  try {
    const res = await fetch(withNoCache(`${API_BASE}/api/events?n=10`), { cache: "no-store" });
    if (!res.ok) return;
    const body = await res.json();
    const list = document.getElementById("eventsList");
    list.innerHTML = "";
    (body.events || []).forEach((e) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="muted">${new Date(e.ts).toLocaleString()}</span> · <strong>${e.level}</strong> · ${e.message}`;
      list.appendChild(li);
    });
  } catch (err) {
    console.error(err);
  }
}

function poll() {
  const tasks = [refreshStatusUI(), refreshGroundChart(), refreshWaterChart(), refreshEvents(), refreshRaw()];
  Promise.all(tasks)
    .catch(console.error)
    .finally(() => setTimeout(poll, POLL_MS));
}

function wireBaselineToggle() {
  const toggle = document.getElementById("baselineToggle");
  if (!toggle) return;
  toggle.addEventListener("change", () => {
    showBaseline = toggle.checked;
    refreshGroundChart();
    refreshWaterChart();
  });
}

function wireModeToggle() {
  const toggle = document.getElementById("modeToggle");
  if (!toggle) return;
  const saved = localStorage.getItem("advancedMode");
  setMode(saved === "true");
  toggle.addEventListener("change", () => {
    setMode(toggle.checked);
    localStorage.setItem("advancedMode", toggle.checked ? "true" : "false");
    refreshStatusUI();
    if (toggle.checked) {
      refreshEvents();
      refreshRaw();
    }
  });
}

function wireChartControls() {
  const groundMetric = document.getElementById("groundMetricSelect");
  const groundNode = document.getElementById("groundNodeSelect");
  const waterMetric = document.getElementById("waterMetricSelect");
  if (groundMetric) groundMetric.addEventListener("change", refreshGroundChart);
  if (groundNode) groundNode.addEventListener("change", refreshGroundChart);
  if (waterMetric) waterMetric.addEventListener("change", refreshWaterChart);
}

function wireTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function renderMapMarkers() {
  const container = document.getElementById("mapMarkers");
  if (!container) return;
  container.innerHTML = "";
  NODE_IDS.forEach((id) => {
    const node = latestStatus[id] || {};
    const pos = NODE_POS[id];
    const div = document.createElement("div");
    div.className = `marker ${(node.status || "safe").toLowerCase()}`;
    div.style.left = `${pos.x}%`;
    div.style.top = `${pos.y}%`;
    div.title = `${displayNodeId(id)}: ${node.status || "unknown"}`;
    container.appendChild(div);
  });
}

window.addEventListener("DOMContentLoaded", () => {
  setSelectOptions(document.getElementById("groundMetricSelect"), GROUND_METRICS);
  setSelectOptions(document.getElementById("waterMetricSelect"), WATER_METRICS);
  initCharts();
  wireModeToggle();
  wireBaselineToggle();
  wireChartControls();
  wireTabs();
  refreshStatusUI();
  refreshGroundChart();
  refreshWaterChart();
  refreshEvents();
  refreshRaw();
  poll();
});
