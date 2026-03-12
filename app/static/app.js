/* ─────────────────────────────────────────────────────────────────────────────
   VehicleAnalysisAI dashboard — app.js
   All data comes from the Athena-backed FastAPI endpoints.
───────────────────────────────────────────────────────────────────────────── */

// ── Colour palette ────────────────────────────────────────────────────────────
const C = {
  hb:     '#dc2626',   hbA: 'rgba(220,38,38,.75)',
  ha:     '#ea580c',   haA: 'rgba(234,88,12,.75)',
  rt:     '#ca8a04',   rtA: 'rgba(202,138,4,.75)',
  accent: '#2563eb',   accentA: 'rgba(37,99,235,.75)',
  green:  '#16a34a',
  muted:  '#64748b',
  grid:   'rgba(226,232,240,1)',
  text:   '#0f172a',
};

// ── Chart.js global defaults (dark theme) ────────────────────────────────────
Chart.defaults.color = C.text;
Chart.defaults.borderColor = C.grid;
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
Chart.defaults.font.size = 12;
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.plugins.legend.labels.padding = 16;

// ── Utility ───────────────────────────────────────────────────────────────────
function fmt(n) {
  if (n == null) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

/** Map full alerttype name → short code for CSS class & display */
function alertLabel(t) {
  if (!t) return '—';
  if (t === 'harsh_braking')      return 'HB';
  if (t === 'harsh_acceleration') return 'HA';
  if (t === 'harsh_cornering')    return 'RT';
  return t;
}

/** Map alerttype to Colour constant */
function alertColour(t) {
  if (t === 'harsh_braking')      return C.hb;
  if (t === 'harsh_acceleration') return C.ha;
  return C.rt;
}

async function api(path, fallback = null) {
  try {
    const r = await fetch(path);
    if (!r.ok) {
      console.error(`API ${r.status} — ${path}`);
      return fallback;
    }
    return r.json();
  } catch (e) {
    console.error(`API fetch error — ${path}:`, e);
    return fallback;
  }
}

function destroyChart(id) {
  const c = Chart.getChart(id);
  if (c) c.destroy();
}

function showLoading(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading from Athena…</div>';
}

// ── Tab switching ─────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

    // Lazy-init map on first visit, then always fix tile rendering
    if (btn.dataset.tab === 'map') {
      if (!window._alertMapInited) {
        window._alertMapInited = true;
        initAlertMap();
        // Auto-load pre-checked layers
        document.querySelectorAll('.map-layer-toggle:checked').forEach(cb => {
          loadOneHotspotLayer(cb.value, true);
        });
      } else {
        setTimeout(() => alertMap && alertMap.invalidateSize(), 50);
      }
    }
    if (btn.dataset.tab === 'device') {
      setTimeout(() => deviceMap && deviceMap.invalidateSize(), 50);
    }
  });
});


// ─────────────────────────────────────────────────────────────────────────────
// ── TAB 1: Fleet Overview
// ─────────────────────────────────────────────────────────────────────────────

async function loadFleetSummary() {
  const data = await api('/api/fleet/summary', {});
  if (!data || !Object.keys(data).length) return;

  // Bad driving KPIs
  const badFields = {
    total_alerts:        'total_alerts',
    harsh_braking:       'harsh_braking',
    harsh_acceleration:  'harsh_acceleration',
    harsh_cornering:     'harsh_cornering',
    avg_speed_at_alert:  'avg_speed_at_alert',
    total_gps_points:    'total_gps_points',
  };
  for (const [key, apiKey] of Object.entries(badFields)) {
    const el = document.querySelector(`#kpi-${key} .kpi-val`);
    if (el) el.textContent = fmt(data[apiKey]);
  }

  // Good driving KPIs
  const el_safe    = document.querySelector('#kpi-safe_drivers .kpi-val');
  const el_risky   = document.querySelector('#kpi-risky_drivers .kpi-val');
  const el_total   = document.querySelector('#kpi-total_devices .kpi-val');
  const el_pct     = document.querySelector('#kpi-safe_driver_pct .kpi-val');
  const el_rate    = document.querySelector('#kpi-alert_rate_per_1k .kpi-val');
  if (el_safe)  el_safe.textContent  = fmt(data.safe_drivers);
  if (el_risky) el_risky.textContent = fmt(data.risky_drivers);
  if (el_total) el_total.textContent = fmt(data.total_devices);
  if (el_pct)   el_pct.textContent   = (data.safe_driver_pct ?? '—') + '%';
  if (el_rate)  el_rate.textContent  = data.alert_rate_per_1k ?? '—';

  // Alert type split donut
  buildAlertSplitChart(
    data.harsh_braking || 0,
    data.harsh_acceleration || 0,
    data.harsh_cornering || 0,
  );

  // Driver safety split donut
  buildDriverSplitChart(data.safe_drivers || 0, data.risky_drivers || 0);
}

function buildDriverSplitChart(safe, risky) {
  destroyChart('chartDriverSplit');
  new Chart(document.getElementById('chartDriverSplit'), {
    type: 'doughnut',
    data: {
      labels: ['Safe Drivers (0 alerts)', 'Risky Drivers (≥1 alert)'],
      datasets: [{
        data: [safe, risky],
        backgroundColor: [C.green, C.hb],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      cutout: '65%',
      plugins: {
        legend: { position: 'bottom' },
        tooltip: { callbacks: {
          label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)} (${safe+risky > 0 ? (ctx.raw/(safe+risky)*100).toFixed(1) : 0}%)`
        }},
      },
    },
  });
}

async function loadSafeDriving() {
  const rows = await api('/api/fleet/safe-drivers', []);
  if (!rows || !rows.length) return;
  const tbody = document.querySelector('#tblSafeDrivers tbody');
  tbody.innerHTML = rows.map((r, i) => `
    <tr>
      <td>${i + 1}</td>
      <td style="font-family:monospace;font-size:12px">${r.deviceid || '—'}</td>
      <td><strong style="color:var(--green)">${fmt(r.gps_points)}</strong></td>
      <td>${r.active_days ?? '—'}</td>
      <td>${fmt(r.avg_daily_pts)}</td>
      <td><button class="link-btn" onclick="drillDevice('${r.deviceid}')">Drill ↗</button></td>
    </tr>
  `).join('');
}


async function loadDailyTrend() {
  const rows = await api('/api/fleet/daily-trend', []);
  if (!rows || !rows.length) return;
  const labels = rows.map(r => r.date ? r.date.slice(5) : '');
  destroyChart('chartDailyTrend');
  new Chart(document.getElementById('chartDailyTrend'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Harsh Braking',      data: rows.map(r => r.harsh_braking      || 0), backgroundColor: C.hbA, borderColor: C.hb, borderWidth: 1 },
        { label: 'Harsh Acceleration', data: rows.map(r => r.harsh_acceleration  || 0), backgroundColor: C.haA, borderColor: C.ha, borderWidth: 1 },
        { label: 'Harsh Cornering',    data: rows.map(r => r.harsh_cornering     || 0), backgroundColor: C.rtA, borderColor: C.rt, borderWidth: 1 },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'top' } },
      scales: {
        x: { stacked: true, grid: { color: C.grid } },
        y: { stacked: true, grid: { color: C.grid }, ticks: { callback: v => fmt(v) } },
      },
    },
  });
}

function buildAlertSplitChart(hb, ha, rt) {
  destroyChart('chartAlertSplit');
  new Chart(document.getElementById('chartAlertSplit'), {
    type: 'doughnut',
    data: {
      labels: ['Harsh Braking', 'Harsh Acceleration', 'Harsh Cornering'],
      datasets: [{ data: [hb, ha, rt], backgroundColor: [C.hb, C.ha, C.rt], borderWidth: 0 }],
    },
    options: {
      responsive: true,
      cutout: '65%',
      plugins: {
        legend: { position: 'bottom' },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)}` } },
      },
    },
  });
}

async function loadHourlyDistribution() {
  const rows = await api('/api/fleet/hourly-distribution', []);
  if (!rows || !rows.length) return;
  const labels = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2,'0')}:00`);
  const byHour = Object.fromEntries(rows.map(r => [r.hour, r]));
  destroyChart('chartHourly');
  new Chart(document.getElementById('chartHourly'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Harsh Braking',      data: labels.map((_, h) => byHour[h]?.harsh_braking      || 0), backgroundColor: C.hbA, borderColor: C.hb, borderWidth: 1 },
        { label: 'Harsh Acceleration', data: labels.map((_, h) => byHour[h]?.harsh_acceleration  || 0), backgroundColor: C.haA, borderColor: C.ha, borderWidth: 1 },
        { label: 'Harsh Cornering',    data: labels.map((_, h) => byHour[h]?.harsh_cornering     || 0), backgroundColor: C.rtA, borderColor: C.rt, borderWidth: 1 },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'top' } },
      scales: {
        x: { stacked: true, grid: { color: C.grid } },
        y: { stacked: true, grid: { color: C.grid }, ticks: { callback: v => fmt(v) } },
      },
    },
  });
}

async function loadHotspotTable(alType) {
  const rows = await api(`/api/fleet/hotspots?alert_type=${alType}&limit=2000`, []);
  if (!rows || !rows.length) return;
  const top = rows.sort((a, b) => b.events - a.events).slice(0, 20);
  const tbody = document.querySelector('#tblHotspotLocations tbody');
  tbody.innerHTML = top.map((r, i) => `
    <tr>
      <td>${i + 1}</td>
      <td>${r.latitude}</td>
      <td>${r.longitude}</td>
      <td><strong>${fmt(r.events)}</strong></td>
      <td>${r.devices}</td>
      <td>${r.avg_speed ?? '—'} km/h</td>
    </tr>
  `).join('');
}

async function loadSpeedDist(alType) {
  const rows = await api(`/api/fleet/speed-distribution?alert_type=${alType}`, []);
  if (!rows || !rows.length) return;
  destroyChart('chartSpeed');
  new Chart(document.getElementById('chartSpeed'), {
    type: 'bar',
    data: {
      labels: rows.map(r => (r.speed_bucket || 0) + ' km/h'),
      datasets: [{
        label: 'Events',
        data: rows.map(r => r.events),
        backgroundColor: alType === 'harsh_braking' ? C.hbA
                       : alType === 'harsh_acceleration' ? C.haA : C.rtA,
        borderColor: alType === 'harsh_braking' ? C.hb
                   : alType === 'harsh_acceleration' ? C.ha : C.rt,
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: C.grid } },
        y: { grid: { color: C.grid }, ticks: { callback: v => fmt(v) } },
      },
    },
  });
}

async function loadTopDevices() {
  const rows = await api('/api/fleet/top-devices', []);
  if (!rows || !rows.length) return;
  const tbody = document.querySelector('#tblTopDevices tbody');
  tbody.innerHTML = rows.map((r, i) => `
    <tr>
      <td>${i + 1}</td>
      <td style="font-family:monospace;font-size:12px">${r.deviceid || '—'}</td>
      <td class="hb-col">${fmt(r.harsh_braking)}</td>
      <td class="ha-col">${fmt(r.harsh_acceleration)}</td>
      <td class="rt-col">${fmt(r.harsh_cornering)}</td>
      <td><strong>${fmt(r.total_alerts)}</strong></td>
      <td><button class="link-btn" onclick="drillDevice('${r.deviceid}')">Drill ↗</button></td>
    </tr>
  `).join('');
}

// Speed dist dropdown
document.getElementById('speedAlertType').addEventListener('change', e => {
  loadSpeedDist(e.target.value);
});

// Hotspot table dropdown
document.getElementById('hotspotAlertType').addEventListener('change', e => {
  loadHotspotTable(e.target.value);
});

async function loadOverview() {
  await Promise.allSettled([
    loadFleetSummary(),
    loadDailyTrend(),
    loadHourlyDistribution(),
    loadHotspotTable('harsh_braking'),
    loadSpeedDist('harsh_braking'),
    loadTopDevices(),
    loadSafeDriving(),
  ]);
  document.getElementById('refreshTs').textContent =
    'Updated ' + new Date().toLocaleTimeString();
}


// ─────────────────────────────────────────────────────────────────────────────
// ── TAB 2: Alert Map — all 3 types as toggleable overlays
// ─────────────────────────────────────────────────────────────────────────────

let alertMap = null;
const alertLayers = {}; // keyed by alert_type

function initAlertMap() {
  alertMap = L.map('alertMap', { zoomControl: false }).setView([20.5937, 78.9629], 5);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19,
  }).addTo(alertMap);
  L.control.zoom({ position: 'topright' }).addTo(alertMap);
  L.control.scale({ position: 'bottomright', maxWidth: 120, metric: true, imperial: false }).addTo(alertMap);
}

async function loadOneHotspotLayer(alType, checked) {
  if (!checked) {
    if (alertLayers[alType]) { alertMap.removeLayer(alertLayers[alType]); delete alertLayers[alType]; }
    updateMapStatus();
    return;
  }
  const colour = alType === 'harsh_braking' ? C.hb : alType === 'harsh_acceleration' ? C.ha : C.rt;
  const labelCls = alType === 'harsh_braking' ? 'popup-hb' : alType === 'harsh_acceleration' ? 'popup-ha' : 'popup-rt';
  const labelTxt = alType === 'harsh_braking' ? 'Harsh Braking' : alType === 'harsh_acceleration' ? 'Harsh Acceleration' : 'Harsh Cornering';

  const overlay = document.getElementById('alertMapLoading');
  if (overlay) overlay.style.display = 'flex';

  const rows = await api(`/api/fleet/hotspots?alert_type=${alType}&limit=2000`, []);

  if (overlay) overlay.style.display = 'none';
  if (!rows || !rows.length) return;
  if (alertLayers[alType]) alertMap.removeLayer(alertLayers[alType]);
  const layer = L.layerGroup();
  const maxE = Math.max(...rows.map(r => r.events), 1);
  rows.forEach(r => {
    if (!r.latitude || !r.longitude) return;
    const radius = 5 + Math.sqrt(r.events / maxE) * 18;
    L.circleMarker([r.latitude, r.longitude], {
      radius, color: colour, fillColor: colour, fillOpacity: 0.5, weight: 1.5,
    }).bindPopup(`
      <span class="popup-label ${labelCls}">${labelTxt}</span><br>
      <b>Events:</b> ${fmt(r.events)} &nbsp; <b>Devices:</b> ${r.devices}<br>
      <b>Avg speed:</b> ${r.avg_speed ?? '—'} km/h<br>
      <small style="color:#64748b">${r.latitude}, ${r.longitude}</small>
    `).addTo(layer);
  });
  alertLayers[alType] = layer;
  layer.addTo(alertMap);

  // Fit on first load
  const pts = rows.filter(r => r.latitude && r.longitude).map(r => [r.latitude, r.longitude]);
  if (pts.length > 0 && Object.keys(alertLayers).length === 1) {
    alertMap.fitBounds(L.latLngBounds(pts), { padding: [40, 40] });
  }
  updateMapStatus();
}

function updateMapStatus() {
  const counts = Object.keys(alertLayers).map(t => t.replace(/_/g,' ')).join(', ');
  document.getElementById('mapStatus').textContent = counts
    ? `Showing: ${counts}`
    : 'No layers selected';
}

document.querySelectorAll('.map-layer-toggle').forEach(cb => {
  cb.addEventListener('change', () => loadOneHotspotLayer(cb.value, cb.checked));
});


// ─────────────────────────────────────────────────────────────────────────────
// ── TAB 3: Device Drilldown
// ─────────────────────────────────────────────────────────────────────────────

let deviceMap = null;
let deviceMapLayer = null;  // alert event markers — persists across route reloads
let _routeLayer    = null;  // GPS polyline only — swapped when day changes
let _currentDeviceId   = null;
let _currentAlertRows  = [];

function initDeviceMap() {
  if (deviceMap) return;
  deviceMap = L.map('deviceMap', { zoomControl: false }).setView([20.5937, 78.9629], 5);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19,
  }).addTo(deviceMap);
  L.control.zoom({ position: 'topright' }).addTo(deviceMap);
  L.control.scale({ position: 'bottomright', maxWidth: 100, metric: true, imperial: false }).addTo(deviceMap);
}

async function drillDevice(deviceId) {
  // Switch to device tab first
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('.tab[data-tab="device"]').classList.add('active');
  document.getElementById('tab-device').classList.add('active');

  document.getElementById('deviceDrilldown').classList.remove('hidden');
  document.getElementById('deviceTitle').textContent = `Device: ${deviceId}`;
  document.getElementById('deviceMeta').textContent = 'Loading…';

  initDeviceMap();

  const loadingEl = document.getElementById('deviceMapLoading');
  if (loadingEl) loadingEl.style.display = 'flex';

  // Load all in parallel
  const [summary, daily, timeline, mapData, days] = await Promise.all([
    api(`/api/devices/${deviceId}/summary`,      {}),
    api(`/api/devices/${deviceId}/daily-alerts`, []),
    api(`/api/devices/${deviceId}/timeline`,     []),
    api(`/api/devices/${deviceId}/map`,          []),
    api(`/api/devices/${deviceId}/days`,         []),
  ]);

  // KPIs
  const dkpis = {
    harsh_braking:       'harsh_braking',
    harsh_acceleration:  'harsh_acceleration',
    harsh_cornering:     'harsh_cornering',
    total_alerts:        'total_alerts',
    avg_speed:           'avg_speed',
    max_speed:           'max_speed',
  };
  for (const [key, apiKey] of Object.entries(dkpis)) {
    const el = document.querySelector(`#dkpi-${key} .kpi-val`);
    if (el) el.textContent = fmt(summary[apiKey]);
  }
  document.getElementById('deviceMeta').textContent =
    `Last seen: ${summary.last_seen ?? '—'} · Avg speed: ${summary.avg_speed ?? '—'} km/h`;

  // Daily alerts chart + date strip
  buildDeviceDailyChart(daily);
  buildDateStrip(daily);

  // Safe driver badge
  const badgeEl = document.getElementById('safeBadge');
  if (badgeEl) {
    if (summary.is_safe_driver) {
      badgeEl.textContent = '\u2705 Safe Driver';
      badgeEl.style.color = '#16a34a';
    } else {
      badgeEl.textContent = '\u26A0\uFE0F Risky Driver';
      badgeEl.style.color = '#dc2626';
    }
  }

  // Timeline table
  buildTimelineTable(timeline);

  // Map — alert markers + GPS route polyline
  if (loadingEl) loadingEl.style.display = 'none';
  await buildDeviceMap(mapData, deviceId, days);

  // Scroll to drilldown
  document.getElementById('deviceDrilldown').scrollIntoView({ behavior: 'smooth' });
}

function buildDateStrip(daily) {
  const el = document.getElementById('dateBand');
  if (!el) return;
  // Build map of day number → total alerts
  const counts = {};
  daily.forEach(r => {
    if (!r.date) return;
    const day = parseInt(r.date.split('-')[2], 10);
    counts[day] = (r.harsh_braking || 0) + (r.harsh_acceleration || 0) + (r.harsh_cornering || 0);
  });
  const cells = [];
  for (let d = 1; d <= 31; d++) {
    const n = counts[d];
    let cls, tip;
    if (n == null) {
      cls = 'nodata'; tip = `Mar ${d}: no data`;
    } else if (n === 0) {
      cls = 'good'; tip = `Mar ${d}: \u2705 Safe – 0 alerts`;
    } else if (n <= 3) {
      cls = 'mild'; tip = `Mar ${d}: \u26A0\uFE0F ${n} alert${n > 1 ? 's' : ''}`;
    } else {
      cls = 'bad'; tip = `Mar ${d}: \uD83D\uDD34 ${n} alerts`;
    }
    const clk = (cls !== 'nodata') ? ` onclick="switchRouteDay(${d})" title="View route for Mar ${d}"` : '';
    cells.push(`<div class="ds-cell ${cls}" data-tip="${tip}"${clk}>${d}</div>`);
  }
  el.innerHTML = cells.join('');
}

function buildDeviceDailyChart(rows) {
  destroyChart('chartDeviceDaily');
  new Chart(document.getElementById('chartDeviceDaily'), {
    type: 'bar',
    data: {
      labels: rows.map(r => r.date ? r.date.slice(5) : ''),
      datasets: [
        { label: 'Harsh Braking',      data: rows.map(r => r.harsh_braking      || 0), backgroundColor: C.hbA, borderColor: C.hb, borderWidth: 1 },
        { label: 'Harsh Acceleration', data: rows.map(r => r.harsh_acceleration  || 0), backgroundColor: C.haA, borderColor: C.ha, borderWidth: 1 },
        { label: 'Harsh Cornering',    data: rows.map(r => r.harsh_cornering     || 0), backgroundColor: C.rtA, borderColor: C.rt, borderWidth: 1 },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'top' } },
      scales: {
        x: { stacked: true, grid: { color: C.grid } },
        y: { stacked: true, grid: { color: C.grid } },
      },
    },
  });
}

function buildTimelineTable(rows) {
  document.getElementById('timelineCount').textContent = `${rows.length} events`;
  const tbody = document.querySelector('#tblTimeline tbody');
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td style="white-space:nowrap">${r.gpstime ?? '—'}</td>
      <td><span class="alert-pill ${alertLabel(r.alerttype)}">${alertLabel(r.alerttype)}</span></td>
      <td>${r.alertdisplayname ?? '—'}</td>
      <td>${r.speed ?? '—'}</td>
      <td>${r.latitude != null ? r.latitude.toFixed(5) : '—'}</td>
      <td>${r.longitude != null ? r.longitude.toFixed(5) : '—'}</td>
    </tr>
  `).join('');
}

async function buildDeviceMap(alertRows, deviceId, days) {
  _currentDeviceId  = deviceId;
  _currentAlertRows = alertRows;

  // Clear previous layers
  if (deviceMapLayer) { deviceMap.removeLayer(deviceMapLayer); deviceMapLayer = null; }
  if (_routeLayer)    { deviceMap.removeLayer(_routeLayer);    _routeLayer    = null; }

  // ── Alert event markers (fixed layer — never reloaded on day switch) ──
  deviceMapLayer = L.layerGroup();
  const alertPts = [];
  alertRows.forEach(r => {
    if (!r.latitude || !r.longitude) return;
    alertPts.push([r.latitude, r.longitude]);
    const colour = alertColour(r.alerttype);
    const lCls = r.alerttype === 'harsh_braking' ? 'popup-hb'
               : r.alerttype === 'harsh_acceleration' ? 'popup-ha' : 'popup-rt';
    L.circleMarker([r.latitude, r.longitude], {
      radius: 8, color: '#fff', fillColor: colour, fillOpacity: 0.9, weight: 1.5,
    }).bindPopup(`
      <span class="popup-label ${lCls}">${alertLabel(r.alerttype)}</span> ${r.alertdisplayname ?? ''}<br>
      <b>Speed:</b> ${r.speed ?? '—'} km/h<br>
      <small style="color:#64748b">${r.gpstime ?? ''}</small>
    `).addTo(deviceMapLayer);
  });
  deviceMapLayer.addTo(deviceMap);

  // ── Route day picker ──
  const picker = document.getElementById('routeDayPicker');
  if (picker) {
    if (days && days.length > 0) {
      const TODAY = 12; // March 12 2026
      const sorted = [...days].sort((a, b) => a.day - b.day);
      const defaultDay = (sorted.find(d => d.day === TODAY) || sorted[sorted.length - 1]).day;
      picker.innerHTML = sorted.map(d =>
        `<button class="route-day-btn${d.day === defaultDay ? ' active' : ''}"
                 onclick="switchRouteDay(${d.day})" data-day="${d.day}">
           Mar&nbsp;${d.day}
         </button>`
      ).join('');
      await _loadRouteLayer(deviceId, defaultDay, alertPts);
    } else {
      picker.innerHTML = '<span class="no-days">No GPS track data in March</span>';
      if (alertPts.length > 0)
        deviceMap.fitBounds(L.latLngBounds(alertPts), { padding: [30, 30] });
      setTimeout(() => deviceMap.invalidateSize(), 150);
    }
  }
}

async function _loadRouteLayer(deviceId, day, alertPts) {
  if (_routeLayer) { deviceMap.removeLayer(_routeLayer); _routeLayer = null; }
  _routeLayer = L.layerGroup();
  const routePts = [];
  const dayParam  = day ? `?day=${day}` : '';
  const route = await api(`/api/devices/${deviceId}/route${dayParam}`, []);
  if (route && route.length > 1) {
    const pts = route.map(r => [r.latitude, r.longitude]);
    routePts.push(...pts);
    L.polyline(pts, { color: C.accent, weight: 3, opacity: 0.85, smoothFactor: 3 })
      .bindPopup(`<b>GPS Route — Mar ${day ?? '?'}</b><br>${route.length} pts<br>
                  <small>${route[0].gpstime} → ${route[route.length-1].gpstime}</small>`)
      .addTo(_routeLayer);
    L.circleMarker(pts[0], { radius: 7, color: '#fff', fillColor: C.green, fillOpacity: 1, weight: 2 })
      .bindPopup(`<b>▶ Start</b><br>${route[0].gpstime}<br>${route[0].speed ?? '—'} km/h`)
      .addTo(_routeLayer);
    L.circleMarker(pts[pts.length-1], { radius: 7, color: '#fff', fillColor: '#334155', fillOpacity: 1, weight: 2 })
      .bindPopup(`<b>■ End</b><br>${route[route.length-1].gpstime}<br>${route[route.length-1].speed ?? '—'} km/h`)
      .addTo(_routeLayer);
  }
  _routeLayer.addTo(deviceMap);
  const allPts = [...routePts, ...alertPts];
  if (allPts.length > 0)
    deviceMap.fitBounds(L.latLngBounds(allPts), { padding: [30, 30] });
  setTimeout(() => deviceMap.invalidateSize(), 150);
}

async function switchRouteDay(day) {
  document.querySelectorAll('.route-day-btn').forEach(b =>
    b.classList.toggle('active', parseInt(b.dataset.day) === day)
  );
  const alertPts = _currentAlertRows
    .filter(r => r.latitude && r.longitude)
    .map(r => [r.latitude, r.longitude]);
  await _loadRouteLayer(_currentDeviceId, day, alertPts);
}

// ── Device search ─────────────────────────────────────────────────────────────
document.getElementById('btnDeviceSearch').addEventListener('click', runDeviceSearch);
document.getElementById('deviceSearchInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') runDeviceSearch();
});

async function runDeviceSearch() {
  const q = document.getElementById('deviceSearchInput').value.trim();
  if (q.length < 3) return;
  const resultsEl = document.getElementById('deviceSearchResults');
  showLoading(resultsEl);
  const rows = await api(`/api/devices/search?q=${encodeURIComponent(q)}`, []);
  if (!rows || !rows.length) {
    resultsEl.innerHTML = '<p style="color:var(--muted);padding:8px">No devices found.</p>';
    return;
  }
  resultsEl.innerHTML = rows.map(r => `
    <div class="search-result-item" onclick="drillDevice('${r.deviceid}')">
      <div class="search-result-id">${r.deviceid}</div>
      <div class="search-result-meta">${fmt(r.total_alerts)} alerts · last: ${r.last_seen ?? '—'}</div>
    </div>
  `).join('');
}

// ── Refresh button ────────────────────────────────────────────────────────────
document.getElementById('btnRefresh').addEventListener('click', loadOverview);

// ── Boot ──────────────────────────────────────────────────────────────────────
loadOverview();
