const LAYER_DEFS = {
  military_installations: {
    title: "Military Installations",
    geoserverLayer: "geoint:military_installations",
  },
  satellite_imagery_catalog: {
    title: "Satellite Imagery Catalog",
    geoserverLayer: "geoint:satellite_imagery_catalog",
  },
  geoint_reports: {
    title: "GEOINT Reports",
    geoserverLayer: "geoint:geoint_reports",
  },
};

const ACCESS_LEVEL_LAYER_MAP = {
  Group1: ["military_installations", "satellite_imagery_catalog", "geoint_reports"],
  Group2: ["satellite_imagery_catalog"],
};

const state = {
  username: null,
  group: null,
  accessLevel: null,
  allowedLayers: [],
  layersByName: {},
  map: null,
  guardrailsEnabled: true,
};

const popupContainer = document.getElementById("popup");
const popupContent = document.getElementById("popup-content");
const popupCloser = document.getElementById("popup-closer");
const layerControls = document.getElementById("layer-controls");
const accessScope = document.getElementById("access-scope");
const chatScopeBadge = document.getElementById("chat-scope-badge");
const userIdentity = document.getElementById("user-identity");
const chatMessages = document.getElementById("chat-messages");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const guardrailsToggle = document.getElementById("guardrails-toggle");

const overlay = new ol.Overlay({
  element: popupContainer,
  autoPan: {
    animation: {
      duration: 250,
    },
  },
});

popupCloser.onclick = function () {
  overlay.setPosition(undefined);
  popupCloser.blur();
  return false;
};

async function fetchSession() {
  const res = await fetch("/api/session", {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.message || `Session request failed (${res.status})`);
  }

  return res.json();
}

function getCookie(name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = document.cookie.match(new RegExp(`(?:^|; )${escapedName}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function deriveSessionIdentity(session = {}) {
  const cookieAccessLevel = getCookie("accessLevel");
  const accessLevel = session.accessLevel || session.access_level || session.role || cookieAccessLevel || null;
  const group = session.group || session.groupName || accessLevel || cookieAccessLevel || null;
  const username =
    session.username ||
    session.user ||
    session.userName ||
    session.preferred_username ||
    getCookie("username") ||
    getCookie("user") ||
    getCookie("preferred_username") ||
    "Unknown";

  const allowedLayers = Array.isArray(session.allowedLayers) && session.allowedLayers.length > 0
    ? session.allowedLayers
    : (accessLevel && ACCESS_LEVEL_LAYER_MAP[accessLevel]) || [];

  return {
    username,
    group,
    accessLevel,
    allowedLayers,
  };
}

function formatLayerName(name) {
  return (LAYER_DEFS[name] && LAYER_DEFS[name].title) || name;
}

function updateScopeUi() {
  const humanList = state.allowedLayers.map(formatLayerName).join(", ") || "No layers";
  accessScope.textContent = `You have access to: ${humanList}`;
  chatScopeBadge.textContent = `Scope: ${humanList}`;
}

function updateUserIdentityUi() {
  if (!userIdentity) return;
  const username = state.username || "Unknown";
  const group = state.group || state.accessLevel || "Unknown";
  userIdentity.textContent = `User: ${username} | Group: ${group}`;
}

function buildLayerControls() {
  layerControls.innerHTML = "";

  state.allowedLayers.forEach((layerName) => {
    if (!state.layersByName[layerName]) return;

    const wrapper = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.addEventListener("change", () => {
      state.layersByName[layerName].setVisible(checkbox.checked);
    });

    const span = document.createElement("span");
    span.textContent = ` ${formatLayerName(layerName)}`;

    wrapper.appendChild(checkbox);
    wrapper.appendChild(span);
    layerControls.appendChild(wrapper);
  });
}

function createWmsLayer(layerName) {
  const layerDef = LAYER_DEFS[layerName];
  return new ol.layer.Tile({
    visible: true,
    source: new ol.source.TileWMS({
      url: "/api/geoserver/wms",
      params: {
        LAYERS: layerDef.geoserverLayer,
        TILED: true,
      },
      serverType: "geoserver",
      transition: 0,
    }),
    properties: {
      layerName,
      title: layerDef.title,
    },
  });
}

function showFeaturePopup(coord, layerName, feature) {
  const properties = feature.properties || {};
  const prettyProps = Object.entries(properties)
    .filter(([k]) => k !== "geometry")
    .map(([k, v]) => `<div><strong>${k}:</strong> ${String(v)}</div>`)
    .join("");

  popupContent.innerHTML = `
    <div><strong>Layer:</strong> ${formatLayerName(layerName)}</div>
    ${prettyProps || "<div>No attributes available</div>"}
  `;
  overlay.setPosition(coord);
}

async function fetchNearestFeature(layerName, lon, lat) {
  if (!state.allowedLayers.includes(layerName)) {
    return null; // defence-in-depth: never query restricted layers from UI.
  }

  const delta = 0.15;
  const bbox = `${lon - delta},${lat - delta},${lon + delta},${lat + delta},EPSG:4326`;
  const params = new URLSearchParams({
    service: "WFS",
    version: "1.1.0",
    request: "GetFeature",
    typeName: `geoint:${layerName}`,
    outputFormat: "application/json",
    srsName: "EPSG:4326",
    bbox,
    count: "1",
  });

  const res = await fetch(`/api/geoserver/ows?${params.toString()}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });

  if (!res.ok) {
    return null;
  }

  const data = await res.json().catch(() => null);
  if (!data || !Array.isArray(data.features) || data.features.length === 0) {
    return null;
  }
  return data.features[0];
}

function getTopVisibleAllowedLayerName() {
  for (let i = state.allowedLayers.length - 1; i >= 0; i -= 1) {
    const name = state.allowedLayers[i];
    const layer = state.layersByName[name];
    if (layer && layer.getVisible()) {
      return name;
    }
  }
  return null;
}

function addChatMessage(role, text) {
  const node = document.createElement("div");
  node.className = `msg ${role}`;
  node.textContent = text;
  chatMessages.appendChild(node);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function sendChat() {
  const message = chatInput.value.trim();
  if (!message) return;

  addChatMessage("user", message);
  chatInput.value = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        message,
        guardrails_enabled: state.guardrailsEnabled,
      }),
    });

    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || `Chat request failed (${res.status})`);
    }

    addChatMessage("assistant", body.response || "No response generated.");
  } catch (err) {
    addChatMessage("assistant", `Error: ${err.message}`);
  }
}

function bindChatUi() {
  chatSend.addEventListener("click", sendChat);
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      sendChat();
    }
  });

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      chatInput.value = chip.textContent.trim();
      sendChat();
    });
  });

  guardrailsToggle.addEventListener("click", () => {
    state.guardrailsEnabled = !state.guardrailsEnabled;
    guardrailsToggle.classList.toggle("enabled", state.guardrailsEnabled);
    guardrailsToggle.classList.toggle("disabled", !state.guardrailsEnabled);
    guardrailsToggle.textContent = state.guardrailsEnabled
      ? "AI Gateway: Guardrails Enabled"
      : "AI Gateway: Guardrails Disabled";
  });
}

function initMap() {
  const baseLayer = new ol.layer.Tile({
    source: new ol.source.OSM(),
  });

  const layers = [baseLayer];

  state.allowedLayers.forEach((layerName) => {
    if (!LAYER_DEFS[layerName]) return;
    const layer = createWmsLayer(layerName);
    state.layersByName[layerName] = layer;
    layers.push(layer);
  });

  state.map = new ol.Map({
    target: "map",
    layers,
    overlays: [overlay],
    view: new ol.View({
      center: ol.proj.fromLonLat([10, 25]),
      zoom: 2,
    }),
  });

  state.map.on("singleclick", async (evt) => {
    const layerName = getTopVisibleAllowedLayerName();
    if (!layerName || !state.allowedLayers.includes(layerName)) {
      return;
    }

    const [lon, lat] = ol.proj.toLonLat(evt.coordinate);
    const feature = await fetchNearestFeature(layerName, lon, lat);
    if (!feature) {
      overlay.setPosition(undefined);
      return;
    }
    showFeaturePopup(evt.coordinate, layerName, feature);
  });
}

async function bootstrap() {
  let session = {};
  let sessionError = null;

  try {
    session = await fetchSession();
  } catch (err) {
    sessionError = err;
  }

  try {
    const derived = deriveSessionIdentity(session);
    state.username = derived.username;
    state.group = derived.group || "Unknown";
    state.accessLevel = derived.accessLevel;
    state.allowedLayers = derived.allowedLayers;

    if (state.allowedLayers.length === 0) {
      const sessionFailureDetail = sessionError ? ` Session lookup issue: ${sessionError.message}` : "";
      throw new Error(`No authorized layers available for this session.${sessionFailureDetail}`);
    }

    updateUserIdentityUi();
    updateScopeUi();
    initMap();
    buildLayerControls();
    bindChatUi();
    addChatMessage("assistant", `Session established for ${state.username} (${state.group}).`);
  } catch (err) {
    updateUserIdentityUi();
    accessScope.textContent = `Access denied: ${err.message}`;
    chatScopeBadge.textContent = "Scope unavailable";
    addChatMessage("assistant", `Access denied: ${err.message}`);
  }
}

bootstrap();