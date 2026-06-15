const form = document.querySelector("#runForm");
const submitButton = document.querySelector("#submitButton");
const connectionState = document.querySelector("#connectionState");
const timeline = document.querySelector("#timeline");
const runIdLabel = document.querySelector("#runIdLabel");
const summaryBlock = document.querySelector("#summaryBlock");
const alertSummary = document.querySelector("#alertSummary");
const downloadXlsx = document.querySelector("#downloadXlsx");
const downloadAlerts = document.querySelector("#downloadAlerts");

let eventSource = null;
let seenEvents = new Set();

document.querySelectorAll("input[type='file']").forEach((input) => {
  input.addEventListener("change", () => {
    const label = input.closest("label").querySelector(".file-name");
    const files = [...input.files].map((file) => file.name);
    label.textContent = files.length ? files.join(", ") : "No file selected";
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetRun();
  submitButton.disabled = true;
  connectionState.textContent = "Uploading";

  try {
    const formData = new FormData(form);
    const response = await fetch("/api/runs", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "Could not create run");
    }
    const { run_id: runId } = await response.json();
    runIdLabel.textContent = runId;
    connectionState.textContent = "Processing";
    connectEvents(runId);
  } catch (error) {
    addTimelineItem(error.message, true);
    connectionState.textContent = "Error";
    submitButton.disabled = false;
  }
});

function resetRun() {
  seenEvents = new Set();
  timeline.innerHTML = "";
  summaryBlock.hidden = true;
  alertSummary.innerHTML = "";
  runIdLabel.textContent = "";
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

function connectEvents(runId) {
  eventSource = new EventSource(`/api/runs/${runId}/events`);

  eventSource.addEventListener("status", (event) => {
    const data = JSON.parse(event.data);
    addTimelineItem(data.status);
  });

  eventSource.addEventListener("complete", (event) => {
    const data = JSON.parse(event.data);
    addTimelineItem("Completed");
    renderSummary(runId, data);
    connectionState.textContent = "Completed";
    submitButton.disabled = false;
    eventSource.close();
  });

  eventSource.addEventListener("error", (event) => {
    if (event.data) {
      const data = JSON.parse(event.data);
      addTimelineItem(data.error || "Run failed", true);
      connectionState.textContent = "Error";
      submitButton.disabled = false;
      eventSource.close();
    }
  });
}

function addTimelineItem(text, isError = false) {
  const key = `${isError}:${text}`;
  if (seenEvents.has(key)) return;
  seenEvents.add(key);
  [...timeline.children].forEach((item) => item.classList.remove("current"));
  const item = document.createElement("li");
  item.textContent = text;
  item.className = isError ? "danger current" : "current";
  timeline.appendChild(item);
}

function renderSummary(runId, data) {
  summaryBlock.hidden = false;
  downloadXlsx.href = `/api/runs/${runId}/result.xlsx`;
  downloadAlerts.href = `/api/runs/${runId}/alerts.md`;

  const alerts = data.alerts || [];
  const notes = data.notes || [];
  const banner = document.createElement("div");
  banner.className = `banner ${alerts.length ? "danger" : "ok"}`;
  banner.textContent = alerts.length
    ? `${alerts.length} account(s) with balance mismatch`
    : "All accounts with statements match their balances";

  alertSummary.innerHTML = "";
  alertSummary.appendChild(banner);

  if (alerts.length) {
    const list = document.createElement("div");
    list.className = "alert-list";
    alerts.forEach((alert) => list.appendChild(renderAlert(alert)));
    alertSummary.appendChild(list);
  }

  if (notes.length) {
    const noteBlock = document.createElement("div");
    noteBlock.className = "banner";
    noteBlock.textContent = notes.join(" ");
    alertSummary.appendChild(noteBlock);
  }
}

function renderAlert(alert) {
  const item = document.createElement("details");
  item.className = "alert-item";
  item.open = true;

  const summary = document.createElement("summary");
  summary.textContent = `Account ${alert.account}`;
  item.appendChild(summary);

  const grid = document.createElement("div");
  grid.className = "alert-grid";
  grid.appendChild(metric("Calculated", formatVnd(alert.end_balance_calculated)));
  grid.appendChild(metric("Statement", formatVnd(alert.end_balance_statement)));
  grid.appendChild(metric("Difference", formatVnd(alert.difference)));
  item.appendChild(grid);

  return item;
}

function metric(label, value) {
  const block = document.createElement("div");
  block.className = "metric";
  block.innerHTML = `<span></span><strong></strong>`;
  block.querySelector("span").textContent = label;
  block.querySelector("strong").textContent = value;
  return block;
}

function formatVnd(value) {
  if (typeof value !== "number") return "";
  return `${new Intl.NumberFormat("en-US").format(value)} VND`;
}
