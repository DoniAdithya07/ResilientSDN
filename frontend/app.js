// ResilientSDN replay dashboard — plain JS, no build step.
// Talks to the backend via relative URLs so it works at any host:port.

(() => {
  "use strict";

  const runSelect = document.getElementById("runSelect");
  const playBtn = document.getElementById("playBtn");
  const playState = document.getElementById("playState");
  const backendError = document.getElementById("backendError");
  const retryBtn = document.getElementById("retryBtn");
  const speedBtns = Array.from(document.querySelectorAll(".speed-btn"));
  const rerouteLog = document.getElementById("rerouteLog");

  const sumSystem = document.getElementById("sumSystem");
  const sumScenario = document.getElementById("sumScenario");
  const sumLink = document.getElementById("sumLink");
  const sumThreshold = document.getElementById("sumThreshold");
  const sumReroutes = document.getElementById("sumReroutes");

  let runs = [];
  let currentMeta = null;
  let speed = 20;
  let socket = null;
  let rerouteCount = 0;

  // ---- chart setup -------------------------------------------------

  const ctx = document.getElementById("utilChart").getContext("2d");
  const chart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: "Utilization",
          data: [],
          borderColor: "#4fd1c5",
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
          parsing: false,
        },
        {
          label: "Threshold",
          data: [],
          borderColor: "#f0a84a",
          borderDash: [6, 4],
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0,
          parsing: false,
        },
        {
          label: "Reroute",
          data: [],
          type: "scatter",
          showLine: false,
          backgroundColor: "#e5534b",
          borderColor: "#e5534b",
          pointStyle: "triangle",
          pointRadius: 6,
          pointHoverRadius: 8,
          parsing: false,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: "seconds since run start", color: "#8aa19d" },
          ticks: { color: "#8aa19d" },
          grid: { color: "#1f2a2c" },
        },
        y: {
          min: -0.1,
          suggestedMax: 1.2,
          title: { display: true, text: "utilization", color: "#8aa19d" },
          ticks: { color: "#8aa19d" },
          grid: { color: "#1f2a2c" },
        },
      },
      plugins: {
        legend: { labels: { color: "#d8e3e0" } },
        tooltip: {
          callbacks: {
            label(item) {
              const p = item.raw;
              if (item.dataset.label === "Reroute") {
                return `reroute — flow ${p.flow} (${p.reason})`;
              }
              return `${item.dataset.label}: ${p.y.toFixed(3)}`;
            },
          },
        },
      },
    },
  });

  function resetChart() {
    chart.data.datasets[0].data = [];
    chart.data.datasets[1].data = [];
    chart.data.datasets[2].data = [];
    chart.update("none");
    rerouteCount = 0;
    sumReroutes.textContent = "0";
    rerouteLog.innerHTML = '<li class="log-empty">No reroutes yet.</li>';
  }

  function addPoint(t, util) {
    chart.data.datasets[0].data.push({ x: t, y: util });
    // Threshold is a flat dashed line from 0 to "now" — just two points,
    // stretch the second one forward as time advances.
    const threshold = currentMeta ? currentMeta.threshold : 0;
    chart.data.datasets[1].data = [{ x: 0, y: threshold }, { x: t, y: threshold }];
    chart.update("none");
  }

  function addReroute(t, flow, reason) {
    chart.data.datasets[2].data.push({ x: t, y: -0.05, flow, reason });
    chart.update("none");

    rerouteCount += 1;
    sumReroutes.textContent = String(rerouteCount);
    if (rerouteLog.querySelector(".log-empty")) rerouteLog.innerHTML = "";
    const li = document.createElement("li");
    const tSpan = document.createElement("span");
    tSpan.className = "t";
    tSpan.textContent = `t=${t.toFixed(1)}s`;
    const rest = document.createElement("span");
    rest.textContent = `flow ${flow} — ${reason}`;
    li.append(tSpan, rest);
    rerouteLog.prepend(li);
  }

  // ---- backend wiring ------------------------------------------------

  function showBackendError(show) {
    backendError.hidden = !show;
  }

  async function loadRuns() {
    runSelect.disabled = true;
    playBtn.disabled = true;
    try {
      const res = await fetch("/api/runs");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      runs = await res.json();
      showBackendError(false);
      populateRunSelect();
    } catch (err) {
      console.warn("Could not load runs:", err.message);
      showBackendError(true);
      runSelect.innerHTML = '<option value="">Backend unavailable</option>';
    }
  }

  function populateRunSelect() {
    if (!runs.length) {
      runSelect.innerHTML = '<option value="">No runs available</option>';
      return;
    }
    runSelect.innerHTML = runs
      .map((r) => {
        const label = `${r.topology} — ${r.system} — ${r.scenario} — run ${r.run}`;
        return `<option value="${r.id}">${label}</option>`;
      })
      .join("");
    runSelect.disabled = false;
    playBtn.disabled = false;
    onRunChange();
  }

  async function onRunChange() {
    const runId = runSelect.value;
    if (!runId) return;
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/meta`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      currentMeta = await res.json();
      showBackendError(false);
      sumSystem.textContent = currentMeta.system;
      sumScenario.textContent = currentMeta.scenario;
      sumLink.textContent = currentMeta.target_link;
      sumThreshold.textContent = currentMeta.threshold.toFixed(2);
      resetChart();
      playState.textContent = "Ready to play";
    } catch (err) {
      console.warn("Could not load run meta:", err.message);
      showBackendError(true);
    }
  }

  function closeSocket() {
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
      try { socket.close(); } catch { /* already closed */ }
      socket = null;
    }
  }

  function play() {
    const runId = runSelect.value;
    if (!runId) return;
    closeSocket();
    resetChart();
    playBtn.disabled = true;
    runSelect.disabled = true;
    playState.textContent = "Connecting…";

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/runs/${encodeURIComponent(runId)}/replay?speed=${speed}`;

    try {
      socket = new WebSocket(url);
    } catch (err) {
      console.warn("WebSocket construction failed:", err.message);
      finishPlay("Couldn't connect — check the backend", true);
      return;
    }

    socket.onopen = () => {
      playState.textContent = "Playing…";
    };

    socket.onmessage = (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return; // ignore malformed frame, don't crash the page
      }
      if (frame.kind === "point") {
        addPoint(frame.t, frame.util);
      } else if (frame.kind === "reroute") {
        addReroute(frame.t, frame.flow, frame.reason);
      } else if (frame.kind === "done") {
        finishPlay("Replay finished — press Play to watch again", false);
        closeSocket();
      }
    };

    socket.onerror = () => {
      finishPlay("Connection lost — backend may be down", true);
    };

    socket.onclose = () => {
      // If we got here without "done" or an explicit error, the server
      // just hung up; don't leave the button stuck on "Playing…".
      if (playBtn.disabled) finishPlay("Connection closed", false);
    };
  }

  function finishPlay(message, isError) {
    playBtn.disabled = false;
    runSelect.disabled = false;
    playState.textContent = message;
    showBackendError(isError);
  }

  // ---- event listeners ------------------------------------------------

  runSelect.addEventListener("change", onRunChange);
  playBtn.addEventListener("click", play);
  retryBtn.addEventListener("click", loadRuns);

  speedBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      speed = Number(btn.dataset.speed);
      speedBtns.forEach((b) => b.classList.toggle("is-active", b === btn));
    });
  });

  window.addEventListener("beforeunload", closeSocket);

  loadRuns();
})();
