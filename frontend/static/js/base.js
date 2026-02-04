async function fetchStatus() {
  const res = await fetch(`/api/status?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Status ${res.status}`);
  return res.json();
}

function displayStatus(status) {
  if (!status) return { text: "SAFE", className: "safe" };
  const normalized = String(status).toLowerCase();
  if (normalized === "danger") return { text: "CRITICAL", className: "danger" };
  if (normalized === "no_data_yet") return { text: "OFFLINE", className: "offline" };
  return { text: String(status).toUpperCase(), className: normalized };
}

function setOverallStatus(status) {
  const badge = document.getElementById("overallStatusBadge");
  if (!badge) return;
  const display = displayStatus(status);
  badge.textContent = display.text;
  badge.className = `status-pill ${display.className}`;
  document.body.dataset.overallStatus = display.className;
  try {
    localStorage.setItem("overallStatus", display.className);
  } catch (_) {}
}

async function pollOverall() {
  try {
    if (window.__statusOverride) {
      setOverallStatus(window.__statusOverride);
      return;
    }
    const data = await fetchStatus();
    setOverallStatus(data.overall_status || data.status || "SAFE");
  } catch (e) {
    setOverallStatus("OFFLINE");
  } finally {
    setTimeout(pollOverall, 2000);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const tester = document.getElementById("statusTestSelect");
  if (tester) {
    tester.addEventListener("change", (e) => {
      const value = e.target.value;
      window.__statusOverride = value ? value.toUpperCase() : null;
      if (window.__statusOverride) {
        setOverallStatus(window.__statusOverride);
      }
    });
  }
  const darkToggle = document.getElementById("darkModeToggle");
  if (darkToggle) {
    const saved = localStorage.getItem("theme") || "light";
    if (saved === "dark") {
      document.body.dataset.theme = "dark";
      darkToggle.checked = true;
    }
    darkToggle.addEventListener("change", (e) => {
      const enabled = e.target.checked;
      document.body.dataset.theme = enabled ? "dark" : "light";
      localStorage.setItem("theme", enabled ? "dark" : "light");
    });
  }
  const lastStatus = localStorage.getItem("overallStatus");
  if (lastStatus) {
    setOverallStatus(lastStatus);
  } else {
    setOverallStatus("OFFLINE");
  }
  pollOverall();
});
