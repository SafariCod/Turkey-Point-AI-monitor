const GROUND_METRICS = {
  radiation_cpm: "Radiation",
  pm25: "PM2.5",
  air_temp_c: "Air Temp",
  humidity: "Humidity",
  pressure_hpa: "Pressure",
  voc: "VOC",
};
const WATER_METRICS = {
  water_temp_c: "Water Temp",
  turbidity: "Turbidity",
  tds: "TDS",
  ph: "pH",
};
const UNITS = {
  radiation_cpm: "µSv/h",
  pm25: "µg/m³",
  air_temp_c: "°C",
  humidity: "%RH",
  pressure_hpa: "hPa",
  voc: "ppm",
  water_temp_c: "°C",
  turbidity: "NTU",
  tds: "ppm",
  ph: "pH",
};
const NODE_IDS = ["ground_1", "ground_2", "ground_3", "water_1"];

let groundChart;
let waterChart;

function displayNodeId(id) {
  if (id.startsWith("ground_")) return `Ground ${id.split("_")[1]}`;
  if (id.startsWith("water_")) return `Water ${id.split("_")[1]}`;
  return id;
}

function pickKeyMetric(nodeId, latest) {
  if (!latest) return { label: "No data", value: "--" };
  if (nodeId.startsWith("ground")) {
    const val = latest.radiation_cpm ?? latest.pm25;
    return { label: latest.radiation_cpm != null ? "Radiation" : "PM2.5", value: val ?? "--" };
  }
  if (nodeId.startsWith("water")) {
    const val = latest.water_temp_c ?? latest.turbidity ?? latest.tds ?? latest.ph;
    const label =
      latest.water_temp_c != null ? "Water Temp" :
      latest.turbidity != null ? "Turbidity" :
      latest.tds != null ? "TDS" :
      latest.ph != null ? "pH" : "No data";
    return { label, value: val ?? "--" };
  }
  return { label: "No data", value: "--" };
}

function renderNodeCards(nodes) {
  const container = document.getElementById("nodeCards");
  container.innerHTML = "";
  NODE_IDS.forEach((id) => {
    const node = nodes[id] || {};
    const latest = node.latest || {};
    const rawStatus = node.status || "Safe";
    const status = String(rawStatus).toLowerCase();
    const updated = latest.ts ? new Date(latest.ts).toLocaleString() : "--";
    const reasons = (node.reasons || []).slice(0, 2);
    const displayText = status === "danger" ? "CRITICAL" :
      status === "no_data_yet" ? "OFFLINE" : String(rawStatus).toUpperCase();
    const showReasons = reasons.length ? reasons : ["Within expected ranges"];
    const card = document.createElement("div");
    card.className = "node-card";
    card.innerHTML = `
      <div class="node-header">
        <strong>${displayNodeId(id)}</strong>
        <span class="badge ${status}">${displayText}</span>
      </div>
      <div class="muted">Last updated: ${updated}</div>
      <div class="muted" style="margin-top:6px;">Why this status?</div>
      <ul class="node-reasons">
        ${showReasons.map((r) => `<li>${r}</li>`).join("")}
      </ul>
    `;
    container.appendChild(card);
  });
}

async function fetchRecent(nodeId, metric) {
  const res = await fetch(`/api/recent?n=200&node_id=${encodeURIComponent(nodeId)}&t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error("recent");
  const body = await res.json();
  const rows = body.rows || [];
  return rows.filter((r) => r[metric] != null);
}

function initChart(ctx) {
  return new Chart(ctx, {
    type: "line",
    data: { labels: [], datasets: [] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { display: false },
        y: {
          beginAtZero: false,
          title: {
            display: true,
            text: "",
            font: { family: "IBM Plex Sans", size: 12, weight: "600" },
          },
        },
      },
      plugins: {
        legend: {
          display: true,
          labels: {
            font: { family: "IBM Plex Sans", size: 12, weight: "600" },
            boxWidth: 14,
            boxHeight: 4,
          },
        },
      },
    },
  });
}

async function refreshGroundChart() {
  const nodeId = document.getElementById("groundNodeSelect").value;
  const metric = document.getElementById("groundMetricSelect").value;
  const rows = await fetchRecent(nodeId, metric);
  const labels = rows.map((r) => new Date(r.ts).toLocaleTimeString());
  groundChart.data.labels = labels;
  groundChart.data.datasets = [{
    label: `${displayNodeId(nodeId)} - ${GROUND_METRICS[metric]}`,
    data: rows.map((r) => r[metric]),
    borderColor: "#22d3ee",
    pointRadius: 0,
    tension: 0.2,
  }];
  groundChart.options.scales.y.title.text = UNITS[metric] || "";
  groundChart.update();
}

async function refreshWaterChart() {
  const metric = document.getElementById("waterMetricSelect").value;
  const rows = await fetchRecent("water_1", metric);
  const labels = rows.map((r) => new Date(r.ts).toLocaleTimeString());
  waterChart.data.labels = labels;
  waterChart.data.datasets = [{
    label: `Water 1 - ${WATER_METRICS[metric]}`,
    data: rows.map((r) => r[metric]),
    borderColor: "#f59e0b",
    pointRadius: 0,
    tension: 0.2,
  }];
  waterChart.options.scales.y.title.text = UNITS[metric] || "";
  waterChart.update();
}

async function refreshEvents() {
  const res = await fetch(`/api/events?n=50&t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) return;
  const body = await res.json();
  const list = document.getElementById("eventsList");
  list.innerHTML = "";
  (body.events || []).forEach((e) => {
    const li = document.createElement("li");
    li.textContent = `${new Date(e.ts).toLocaleString()} - ${e.node_id} - ${e.message}`;
    list.appendChild(li);
  });
}

async function refreshStatus() {
  const res = await fetch(`/api/status?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) return;
  const body = await res.json();
  renderNodeCards(body.nodes || {});
}

function poll() {
  Promise.all([refreshStatus(), refreshGroundChart(), refreshWaterChart(), refreshEvents()])
    .finally(() => setTimeout(poll, 1000));
}

window.addEventListener("DOMContentLoaded", () => {
  const groundMetricSelect = document.getElementById("groundMetricSelect");
  const waterMetricSelect = document.getElementById("waterMetricSelect");
  Object.entries(GROUND_METRICS).forEach(([value, label]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    groundMetricSelect.appendChild(opt);
  });
  Object.entries(WATER_METRICS).forEach(([value, label]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    waterMetricSelect.appendChild(opt);
  });
  groundChart = initChart(document.getElementById("groundChart"));
  waterChart = initChart(document.getElementById("waterChart"));
  document.getElementById("groundNodeSelect").addEventListener("change", refreshGroundChart);
  groundMetricSelect.addEventListener("change", refreshGroundChart);
  waterMetricSelect.addEventListener("change", refreshWaterChart);
  poll();
});
