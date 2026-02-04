const RAW_FIELDS = [
  "ts","node_id","radiation_cpm","pm25","air_temp_c","humidity","pressure_hpa","voc",
  "tds","ph","turbidity","water_temp_c"
];

function toCsv(rows) {
  const header = RAW_FIELDS.join(",");
  const lines = rows.map((r) => RAW_FIELDS.map((f) => (r[f] ?? "")).join(","));
  return [header, ...lines].join("\n");
}

async function fetchRaw(nodeId) {
  const qs = nodeId ? `&node_id=${encodeURIComponent(nodeId)}` : "";
  const res = await fetch(`/api/recent?n=1000${qs}&t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) return [];
  const body = await res.json();
  return body.rows || [];
}

function withinRange(ts, minutes) {
  if (!ts) return false;
  const t = new Date(ts).getTime();
  return Date.now() - t <= minutes * 60 * 1000;
}

function renderTable(rows) {
  const head = document.getElementById("rawTableHead");
  const body = document.querySelector("#rawTable tbody");
  head.innerHTML = RAW_FIELDS.map((f) => `<th>${f}</th>`).join("");
  body.innerHTML = rows.map((r) =>
    `<tr>${RAW_FIELDS.map((f) => `<td>${r[f] ?? "-"}</td>`).join("")}</tr>`
  ).join("");
}

async function refreshRaw() {
  const nodeId = document.getElementById("rawNodeSelect").value;
  const minutes = Number(document.getElementById("rawRangeSelect").value);
  const rows = await fetchRaw(nodeId);
  const filtered = rows.filter((r) => withinRange(r.ts, minutes));
  renderTable(filtered);
}

function setupExport() {
  const btn = document.getElementById("rawExportBtn");
  btn.addEventListener("click", async () => {
    const nodeId = document.getElementById("rawNodeSelect").value;
    const minutes = Number(document.getElementById("rawRangeSelect").value);
    const rows = (await fetchRaw(nodeId)).filter((r) => withinRange(r.ts, minutes));
    const csv = toCsv(rows);
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "raw_data.csv";
    a.click();
    URL.revokeObjectURL(url);
  });
}

function poll() {
  const auto = document.getElementById("rawAutoRefresh").checked;
  if (auto) refreshRaw();
  setTimeout(poll, 3000);
}

window.addEventListener("DOMContentLoaded", () => {
  document.getElementById("rawNodeSelect").addEventListener("change", refreshRaw);
  document.getElementById("rawRangeSelect").addEventListener("change", refreshRaw);
  setupExport();
  refreshRaw();
  poll();
});
