/* global ol */

const mapCenter = ol.proj.fromLonLat([0, 20]);

const osmLayer = new ol.layer.Tile({
  source: new ol.source.OSM(),
  visible: true,
});

const installationsLayer = new ol.layer.Tile({
  source: new ol.source.TileWMS({
    url: '/geoserver/geoint/wms',
    params: { LAYERS: 'geoint:military_installations', TILED: true },
    serverType: 'geoserver',
    transition: 0,
  }),
  visible: true,
});

const imageryLayer = new ol.layer.Tile({
  source: new ol.source.TileWMS({
    url: '/geoserver/geoint/wms',
    params: { LAYERS: 'geoint:satellite_imagery_catalog', TILED: true },
    serverType: 'geoserver',
    transition: 0,
  }),
  visible: true,
});

const reportsLayer = new ol.layer.Tile({
  source: new ol.source.TileWMS({
    url: '/geoserver/geoint/wms',
    params: { LAYERS: 'geoint:geoint_reports', TILED: true },
    serverType: 'geoserver',
    transition: 0,
  }),
  visible: true,
});

const markerSource = new ol.source.Vector();
const markerLayer = new ol.layer.Vector({
  source: markerSource,
  style: new ol.style.Style({
    image: new ol.style.Circle({
      radius: 6,
      fill: new ol.style.Fill({ color: '#e94560' }),
      stroke: new ol.style.Stroke({ color: '#ffffff', width: 2 }),
    }),
  }),
});

const popupContainer = document.getElementById('popup');
const popupContent = document.getElementById('popup-content');
const popupCloser = document.getElementById('popup-closer');

const overlay = new ol.Overlay({
  element: popupContainer,
  autoPan: { animation: { duration: 250 } },
});

const map = new ol.Map({
  target: 'map',
  layers: [osmLayer, imageryLayer, reportsLayer, installationsLayer, markerLayer],
  overlays: [overlay],
  view: new ol.View({
    center: mapCenter,
    zoom: 3,
  }),
});

popupCloser.onclick = function () {
  overlay.setPosition(undefined);
  popupCloser.blur();
  return false;
};

function escapeHtml(str) {
  return String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatFeatureProperties(properties) {
  const entries = Object.entries(properties)
    .filter(([k]) => k !== 'bbox' && k !== 'geometry')
    .map(([k, v]) => `<div><strong>${escapeHtml(k)}:</strong> ${escapeHtml(v ?? '')}</div>`)
    .join('');
  return entries || '<em>No attributes found.</em>';
}

map.on('singleclick', async (evt) => {
  const [lon, lat] = ol.proj.toLonLat(evt.coordinate);
  const url = `/geoserver/geoint/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=geoint:military_installations,geoint:satellite_imagery_catalog,geoint:geoint_reports&outputFormat=application/json&CQL_FILTER=INTERSECTS(geometry,POINT(${lon} ${lat}));INTERSECTS(footprint,POINT(${lon} ${lat}));INTERSECTS(area_of_interest,POINT(${lon} ${lat}))`;

  try {
    const resp = await fetch(url);
    const data = await resp.json();
    if (!data.features || data.features.length === 0) {
      overlay.setPosition(undefined);
      return;
    }

    const topFeature = data.features[0];
    const title = `<div><strong>${escapeHtml(topFeature.id || 'Feature')}</strong></div>`;
    popupContent.innerHTML = title + formatFeatureProperties(topFeature.properties || {});
    overlay.setPosition(evt.coordinate);
  } catch (err) {
    console.error('WFS feature query failed:', err);
  }
});

document.getElementById('layer-installations').addEventListener('change', (e) => {
  installationsLayer.setVisible(e.target.checked);
});
document.getElementById('layer-imagery').addEventListener('change', (e) => {
  imageryLayer.setVisible(e.target.checked);
});
document.getElementById('layer-reports').addEventListener('change', (e) => {
  reportsLayer.setVisible(e.target.checked);
});

const chatMessages = document.getElementById('chat-messages');
const chatInput = document.getElementById('chat-input');
const chatSend = document.getElementById('chat-send');

function addMessage(text, role = 'assistant') {
  const el = document.createElement('div');
  el.className = `msg ${role}`;
  el.textContent = text;
  chatMessages.appendChild(el);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function extractCoordinatesFromText(text) {
  const coords = [];
  const re = /\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]/g;
  let match;
  while ((match = re.exec(text)) !== null) {
    const lat = parseFloat(match[1]);
    const lon = parseFloat(match[2]);
    if (!Number.isNaN(lat) && !Number.isNaN(lon)) {
      coords.push([lat, lon]);
    }
  }
  return coords;
}

function zoomToCoordinates(coords) {
  if (!coords || coords.length === 0) return;

  markerSource.clear();
  const points = coords.map(([lat, lon]) => {
    const feature = new ol.Feature({
      geometry: new ol.geom.Point(ol.proj.fromLonLat([lon, lat])),
    });
    markerSource.addFeature(feature);
    return feature.getGeometry().getCoordinates();
  });

  const extent = ol.extent.boundingExtent(points);
  map.getView().fit(extent, { maxZoom: 8, duration: 800, padding: [80, 80, 80, 80] });

  // Clear temporary markers after 20 seconds for cleaner demo UX.
  setTimeout(() => markerSource.clear(), 20000);
}

async function sendChat() {
  const message = chatInput.value.trim();
  if (!message) return;

  addMessage(message, 'user');
  chatInput.value = '';

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`API error ${resp.status}: ${text}`);
    }

    const data = await resp.json();
    const responseText = data.response || 'No response from assistant.';
    addMessage(responseText, 'assistant');

    const coords = (data.coordinates && data.coordinates.length > 0)
      ? data.coordinates
      : extractCoordinatesFromText(responseText);
    zoomToCoordinates(coords);
  } catch (err) {
    console.error(err);
    addMessage(`Error contacting AI service: ${err.message}`, 'assistant');
  }
}

chatSend.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendChat();
});

document.querySelectorAll('.chip').forEach((chip) => {
  chip.addEventListener('click', () => {
    chatInput.value = chip.textContent;
    sendChat();
  });
});

addMessage('GEOINT assistant online. Ask a question to begin analysis.', 'assistant');
