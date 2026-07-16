// map.js
// Handles login (JWT), then renders live vehicle positions, movement
// trails, and anomaly alerts on a Leaflet map - fed by a WebSocket
// connection to the backend instead of polling.

const API_BASE = "http://localhost:8000";
const WS_BASE = "ws://localhost:8000";
const TOKEN_STORAGE_KEY = "phantomtrack_token";

let token = null;
let ws = null;
let reconnectTimer = null;

const map = L.map("map").setView([24.7136, 46.6753], 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const markers = {};      // device_id -> L.circleMarker
const trails = {};       // device_id -> L.polyline
const trailPoints = {};  // device_id -> [[lat,lon], ...]
const deviceState = {};  // device_id -> {last_lat, last_lon, last_speed, last_seen}
let recentAlerts = [];   // most recent first, capped

// ---------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------

function showLogin(message) {
  document.getElementById("login-screen").style.display = "flex";
  document.getElementById("app").classList.remove("visible");
  document.getElementById("login-error").textContent = message || "";
  if (ws) { ws.close(); ws = null; }
}

function showApp() {
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("app").classList.add("visible");
}

async function login(username, password) {
  const body = new URLSearchParams({ username, password });
  const res = await fetch(`${API_BASE}/auth/token`, { method: "POST", body });
  if (!res.ok) {
    throw new Error("Incorrect username or password");
  }
  const data = await res.json();
  return data.access_token;
}

function authHeaders() {
  return { Authorization: `Bearer ${token}` };
}

async function tryStoredToken() {
  const stored = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (!stored) return false;
  token = stored;
  // Confirm it's still valid (not expired) before committing to it.
  const res = await fetch(`${API_BASE}/devices`, { headers: authHeaders() });
  if (res.ok) {
    return true;
  }
  token = null;
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  return false;
}

document.getElementById("login-submit").addEventListener("click", async () => {
  const username = document.getElementById("login-username").value;
  const password = document.getElementById("login-password").value;
  try {
    token = await login(username, password);
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
    await start();
  } catch (err) {
    showLogin(err.message);
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  token = null;
  showLogin();
});

// ---------------------------------------------------------------------
// Map rendering
// ---------------------------------------------------------------------

function colorForSpeed(speed) {
  if (speed > 120) return "#ff5c72";
  if (speed > 80) return "#ffb454";
  return "#4ade80";
}

function upsertDevice(device_id, lat, lon, speed, last_seen) {
  if (lat == null || lon == null) return;
  deviceState[device_id] = { last_lat: lat, last_lon: lon, last_speed: speed, last_seen };

  const latlng = [lat, lon];
  if (!markers[device_id]) {
    markers[device_id] = L.circleMarker(latlng, {
      radius: 8, color: "#fff", weight: 1.5,
      fillColor: colorForSpeed(speed), fillOpacity: 0.9,
    }).addTo(map).bindPopup(device_id);
    trailPoints[device_id] = [];
    trails[device_id] = L.polyline([], { color: "#4b9bff", weight: 2, opacity: 0.5 }).addTo(map);
  } else {
    markers[device_id].setLatLng(latlng);
    markers[device_id].setStyle({ fillColor: colorForSpeed(speed) });
  }
  markers[device_id].setPopupContent(
    `<b>${device_id}</b><br>Speed: ${(speed ?? 0).toFixed(1)} km/h<br>Last seen: ${last_seen ?? "-"}`
  );

  trailPoints[device_id].push(latlng);
  if (trailPoints[device_id].length > 50) trailPoints[device_id].shift();
  trails[device_id].setLatLngs(trailPoints[device_id]);

  renderDeviceList();
}

function renderDeviceList() {
  const ids = Object.keys(deviceState).sort();
  document.getElementById("device-count").textContent = ids.length;
  const list = document.getElementById("device-list");
  list.innerHTML = "";
  ids.forEach((id) => {
    const d = deviceState[id];
    const el = document.createElement("div");
    el.className = "device-item";
    el.innerHTML = `
      <div class="id">${id}</div>
      <div class="meta">${(d.last_speed ?? 0).toFixed(1)} km/h &middot; ${d.last_seen ?? "never"}</div>
    `;
    el.onclick = async () => {
      if (markers[id]) {
        map.panTo(markers[id].getLatLng());
        markers[id].openPopup();
      }
      // Pull full history for this device on demand and render the trail
      // from actual stored data, not just what's accumulated client-side
      // since the page loaded.
      try {
        const res = await fetch(`${API_BASE}/devices/${id}/history?limit=50`, { headers: authHeaders() });
        if (res.ok) {
          const hist = await res.json();
          const pts = hist.reverse().map((h) => [h.lat, h.lon]);
          trailPoints[id] = pts;
          trails[id].setLatLngs(pts);
        }
      } catch (err) {
        console.error("history fetch failed", err);
      }
    };
    list.appendChild(el);
  });
}

function addAlert(alert) {
  recentAlerts.unshift(alert);
  recentAlerts = recentAlerts.slice(0, 30);
  renderAlertList();
}

function renderAlertList() {
  const list = document.getElementById("alert-list");
  list.innerHTML = "";
  recentAlerts.forEach((a) => {
    const el = document.createElement("div");
    el.className = `alert-item ${a.type}`;
    el.innerHTML = `
      <div class="type">${a.type} &middot; ${a.device_id}</div>
      <div class="desc">${a.description}</div>
      <div class="time">${a.timestamp}</div>
    `;
    list.appendChild(el);
  });
}

function setStatus(connected, text) {
  const el = document.getElementById("status");
  el.className = connected ? "connected" : "disconnected";
  document.getElementById("status-text").textContent = text;
}

// ---------------------------------------------------------------------
// WebSocket live feed
// ---------------------------------------------------------------------

function connectWebSocket() {
  ws = new WebSocket(`${WS_BASE}/ws?token=${encodeURIComponent(token)}`);

  ws.onopen = () => setStatus(true, "Live");

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.event === "location") {
      upsertDevice(msg.device_id, msg.lat, msg.lon, msg.speed, msg.timestamp);
    } else if (msg.event === "alert") {
      addAlert(msg);
    }
  };

  ws.onclose = () => {
    setStatus(false, "Disconnected - reconnecting...");
    // Auto-reconnect, e.g. after a token refresh or transient network drop.
    if (token && !reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectWebSocket();
      }, 3000);
    }
  };

  ws.onerror = () => ws.close();
}

// ---------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------

async function start() {
  showApp();
  setStatus(false, "Connecting...");

  // Seed initial state via REST (devices + recent alerts), then switch to
  // the WebSocket for everything live from here on.
  try {
    const [devicesRes, alertsRes] = await Promise.all([
      fetch(`${API_BASE}/devices`, { headers: authHeaders() }),
      fetch(`${API_BASE}/alerts?limit=30`, { headers: authHeaders() }),
    ]);
    const devices = await devicesRes.json();
    const alerts = await alertsRes.json();
    devices.forEach((d) => upsertDevice(d.device_id, d.last_lat, d.last_lon, d.last_speed, d.last_seen));
    recentAlerts = alerts;
    renderAlertList();
  } catch (err) {
    console.error("initial load failed", err);
  }

  connectWebSocket();
}

(async function init() {
  if (await tryStoredToken()) {
    await start();
  } else {
    showLogin();
  }
})();
