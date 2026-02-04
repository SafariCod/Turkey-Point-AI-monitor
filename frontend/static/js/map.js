const NODE_COORDS = {
  ground_1: { name: "Ground 1", lat: 25.4309, lng: -80.3311 },
  ground_2: { name: "Ground 2", lat: 25.4272, lng: -80.3283 },
  ground_3: { name: "Ground 3", lat: 25.4238, lng: -80.3259 },
  water_1: { name: "Water 1", lat: 25.4215, lng: -80.3364 },
};

const STATUS_COLORS = {
  safe: "#10b981",
  warning: "#f59e0b",
  danger: "#ef4444",
  critical: "#ef4444",
  offline: "#94a3b8",
  abnormal: "#60a5fa",
};

let map;
let markers = {};

function statusColor(status) {
  const key = String(status || "safe").toLowerCase();
  return STATUS_COLORS[key] || STATUS_COLORS.safe;
}

function keyMetric(nodeId, latest) {
  if (!latest) return { label: "No data", value: "--" };
  if (nodeId.startsWith("ground")) {
    const val = latest.radiation_cpm ?? latest.pm25;
    const label = latest.radiation_cpm != null ? "Radiation" : "PM2.5";
    return { label, value: val ?? "--" };
  }
  const val = latest.water_temp_c ?? latest.turbidity ?? latest.tds ?? latest.ph;
  const label =
    latest.water_temp_c != null ? "Water Temp" :
    latest.turbidity != null ? "Turbidity" :
    latest.tds != null ? "TDS" :
    latest.ph != null ? "pH" : "No data";
  return { label, value: val ?? "--" };
}

function buildPopup(nodeId, status, latest) {
  const updated = latest && latest.ts ? new Date(latest.ts).toLocaleString() : "--";
  const metric = keyMetric(nodeId, latest);
  return `
    <strong>${NODE_COORDS[nodeId]?.name || nodeId}</strong><br/>
    Status: ${String(status || "Safe").toUpperCase()}<br/>
    Last updated: ${updated}<br/>
    ${metric.label}: ${metric.value}
  `;
}

async function refreshMap() {
  const res = await fetch(`/api/status?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) return;
  const data = await res.json();
  const nodes = data.nodes || {};
  Object.keys(NODE_COORDS).forEach((nodeId) => {
    const coord = NODE_COORDS[nodeId];
    if (!coord) return;
    const node = nodes[nodeId] || {};
    const status = node.status || "Safe";
    const latest = node.latest || null;
    const color = statusColor(status);
    const popup = buildPopup(nodeId, status, latest);

    if (!markers[nodeId]) {
      const marker = L.circleMarker([coord.lat, coord.lng], {
        radius: 8,
        color,
        fillColor: color,
        fillOpacity: 0.9,
      });
      marker.addTo(map).bindPopup(popup);
      markers[nodeId] = marker;
    } else {
      markers[nodeId].setStyle({ color, fillColor: color });
      markers[nodeId].setPopupContent(popup);
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  const el = document.getElementById("map");
  if (!el) return;
  const bounds = L.latLngBounds(
    [25.30, -80.55],
    [25.60, -80.20]
  );
  map = L.map("map", { maxBounds: bounds, maxBoundsViscosity: 1.0 })
    .setView([25.43, -80.34], 12.5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  refreshMap();
  setInterval(refreshMap, 5000);
});
