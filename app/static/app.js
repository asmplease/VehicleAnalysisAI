const palette = {
    text: '#14213d',
    grid: 'rgba(20, 33, 61, 0.10)',
    blue: '#3662ff',
    blueSoft: 'rgba(54, 98, 255, 0.18)',
    teal: '#00a6a6',
    tealSoft: 'rgba(0, 166, 166, 0.18)',
    rose: '#e11d48',
    roseSoft: 'rgba(225, 29, 72, 0.18)',
    amber: '#d97706',
    amberSoft: 'rgba(217, 119, 6, 0.18)',
    mint: '#059669',
    mintSoft: 'rgba(5, 150, 105, 0.18)',
};

const state = {
    riskChart: null,
    trendChart: null,
    eventMixChart: null,
    vehicleMixChart: null,
    dataPointsChart: null,
    deviceScoreChart: null,
    deviceMap: null,
    deviceMapLayers: null,
    selectedDevice: null,
    // Deep analysis charts
    alertMixChart: null,
    alertSeverityChart: null,
    dailyAlertTrendChart: null,
    fleetCompareChart: null,
};

const panelMessages = {
    connection: 'Checking database connection…',
    overview: 'Loading overview metrics…',
    'risk-chart': 'Loading risk distribution…',
    'trend-chart': 'Loading trend data…',
    'event-mix': 'Loading event mix…',
    'vehicle-mix': 'Loading vehicle mix…',
    'data-points': 'Loading telemetry activity…',
    'top-risky': 'Loading risky devices…',
    'top-safe': 'Loading safe devices…',
    'quality-hotspots': 'Loading telemetry hotspots…',
    'latest-devices': 'Loading latest devices…',
    'device-search-results': 'Searching devices…',
    'device-summary': 'Loading device summary…',
    'device-score-history': 'Loading score history…',
    'device-trip-candidates': 'Loading trip candidates…',
    'device-map': 'Loading map and GPS markers…',
    'device-recent-positions': 'Loading telemetry points…',
    // Deep analysis
    'alert-mix': 'Loading alert distribution…',
    'fleet-comparison': 'Loading fleet comparison…',
    'alert-severity': 'Loading alert severity breakdown…',
    'daily-alert-trend': 'Loading daily alert trend…',
    'activity-calendar': 'Building activity calendar…',
    'gps-quality': 'Checking GPS data quality…',
};

async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
    }
    return response.json();
}

function formatNumber(value) {
    if (value === null || value === undefined) return '—';
    return new Intl.NumberFormat().format(value);
}

function formatDecimal(value, digits = 2) {
    if (value === null || value === undefined || value === '') return '—';
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(Number(value));
}

function formatValue(value) {
    if (value === null || value === undefined || value === '') return '—';
    return value;
}

function initializePanelLoaders() {
    document.querySelectorAll('[data-panel]').forEach(panel => {
        if (panel.querySelector('.panel-loader')) {
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'panel-loader';
        overlay.innerHTML = `
            <div class="panel-loader-spinner" aria-hidden="true"></div>
            <div class="panel-loader-text">${panelMessages[panel.dataset.panel] || 'Loading…'}</div>
        `;
        panel.appendChild(overlay);
    });
}

function setPanelLoading(panelNames, isLoading, message) {
    panelNames.forEach(panelName => {
        const panel = document.querySelector(`[data-panel="${panelName}"]`);
        if (!panel) {
            return;
        }

        const text = panel.querySelector('.panel-loader-text');
        if (text) {
            text.textContent = message || panelMessages[panelName] || 'Loading…';
        }

        panel.classList.toggle('is-loading', isLoading);
        panel.setAttribute('aria-busy', isLoading ? 'true' : 'false');
    });
}

function truncateText(value, length = 24) {
    if (!value || value.length <= length) return value;
    return `${value.slice(0, length)}…`;
}

function baseChartOptions() {
    return {
        responsive: true,
        plugins: {
            legend: {
                labels: { color: palette.text },
            },
        },
        scales: {
            x: {
                ticks: { color: palette.text },
                grid: { color: palette.grid },
            },
            y: {
                ticks: { color: palette.text },
                grid: { color: palette.grid },
            },
        },
    };
}

function buildOrUpdateChart(instance, canvasId, type, data, options) {
    if (instance) {
        instance.data = data;
        instance.options = options;
        instance.update();
        return instance;
    }

    const ctx = document.getElementById(canvasId);
    return new Chart(ctx, { type, data, options });
}

function renderOverviewCards(overview, advanced) {
    const summary = overview.overview[0];
    const quality = overview.quality[0];
    const pointsTrend = advanced.data_points_trend[0] || {};
    const cards = [
        ['Scored rows', formatNumber(summary.score_rows), 'Daily score facts already materialized'],
        ['Scored devices', formatNumber(summary.scored_devices), 'Distinct devices in driver score history'],
        ['Average score', formatDecimal(summary.avg_score), 'Fleet-wide behaviour score'],
        ['Critical devices', formatNumber(summary.critical_devices), 'Devices currently marked critical'],
        ['Telemetry rows', formatNumber(summary.position_rows), 'Raw recent GPS position records'],
        ['Bad speed rows', formatNumber(summary.high_speed_rows), 'Positions over 180 speed units'],
        ['Invalid timestamps', formatNumber(quality.invalid_gps_time_rows), 'GPS times outside safe range'],
        ['Latest points/day', formatNumber(pointsTrend.total_points), 'GPS points on latest analytics day'],
    ];

    document.getElementById('overviewCards').innerHTML = cards.map(([label, value, subtext]) => `
        <div class="card">
            <div class="label">${label}</div>
            <div class="value">${value}</div>
            <div class="subtext">${subtext}</div>
        </div>
    `).join('');
}

function renderRiskChart(overview) {
    const rows = overview.risk_distribution;
    state.riskChart = buildOrUpdateChart(
        state.riskChart,
        'riskChart',
        'doughnut',
        {
            labels: rows.map(row => row.risk_category),
            datasets: [{
                data: rows.map(row => row.devices),
                backgroundColor: [palette.rose, palette.amber, palette.blue, palette.mint],
                borderWidth: 0,
            }],
        },
        {
            responsive: true,
            plugins: {
                legend: { labels: { color: palette.text } },
            },
        }
    );
}

function renderTrendChart(rows) {
    const ordered = [...rows].reverse();
    state.trendChart = buildOrUpdateChart(
        state.trendChart,
        'trendChart',
        'line',
        {
            labels: ordered.map(row => row.score_date),
            datasets: [
                {
                    label: 'Average score',
                    data: ordered.map(row => row.avg_score),
                    borderColor: palette.blue,
                    backgroundColor: palette.blueSoft,
                    yAxisID: 'y',
                    tension: 0.35,
                    fill: true,
                },
                {
                    label: 'Harsh acceleration',
                    data: ordered.map(row => row.harsh_acceleration),
                    borderColor: palette.rose,
                    backgroundColor: palette.roseSoft,
                    yAxisID: 'y1',
                    tension: 0.35,
                },
            ],
        },
        {
            ...baseChartOptions(),
            interaction: { mode: 'index', intersect: false },
            scales: {
                ...baseChartOptions().scales,
                y: { position: 'left', ticks: { color: palette.text }, grid: { color: palette.grid } },
                y1: { position: 'right', ticks: { color: palette.text }, grid: { display: false } },
            },
        }
    );
}

function renderEventMixChart(advanced) {
    const rows = advanced.event_mix;
    state.eventMixChart = buildOrUpdateChart(
        state.eventMixChart,
        'eventMixChart',
        'bar',
        {
            labels: rows.map(row => row.event_type),
            datasets: [{
                label: 'Event count',
                data: rows.map(row => row.event_count),
                backgroundColor: [palette.rose, palette.amber, palette.teal],
                borderRadius: 12,
            }],
        },
        {
            ...baseChartOptions(),
            plugins: { legend: { display: false } },
        }
    );
}

function renderVehicleMixChart(advanced) {
    const rows = advanced.vehicle_mix;
    state.vehicleMixChart = buildOrUpdateChart(
        state.vehicleMixChart,
        'vehicleMixChart',
        'polarArea',
        {
            labels: rows.map(row => row.vehicle_type),
            datasets: [{
                data: rows.map(row => row.devices),
                backgroundColor: [palette.blue, palette.teal, palette.amber, palette.mint, '#8b5cf6', '#ec4899'],
                borderWidth: 0,
            }],
        },
        {
            responsive: true,
            scales: {
                r: {
                    ticks: { color: palette.text, backdropColor: 'transparent' },
                    grid: { color: palette.grid },
                },
            },
            plugins: { legend: { labels: { color: palette.text } } },
        }
    );
}

function renderDataPointsChart(advanced) {
    const rows = [...advanced.data_points_trend].reverse();
    state.dataPointsChart = buildOrUpdateChart(
        state.dataPointsChart,
        'dataPointsChart',
        'bar',
        {
            labels: rows.map(row => row.analytics_date),
            datasets: [
                {
                    type: 'bar',
                    label: 'Total points',
                    data: rows.map(row => row.total_points),
                    backgroundColor: palette.tealSoft,
                    borderColor: palette.teal,
                    borderWidth: 1,
                    yAxisID: 'y',
                },
                {
                    type: 'line',
                    label: 'Active devices',
                    data: rows.map(row => row.active_devices),
                    borderColor: palette.blue,
                    backgroundColor: palette.blueSoft,
                    tension: 0.3,
                    yAxisID: 'y1',
                },
            ],
        },
        {
            ...baseChartOptions(),
            interaction: { mode: 'index', intersect: false },
            scales: {
                ...baseChartOptions().scales,
                y: { position: 'left', ticks: { color: palette.text }, grid: { color: palette.grid } },
                y1: { position: 'right', ticks: { color: palette.text }, grid: { display: false } },
            },
        }
    );
}

function renderTable(containerId, columns, rows, emptyMessage = 'No records found.') {
    const container = document.getElementById(containerId);
    if (!rows.length) {
        container.innerHTML = `<p class="warn">${emptyMessage}</p>`;
        return;
    }

    container.innerHTML = `
        <table class="table">
            <thead>
                <tr>
                    ${columns.map(column => `<th>${column.label}</th>`).join('')}
                </tr>
            </thead>
            <tbody>
                ${rows.map(row => `
                    <tr>
                        ${columns.map(column => `<td>${column.render ? column.render(row[column.key], row) : formatValue(row[column.key])}</td>`).join('')}
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderLeaderboards(advanced) {
    renderTable(
        'topRiskyDevices',
        [
            { key: 'device_id', label: 'Device', render: value => truncateText(value, 18) },
            { key: 'current_score', label: 'Score', render: value => formatValue(value) },
            { key: 'total_deductions', label: 'Deductions', render: value => formatNumber(value) },
            { key: 'score_date', label: 'Date' },
        ],
        advanced.top_risky_devices,
        'No risky devices found.'
    );

    renderTable(
        'topSafeDevices',
        [
            { key: 'device_id', label: 'Device', render: value => truncateText(value, 18) },
            { key: 'current_score', label: 'Score' },
            { key: 'total_deductions', label: 'Deductions', render: value => formatNumber(value) },
            { key: 'score_date', label: 'Date' },
        ],
        advanced.top_safe_devices,
        'No safe-device records found.'
    );

    renderTable(
        'qualityHotspots',
        [
            { key: 'device_id', label: 'Device', render: value => truncateText(value, 18) },
            { key: 'bad_speed_rows', label: 'Bad rows', render: value => formatNumber(value) },
            { key: 'max_speed', label: 'Max speed', render: value => formatDecimal(value, 1) },
            { key: 'latest_bad_gps_time', label: 'Latest bad GPS' },
        ],
        advanced.quality_hotspots,
        'No telemetry hotspots detected.'
    );
}

function renderSearchResults(rows) {
    const container = document.getElementById('searchResults');
    if (!rows.length) {
        container.innerHTML = '<p class="warn">No matching devices.</p>';
        return;
    }

    container.innerHTML = rows.map(row => `
        <button class="list-item" data-device-id="${row.device_id}">
            <div class="badge">${formatValue(row.risk_category || 'Unknown risk')}${row.exact_match ? ' · exact' : ''}</div>
            <div><strong>${row.device_id}</strong></div>
            <div class="muted">Today ${formatValue(row.score_today)} · 7d ${formatDecimal(row.score_7day_avg)}</div>
            <div class="muted">30d ${formatDecimal(row.score_30day_avg)} · Updated ${formatValue(row.last_updated)}</div>
        </button>
    `).join('');

    container.querySelectorAll('[data-device-id]').forEach(element => {
        element.addEventListener('click', () => loadDeviceDetails(element.dataset.deviceId));
    });
}

function renderLatestDevices(rows) {
    const container = document.getElementById('latestDevices');
    if (!rows.length) {
        container.innerHTML = '<p class="warn">No recent devices found.</p>';
        return;
    }

    container.innerHTML = rows.map(row => `
        <button class="quick-device-button" data-latest-device-id="${row.device_id}">
            <div class="badge">${formatValue(row.risk_category || 'Unknown risk')}</div>
            <div><strong>${row.device_id}</strong></div>
            <div class="quick-device-meta">Score date ${formatValue(row.latest_score_date)}</div>
            <div class="quick-device-meta">Updated ${formatValue(row.last_updated || row.latest_score_update)}</div>
        </button>
    `).join('');

    container.querySelectorAll('[data-latest-device-id]').forEach(element => {
        element.addEventListener('click', async () => {
            const deviceId = element.dataset.latestDeviceId;
            document.getElementById('deviceSearch').value = deviceId;
            await loadDeviceDetails(deviceId);
        });
    });
}

function renderDeviceSummary(summary) {
    const profile = summary.profile || {};
    const scoreSummary = summary.score_summary || {};
    const positionSummary = summary.position_summary || {};
    const tiles = [
        ['Device', profile.device_id || state.selectedDevice],
        ['Risk', profile.risk_category],
        ['Score today', profile.score_today],
        ['7d avg', formatDecimal(profile.score_7day_avg)],
        ['30d avg', formatDecimal(profile.score_30day_avg)],
        ['Alerts 7d', formatNumber(profile.total_alerts_7d)],
        ['Alerts 30d', formatNumber(profile.total_alerts_30d)],
        ['Brake /100km', formatDecimal(profile.braking_rate_per_100km)],
        ['Accel /100km', formatDecimal(profile.accel_rate_per_100km)],
        ['Corner /100km', formatDecimal(profile.cornering_rate_per_100km)],
        ['Score days', formatNumber(scoreSummary.score_days)],
        ['Avg score', formatDecimal(scoreSummary.avg_score)],
        ['Total HB', formatNumber(scoreSummary.total_hb)],
        ['Total HA', formatNumber(scoreSummary.total_ha)],
        ['Total RT', formatNumber(scoreSummary.total_rt)],
        ['Latest GPS', positionSummary.latest_gps_time],
        ['Avg speed', formatDecimal(positionSummary.avg_speed)],
        ['Bad speed rows', formatNumber(positionSummary.bad_speed_rows)],
    ];

    document.getElementById('deviceSummary').innerHTML = tiles.map(([label, value]) => `
        <div class="detail-tile">
            <div class="label">${label}</div>
            <div class="value">${formatValue(value)}</div>
        </div>
    `).join('');
}

function renderDeviceScoreChart(rows) {
    const ordered = [...rows].reverse();
    state.deviceScoreChart = buildOrUpdateChart(
        state.deviceScoreChart,
        'deviceScoreChart',
        'line',
        {
            labels: ordered.map(row => row.score_date),
            datasets: [
                {
                    label: 'Current score',
                    data: ordered.map(row => row.current_score),
                    borderColor: palette.blue,
                    backgroundColor: palette.blueSoft,
                    tension: 0.25,
                    fill: true,
                },
                {
                    label: 'Total deductions',
                    data: ordered.map(row => row.total_deductions),
                    borderColor: palette.rose,
                    backgroundColor: palette.roseSoft,
                    tension: 0.25,
                    yAxisID: 'y1',
                },
            ],
        },
        {
            ...baseChartOptions(),
            interaction: { mode: 'index', intersect: false },
            scales: {
                ...baseChartOptions().scales,
                y: { position: 'left', ticks: { color: palette.text }, grid: { color: palette.grid } },
                y1: { position: 'right', ticks: { color: palette.text }, grid: { display: false } },
            },
        }
    );
}

function renderTripCandidates(rows) {
    renderTable(
        'tripCandidates',
        [
            { key: 'trip_id', label: 'Trip' },
            { key: 'trip_start', label: 'Start' },
            { key: 'trip_end', label: 'End' },
            { key: 'point_count', label: 'Points', render: value => formatNumber(value) },
            { key: 'avg_speed', label: 'Avg speed', render: value => formatDecimal(value, 1) },
            { key: 'max_speed', label: 'Max speed', render: value => formatDecimal(value, 1) },
        ],
        rows.slice(0, 12),
        'No trip candidates found.'
    );
}

function renderRecentPositions(rows) {
    renderTable(
        'recentPositions',
        [
            { key: 'gps_time', label: 'GPS time' },
            { key: 'latitude', label: 'Latitude', render: value => formatDecimal(value, 6) },
            { key: 'longitude', label: 'Longitude', render: value => formatDecimal(value, 6) },
            { key: 'device_speed', label: 'Speed', render: value => formatDecimal(value, 1) },
            { key: 'vehicle_type', label: 'Vehicle' },
        ],
        rows.slice(0, 20),
        'No recent telemetry points found.'
    );
}

function getBehaviourStyle(speed) {
    const value = Number(speed || 0);
    if (value <= 1) {
        return { label: 'Stopped', color: '#94a3b8', radius: 5 };
    }
    if (value <= 20) {
        return { label: 'Calm', color: '#059669', radius: 5 };
    }
    if (value <= 60) {
        return { label: 'Cruising', color: '#3662ff', radius: 6 };
    }
    if (value <= 90) {
        return { label: 'Brisk', color: '#d97706', radius: 7 };
    }
    return { label: 'Aggressive', color: '#e11d48', radius: 8 };
}

function ensureDeviceMap() {
    if (state.deviceMap || typeof L === 'undefined') {
        return;
    }

    state.deviceMap = L.map('deviceMap', {
        zoomControl: true,
        scrollWheelZoom: true,
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
    }).addTo(state.deviceMap);

    state.deviceMapLayers = L.layerGroup().addTo(state.deviceMap);
    state.deviceMap.setView([20.5937, 78.9629], 5);
}

function renderDeviceMap(rows, deviceId) {
    ensureDeviceMap();
    if (!state.deviceMap || !state.deviceMapLayers) {
        return;
    }

    state.deviceMapLayers.clearLayers();

    const validRows = rows
        .filter(row => row.latitude !== null && row.longitude !== null)
        .map(row => ({
            ...row,
            latitude: Number(row.latitude),
            longitude: Number(row.longitude),
            device_speed: Number(row.device_speed || 0),
        }))
        .filter(row => Number.isFinite(row.latitude) && Number.isFinite(row.longitude));

    if (!validRows.length) {
        state.deviceMap.setView([20.5937, 78.9629], 5);
        document.getElementById('deviceMap').setAttribute('data-empty', 'true');
        return;
    }

    document.getElementById('deviceMap').removeAttribute('data-empty');
    const ordered = [...validRows].reverse();
    const latLngs = ordered.map(row => [row.latitude, row.longitude]);

    L.polyline(latLngs, {
        color: '#3662ff',
        weight: 3,
        opacity: 0.55,
    }).addTo(state.deviceMapLayers);

    ordered.forEach((row, index) => {
        const behaviour = getBehaviourStyle(row.device_speed);
        const isLatest = index === ordered.length - 1;
        const marker = L.circleMarker([row.latitude, row.longitude], {
            radius: isLatest ? behaviour.radius + 2 : behaviour.radius,
            color: '#ffffff',
            weight: isLatest ? 2.5 : 1.5,
            fillColor: behaviour.color,
            fillOpacity: 0.9,
        });

        marker.bindPopup(`
            <div class="map-popup">
                <strong>${deviceId}</strong><br>
                Behaviour: ${behaviour.label}<br>
                GPS time: ${formatValue(row.gps_time)}<br>
                Speed: ${formatDecimal(row.device_speed, 1)}<br>
                Lat/Lon: ${formatDecimal(row.latitude, 6)}, ${formatDecimal(row.longitude, 6)}<br>
                Vehicle: ${formatValue(row.vehicle_type)}
            </div>
        `);

        marker.addTo(state.deviceMapLayers);
        if (isLatest) {
            marker.openPopup();
        }
    });

    const bounds = L.latLngBounds(latLngs);
    state.deviceMap.fitBounds(bounds.pad(0.15));
    setTimeout(() => state.deviceMap.invalidateSize(), 50);
}

async function loadOverview() {
    const panels = ['connection', 'overview', 'risk-chart', 'trend-chart', 'event-mix', 'vehicle-mix', 'data-points', 'top-risky', 'top-safe', 'quality-hotspots', 'latest-devices'];
    setPanelLoading(panels, true);
    const trendDays = document.getElementById('trendDays').value;
    try {
        const [overview, advanced, trend, latestDevices, health] = await Promise.all([
            fetchJson('/api/overview'),
            fetchJson('/api/analytics/advanced'),
            fetchJson(`/api/trends/daily?days=${trendDays}`),
            fetchJson('/api/devices/latest?limit=12'),
            fetchJson('/api/health'),
        ]);

        renderOverviewCards(overview, advanced);
        renderRiskChart(overview);
        renderTrendChart(trend);
        renderEventMixChart(advanced);
        renderVehicleMixChart(advanced);
        renderDataPointsChart(advanced);
        renderLeaderboards(advanced);
        renderLatestDevices(latestDevices);
        document.getElementById('healthStatus').innerHTML = `<span class="ok">Connected</span> · ${health.database_name} · ${health.checked_at}`;
    } finally {
        setPanelLoading(panels, false);
    }
}

async function searchDevices() {
    const query = document.getElementById('deviceSearch').value.trim();
    if (query.length < 3) {
        document.getElementById('searchResults').innerHTML = '<p class="warn">Enter at least 3 characters.</p>';
        return;
    }

    setPanelLoading(['device-search-results'], true, 'Searching fleet devices…');
    try {
        const rows = await fetchJson(`/api/devices/search?q=${encodeURIComponent(query)}`);
        renderSearchResults(rows);

        const preferred = rows.find(row => row.device_id === query) || rows[0];
        if (preferred) {
            await loadDeviceDetails(preferred.device_id);
            return;
        }

        try {
            await loadDeviceDetails(query);
        } catch {
            document.getElementById('deviceSummary').innerHTML = '<p class="warn">No device details found for that id.</p>';
            document.getElementById('tripCandidates').innerHTML = '<p class="warn">No trip candidates found.</p>';
            document.getElementById('recentPositions').innerHTML = '<p class="warn">No recent telemetry points found.</p>';
        }
    } finally {
        setPanelLoading(['device-search-results'], false);
    }
}

// ═══════════════════════════════════════════════════════════════════
// DEEP ANALYSIS — Device-wise full analysis rendering
// ═══════════════════════════════════════════════════════════════════

function renderScoreStats(stats) {
    const row = document.getElementById('scoreStatsRow');
    if (!row) return;
    if (!stats || !stats.total_days) { row.innerHTML = ''; return; }
    const trend = (stats.latest_score > stats.oldest_score) ? '▲' :
                  (stats.latest_score < stats.oldest_score) ? '▼' : '→';
    const trendCls = (stats.latest_score > stats.oldest_score) ? 'up' :
                     (stats.latest_score < stats.oldest_score) ? 'down' : 'flat';
    const cards = [
        { label: 'Latest score',  val: stats.latest_score ?? '—',  sub: 'most recent day' },
        { label: 'Best score',    val: stats.best_score ?? '—',   sub: `in ${stats.total_days} days` },
        { label: 'Worst score',   val: stats.worst_score ?? '—',  sub: `in ${stats.total_days} days` },
        { label: 'Average score', val: stats.avg_score ?? '—',    sub: `over ${stats.total_days} days` },
        { label: 'Active days',   val: stats.total_days ?? '—',   sub: `zero-score days: ${stats.zero_score_days ?? 0}` },
        { label: 'Score trend',   val: trend,                      sub: `${stats.oldest_score} → ${stats.latest_score}`, cls: trendCls },
    ];
    row.innerHTML = cards.map(c => `
        <div class="score-stat-card">
            <div class="stat-label">${c.label}</div>
            <div class="stat-val${c.cls ? ` ${c.cls}` : ''}">${c.val}</div>
            <div class="stat-sub">${c.sub}</div>
        </div>`).join('');
}

function renderAlertMixChart(totals) {
    if (state.alertMixChart) { state.alertMixChart.destroy(); state.alertMixChart = null; }
    const ctx = document.getElementById('alertMixChart');
    if (!ctx) return;
    const hb = totals.total_hb || 0;
    const ha = totals.total_ha || 0;
    const rt = totals.total_rt || 0;
    state.alertMixChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Harsh Braking', 'Harsh Acceleration', 'Rash Turning'],
            datasets: [{
                data: [hb, ha, rt],
                backgroundColor: [palette.rose, palette.amber, palette.blue],
                borderWidth: 2,
                borderColor: '#fff',
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom', labels: { font: { size: 11 } } },
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.label}: ${formatNumber(ctx.parsed)}`
                    }
                }
            },
            cutout: '60%',
        }
    });
}

function renderAlertSeverityChart(totals) {
    if (state.alertSeverityChart) { state.alertSeverityChart.destroy(); state.alertSeverityChart = null; }
    const ctx = document.getElementById('alertSeverityChart');
    if (!ctx) return;
    state.alertSeverityChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Harsh Braking (HB)', 'Harsh Acceleration (HA)', 'Rash Turning (RT)'],
            datasets: [
                { label: 'Critical', data: [totals.hb_critical || 0, 0, 0], backgroundColor: '#7f1d1d', stack: 's' },
                { label: 'High',     data: [totals.hb_high || 0, totals.ha_high || 0, totals.rt_high || 0],      backgroundColor: palette.rose,  stack: 's' },
                { label: 'Medium',   data: [totals.hb_medium || 0, totals.ha_medium || 0, totals.rt_medium || 0], backgroundColor: palette.amber, stack: 's' },
                { label: 'Low',      data: [totals.hb_low || 0, totals.ha_low || 0, totals.rt_low || 0],          backgroundColor: palette.teal,  stack: 's' },
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'top', labels: { font: { size: 11 } } } },
            scales: {
                x: { stacked: true, grid: { display: false } },
                y: { stacked: true, grid: { color: palette.grid }, title: { display: true, text: 'Count' } }
            }
        }
    });
}

function renderDailyAlertTrendChart(history) {
    if (state.dailyAlertTrendChart) { state.dailyAlertTrendChart.destroy(); state.dailyAlertTrendChart = null; }
    const ctx = document.getElementById('dailyAlertTrendChart');
    if (!ctx || !history || !history.length) return;
    const sorted = [...history].reverse(); // history is desc from API, flip to asc for chart
    const labels = sorted.map(r => r.score_date);
    state.dailyAlertTrendChart = new Chart(ctx, {
        data: {
            labels,
            datasets: [
                {
                    type: 'line',
                    label: 'Score',
                    data: sorted.map(r => r.current_score),
                    borderColor: palette.blue,
                    backgroundColor: palette.blueSoft,
                    yAxisID: 'yScore',
                    borderWidth: 2,
                    pointRadius: 3,
                    tension: 0.3,
                    fill: true,
                    order: 1,
                },
                { type: 'bar', label: 'HB', data: sorted.map(r => r.total_hb), backgroundColor: palette.roseSoft,  borderColor: palette.rose,  borderWidth: 1, yAxisID: 'yAlerts', stack: 'alerts', order: 2 },
                { type: 'bar', label: 'HA', data: sorted.map(r => r.total_ha), backgroundColor: palette.amberSoft, borderColor: palette.amber, borderWidth: 1, yAxisID: 'yAlerts', stack: 'alerts', order: 3 },
                { type: 'bar', label: 'RT', data: sorted.map(r => r.total_rt), backgroundColor: palette.tealSoft,  borderColor: palette.teal,  borderWidth: 1, yAxisID: 'yAlerts', stack: 'alerts', order: 4 },
            ]
        },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { position: 'top', labels: { font: { size: 11 } } } },
            scales: {
                x: { grid: { display: false }, ticks: { maxRotation: 45 } },
                yScore:  { type: 'linear', position: 'left',  min: 0, max: 100, title: { display: true, text: 'Score' },  grid: { color: palette.grid } },
                yAlerts: { type: 'linear', position: 'right', min: 0, title: { display: true, text: 'Alerts' }, grid: { display: false } },
            }
        }
    });
}

function renderFleetCompareChart(fc) {
    if (state.fleetCompareChart) { state.fleetCompareChart.destroy(); state.fleetCompareChart = null; }
    const ctx = document.getElementById('fleetCompareChart');
    if (!ctx || !fc || !fc.fleet_avg_score) return;
    const labels  = ['This device', 'Fleet avg', 'Fleet median', 'Fleet P75'];
    const values  = [fc.device_score, fc.fleet_avg_score, fc.fleet_median_score, fc.fleet_p75_score];
    const bColors = [palette.blue, '#a3b4d6', palette.amber, palette.teal];
    state.fleetCompareChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Score',
                data: values,
                backgroundColor: bColors,
                borderRadius: 6,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            scales: {
                x: { min: 0, max: 100, grid: { color: palette.grid }, title: { display: true, text: 'Score (0-100)' } },
                y: { grid: { display: false } }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        afterBody: (items) => {
                            if (items[0]?.dataIndex === 0 && fc.device_percentile_rank != null) {
                                return [`Percentile rank: ${fc.device_percentile_rank}%`, `Fleet total: ${formatNumber(fc.fleet_total_devices)} devices`];
                            }
                            return [];
                        }
                    }
                }
            }
        }
    });
}

function renderActivityCalendar(calendar) {
    const container = document.getElementById('activityCalendar');
    if (!container) return;
    const map = {};
    (calendar || []).forEach(r => { map[r.score_date] = r; });
    const days = [];
    for (let i = 29; i >= 0; i--) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        const k = d.toISOString().slice(0, 10);
        days.push({ k, label: d.getDate(), data: map[k] || null });
    }
    container.innerHTML = days.map(({ k, label, data }) => {
        if (!data) {
            return `<div class="cal-day no-data" title="${k}&#10;No data">${label}</div>`;
        }
        const s = data.current_score;
        const alerts = data.total_alerts ?? 0;
        const cls = s >= 80 ? 'score-great' : s >= 60 ? 'score-good' : s >= 40 ? 'score-mid' : s > 0 ? 'score-low' : 'score-zero';
        return `<div class="cal-day ${cls}" title="${k}&#10;Score: ${s}&#10;Alerts: ${alerts}">${label}<br><span>${s}</span></div>`;
    }).join('');
}

function renderGpsQuality(q) {
    const el = document.getElementById('gpsQuality');
    if (!el) return;
    if (!q || !q.total_position_rows) {
        el.innerHTML = '<p class="muted" style="padding:16px 0">No GPS position data found for this device.</p>';
        return;
    }
    const total = q.total_position_rows || 1;
    const pctBad = ((q.high_speed_rows / total) * 100).toFixed(1);
    const pctTs  = ((q.invalid_timestamp_rows / total) * 100).toFixed(1);
    const items = [
        { k: 'Total position rows',          v: formatNumber(q.total_position_rows), cls: '' },
        { k: 'Valid-speed rows (≤180 km/h)', v: formatNumber(q.valid_speed_rows),    cls: q.valid_speed_rows > 0 ? 'ok' : 'warn' },
        { k: 'Bad-speed rows (>180 km/h)',   v: `${formatNumber(q.high_speed_rows)} (${pctBad}%)`, cls: pctBad > 5 ? 'danger' : pctBad > 0 ? 'warn' : 'ok' },
        { k: 'Invalid timestamps',           v: `${formatNumber(q.invalid_timestamp_rows)} (${pctTs}%)`, cls: pctTs > 5 ? 'danger' : pctTs > 0 ? 'warn' : 'ok' },
        { k: 'Avg speed (clean)',            v: q.clean_avg_speed != null ? `${formatDecimal(q.clean_avg_speed, 1)} km/h` : '—', cls: '' },
        { k: 'Max speed (clean)',            v: q.clean_max_speed != null ? `${formatDecimal(q.clean_max_speed, 1)} km/h` : '—', cls: '' },
        { k: 'Earliest valid GPS time',      v: formatValue(q.earliest_valid_gps), cls: '' },
        { k: 'Latest valid GPS time',        v: formatValue(q.latest_valid_gps),   cls: '' },
    ];
    el.innerHTML = items.map(({ k, v, cls }) => `
        <div class="stat-item">
            <span class="stat-k">${k}</span>
            <span class="stat-v${cls ? ` ${cls}` : ''}">${v}</span>
        </div>`).join('');
}

async function loadDeviceFullAnalysis(deviceId) {
    const section = document.getElementById('deepAnalysisSection');
    if (section) {
        section.removeAttribute('hidden');
        document.getElementById('deepAnalysisTitle').textContent = deviceId;
    }
    const panels = ['alert-mix', 'fleet-comparison', 'alert-severity', 'daily-alert-trend', 'activity-calendar', 'gps-quality'];
    setPanelLoading(panels, true);
    try {
        const data = await fetchJson(`/api/devices/${encodeURIComponent(deviceId)}/full-analysis?days=30`);
        renderScoreStats(data.score_stats || {});
        renderAlertMixChart(data.alert_totals || {});
        renderAlertSeverityChart(data.alert_totals || {});
        renderDailyAlertTrendChart(data.daily_history || []);
        renderFleetCompareChart(data.fleet_comparison || {});
        renderActivityCalendar(data.activity_calendar || []);
        renderGpsQuality(data.gps_quality || {});
    } finally {
        setPanelLoading(panels, false);
    }
}

async function loadDeviceDetails(deviceId) {
    state.selectedDevice = deviceId;
    const panels = ['device-summary', 'device-score-history', 'device-trip-candidates', 'device-map', 'device-recent-positions'];
    setPanelLoading(panels, true);
    try {
        const [summary, scores, trips, positions] = await Promise.all([
            fetchJson(`/api/devices/${encodeURIComponent(deviceId)}`),
            fetchJson(`/api/devices/${encodeURIComponent(deviceId)}/scores?days=30`),
            fetchJson(`/api/devices/${encodeURIComponent(deviceId)}/trip-candidates?limit=25`),
            fetchJson(`/api/devices/${encodeURIComponent(deviceId)}/positions?limit=40`),
        ]);

        renderDeviceSummary(summary);
        renderDeviceScoreChart(scores);
        renderTripCandidates(trips);
        renderDeviceMap(positions, deviceId);
        renderRecentPositions(positions);
    } finally {
        setPanelLoading(panels, false);
    }
    // Fire deep analysis in parallel (non-blocking for the main panels above)
    loadDeviceFullAnalysis(deviceId);
}

function registerEvents() {
    document.getElementById('refreshButton').addEventListener('click', loadOverview);
    document.getElementById('searchButton').addEventListener('click', searchDevices);
    document.getElementById('deviceSearch').addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            searchDevices();
        }
    });
    document.getElementById('trendDays').addEventListener('change', async () => {
        setPanelLoading(['trend-chart'], true, 'Refreshing trend view…');
        try {
            const trend = await fetchJson(`/api/trends/daily?days=${document.getElementById('trendDays').value}`);
            renderTrendChart(trend);
        } finally {
            setPanelLoading(['trend-chart'], false);
        }
    });
}

async function bootstrap() {
    initializePanelLoaders();
    registerEvents();
    try {
        await loadOverview();
    } catch (error) {
        document.getElementById('healthStatus').innerHTML = `<span class="bad">Error</span> · ${error.message}`;
    }
}

bootstrap();
