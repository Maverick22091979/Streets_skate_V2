const grid = document.getElementById('routesGrid');
const summaryGrid = document.getElementById('summaryGrid');
const statusText = document.getElementById('statusText');
const reloadBtn = document.getElementById('reloadRoutes');
const importBtn = document.getElementById('importRoutes');
const provider = importBtn?.dataset?.provider || '';
const isReadOnly = (importBtn?.dataset?.readOnly || 'false') === 'true';
const importAllowed = (importBtn?.dataset?.importAllowed || 'true') === 'true';
const importAutostart = (importBtn?.dataset?.importAutostart || 'false') === 'true';
const resetFiltersBtn = document.getElementById('resetFilters');
const weightsGrid = document.getElementById('weightsGrid');
const weightsTotal = document.getElementById('weightsTotal');
const saveWeightsBtn = document.getElementById('saveWeights');
const saveDuplicateSettingsBtn = document.getElementById('saveDuplicateSettings');
const saveSlopeSettingsBtn = document.getElementById('saveSlopeSettings');
const saveProviderButtonsBtn = document.getElementById('saveProviderButtons');
const gpxUploadForm = document.getElementById('gpxUploadForm');
const gpxFiles = document.getElementById('gpxFiles');
const uploadGpxBtn = document.getElementById('uploadGpx');
const gpxUploadState = document.getElementById('gpxUploadState');
const gpxProgress = document.getElementById('gpxProgress');
const gpxProgressLabel = document.getElementById('gpxProgressLabel');
const gpxProgressValue = document.getElementById('gpxProgressValue');
const gpxProgressFill = document.getElementById('gpxProgressFill');
const routesProgress = document.getElementById('routesProgress');
const routesProgressLabel = document.getElementById('routesProgressLabel');
const routesProgressValue = document.getElementById('routesProgressValue');
const routesProgressFill = document.getElementById('routesProgressFill');
const userFilter = document.getElementById('userFilter');
const dupDistanceDiffPct = document.getElementById('dupDistanceDiffPct');
const dupEndpointToleranceM = document.getElementById('dupEndpointToleranceM');
const dupAllowReverseMatch = document.getElementById('dupAllowReverseMatch');
const dupDistanceDiffPctValue = document.getElementById('dupDistanceDiffPctValue');
const dupEndpointToleranceMValue = document.getElementById('dupEndpointToleranceMValue');
const dupAllowReverseMatchValue = document.getElementById('dupAllowReverseMatchValue');
const slopeSmoothingWindow = document.getElementById('slopeSmoothingWindow');
const slopeMinRunDistanceM = document.getElementById('slopeMinRunDistanceM');
const slopeMaxCapPct = document.getElementById('slopeMaxCapPct');
const slopeSmoothingWindowValue = document.getElementById('slopeSmoothingWindowValue');
const slopeMinRunDistanceMValue = document.getElementById('slopeMinRunDistanceMValue');
const slopeMaxCapPctValue = document.getElementById('slopeMaxCapPctValue');
const refreshAuditLogsBtn = document.getElementById('refreshAuditLogs');
const auditLogsBody = document.getElementById('auditLogsBody');
const providerButtonsBody = document.getElementById('providerButtonsBody');
const initialRoutesData = document.getElementById('initialRoutesData');
const initialUsersData = document.getElementById('initialUsersData');
const importConsentModal = document.getElementById('importConsentModal');
const importFooterCounter = document.getElementById('importFooterCounter');
const importFooterLabel = document.getElementById('importFooterLabel');
const importFooterValue = document.getElementById('importFooterValue');
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
const isAdmin = provider === 'admin' || Boolean(saveWeightsBtn || saveDuplicateSettingsBtn || saveSlopeSettingsBtn);
let allRoutes = [];
let importPulse = null;
let importFooterTimer = null;
const weightLabels = {
  max_slope: 'Pendenza max',
  weighted_slope: 'Pendenza media ponderata',
  surface: 'Pavimentazione',
  smoothness: 'Smoothness',
  pressure: 'Pressione atmosferica',
  temperature: 'Temperatura',
  segment_speed: 'Velocita max sul tratto',
};

const declaredDifficultyColors = {
  Easy: '#16a34a',
  EasyLong: '#facc15',
  Advanced: '#dc2626',
  Pro: '#111111',
  Unknown: '#98a2b3',
};

const declaredDifficultyClasses = {
  Easy: 'declared-easy',
  EasyLong: 'declared-easylong',
  Advanced: 'declared-advanced',
  Pro: 'declared-pro',
  Unknown: 'declared-unknown',
};

const slopeColorClasses = {
  '#22c55e': 'slope-color-flat',
  '#facc15': 'slope-color-moderate',
  '#fb923c': 'slope-color-steep',
  '#ef4444': 'slope-color-extreme',
  '#cbd5e1': 'slope-color-na',
};

function parseInitialJson(el, fallback) {
  if (!el?.textContent) return fallback;
  try {
    return JSON.parse(el.textContent);
  } catch {
    return fallback;
  }
}

function badgeClass(score) {
  if (score >= 70) return 'hard';
  if (score >= 35) return 'medium';
  return 'easy';
}

function fmt(value, suffix = '') {
  if (value === null || value === undefined || value === '') return 'n.d.';
  return `${value}${suffix}`;
}

function csrfHeaders(extra = {}) {
  return csrfToken ? { ...extra, 'X-CSRF-Token': csrfToken } : extra;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function safeAttr(value) {
  return escapeHtml(value);
}

function safeNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function safeColor(value, fallback = '#98a2b3') {
  const raw = String(value || '').trim();
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(raw) ? raw : fallback;
}

function safeText(value, suffix = '') {
  return escapeHtml(fmt(value, suffix));
}

function renderSummary(routes) {
  if (!summaryGrid) return;
  const total = routes.length;
  const avgScore = total ? Math.round(routes.reduce((acc, r) => acc + (r.difficulty?.score || 0), 0) / total) : 0;
  const totalKm = routes.reduce((acc, r) => acc + (r.distance_km || 0), 0).toFixed(1);
  const hard = routes.filter(r => (r.difficulty?.score || 0) >= 70).length;
  const lastImport = routes
    .map(r => r.imported_at)
    .filter(Boolean)
    .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];
  const lastImportLabel = lastImport
    ? new Date(lastImport).toLocaleString('it-IT', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : 'n.d.';
  summaryGrid.innerHTML = `
    <div><span>Percorsi</span><strong>${total}</strong></div>
    <div><span>Distanza totale</span><strong>${totalKm} km</strong></div>
    <div><span>Difficoltà media</span><strong>${avgScore}/100</strong></div>
    <div><span>Percorsi difficili</span><strong>${hard}</strong></div>
    <div><span>Ultima importazione</span><strong>${lastImportLabel}</strong></div>
  `;
}

function renderGlobalStats(routes) {
  const box = document.getElementById('globalStats');
  if (!box) return;
  box.innerHTML = '';
}

function metricValue(route, key) {
  let raw = route[key];
  if (key === 'difficulty_score') raw = route.difficulty?.score;
  if (key === 'max_slope_pct') raw = route.difficulty?.factors?.max_slope_pct;
  if (key === 'weighted_avg_slope_pct') raw = route.difficulty?.factors?.weighted_avg_slope_pct;
  if (key === 'segment_max_speed_kmh') raw = route.difficulty?.factors?.segment_max_speed_kmh;
  if (key === 'import_user_id') raw = route.import_user_id;
  if (key === 'has_cobblestone') return String(Boolean(route.enrichment?.has_cobblestone));
  if (key === 'declared_difficulty_level') return route.difficulty?.declared_difficulty?.level || '';
  const val = Number(raw);
  return Number.isFinite(val) ? val : 0;
}

function stepDecimals(step) {
  const s = String(step || '1');
  const idx = s.indexOf('.');
  return idx >= 0 ? s.length - idx - 1 : 0;
}

function formatRangeValue(val, step) {
  if (!Number.isFinite(val)) return 'n.d.';
  const d = stepDecimals(step);
  return d > 0 ? val.toFixed(d) : String(Math.round(val));
}

function updateFilterRanges(routes = allRoutes) {
  document.querySelectorAll('.filter-input').forEach(el => {
    const key = el.dataset.key;
    if (key === 'import_user_id' || key === 'declared_difficulty_level') return;
    const vals = routes.map(route => metricValue(route, key)).filter(Number.isFinite);
    const rangeEl = el.parentElement?.querySelector('.filter-range');
    if (!vals.length) {
      el.min = '';
      el.max = '';
      if (rangeEl) rangeEl.textContent = 'Range: n.d.';
      return;
    }
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    el.min = String(min);
    el.max = String(max);
    if (el.value !== '') {
      const cur = Number(el.value);
      if (Number.isFinite(cur)) {
        if (cur < min) el.value = String(min);
        if (cur > max) el.value = String(max);
      }
    }
    if (rangeEl) {
      rangeEl.textContent = `Range: ${formatRangeValue(min, el.step)} - ${formatRangeValue(max, el.step)}`;
    }
  });
}

function readFilters() {
  const out = {};
  document.querySelectorAll('.filter-input').forEach(el => {
    const key = el.dataset.key;
    const raw = el.value.trim();
    if (!out[key]) out[key] = {};
    if (key === 'import_user_id' || key === 'declared_difficulty_level' || key === 'has_cobblestone') {
      out[key].eq = raw === '' ? null : raw;
    } else {
      out[key].max = raw === '' ? null : Number(raw);
    }
  });
  return out;
}

function applyFilters(routes = allRoutes) {
  const filters = readFilters();
  return routes.filter(route => Object.entries(filters).every(([key, bounds]) => {
    if (key === 'import_user_id' || key === 'declared_difficulty_level' || key === 'has_cobblestone') {
      if (bounds.eq === null) return true;
      const current = key === 'import_user_id'
        ? String(route.import_user_id || '')
        : key === 'declared_difficulty_level'
          ? String(route.difficulty?.declared_difficulty?.level || '')
          : String(Boolean(route.enrichment?.has_cobblestone));
      return current === String(bounds.eq);
    }
    const val = metricValue(route, key);
    if (bounds.max !== null && val > bounds.max) return false;
    return true;
  }));
}

function renderRoutes(routes) {
  if (!grid) return routes;
  updateFilterRanges(allRoutes);
  const filtered = applyFilters(routes);
  renderSummary(filtered);
  renderGlobalStats(filtered);
  updateRoutesProgress(filtered.length, allRoutes.length);
  grid.innerHTML = filtered.map(renderRoute).join('');
  bindRouteCards();
  bindDeleteButtons();
  return filtered;
}

function updateRoutesProgress(found, total) {
  if (!routesProgress || !routesProgressLabel || !routesProgressValue || !routesProgressFill) return;
  const safeFound = Math.max(0, Number(found || 0));
  const safeTotal = Math.max(0, Number(total || 0));
  const pct = safeTotal > 0 ? Math.round((safeFound / safeTotal) * 100) : 0;
  routesProgressLabel.textContent = safeTotal > 0 ? 'Percorsi trovati' : 'Percorsi trovati in cache';
  routesProgressValue.textContent = `${safeFound} / ${safeTotal}`;
  routesProgressFill.max = 100;
  routesProgressFill.value = pct;
}

function setImportProgress(done, total, label = 'Import in corso') {
  if (!routesProgress || !routesProgressLabel || !routesProgressValue || !routesProgressFill) return;
  routesProgress.classList.remove('loading');
  const safeDone = Math.max(0, Number(done || 0));
  const safeTotal = Math.max(0, Number(total || 0));
  const pct = safeTotal > 0 ? Math.round((safeDone / safeTotal) * 100) : 0;
  routesProgressLabel.textContent = label;
  routesProgressValue.textContent = `${safeDone} / ${safeTotal}`;
  routesProgressFill.max = 100;
  routesProgressFill.value = pct;
}

function startImportPulse(label = 'Import in corso') {
  if (!routesProgress || !routesProgressLabel || !routesProgressValue || !routesProgressFill) return;
  stopImportPulse();
  routesProgress.classList.add('loading');
  routesProgressLabel.textContent = label;
  routesProgressValue.textContent = 'lettura dati...';
  routesProgressFill.max = 100;
  routesProgressFill.removeAttribute('value');
  let dots = 0;
  importPulse = window.setInterval(() => {
    dots = (dots + 1) % 4;
    routesProgressValue.textContent = `lettura dati${'.'.repeat(dots)}`;
  }, 350);
}

function stopImportPulse() {
  if (importPulse) {
    window.clearInterval(importPulse);
    importPulse = null;
  }
  routesProgress?.classList.remove('loading');
  if (routesProgressFill && !routesProgressFill.hasAttribute('value')) {
    routesProgressFill.value = 0;
  }
}

function setImportFooter(imported, total, label = 'Import percorsi') {
  if (!importFooterCounter || !importFooterLabel || !importFooterValue) return;
  if (importFooterTimer) {
    window.clearTimeout(importFooterTimer);
    importFooterTimer = null;
  }
  importFooterCounter.classList.remove('hidden');
  importFooterLabel.textContent = label;
  importFooterValue.textContent = `[${Math.max(0, Number(imported || 0))} / ${Math.max(0, Number(total || 0))}]`;
}

function hideImportFooter(delayMs = 0) {
  if (!importFooterCounter) return;
  if (importFooterTimer) {
    window.clearTimeout(importFooterTimer);
    importFooterTimer = null;
  }
  if (delayMs > 0) {
    importFooterTimer = window.setTimeout(() => {
      importFooterCounter.classList.add('hidden');
      importFooterTimer = null;
    }, delayMs);
    return;
  }
  importFooterCounter.classList.add('hidden');
}

async function pollImportJob(jobId) {
  while (true) {
    const data = await importJobStatus(jobId, () => {
      statusText.textContent = 'Connessione rallentata: riconnessione allo stesso import Strava in corso...';
    }, '/api/import-jobs/');
    stopImportPulse();
    const cur = data.current ? ` · ${data.current}` : '';
    setImportProgress(data.done || 0, data.total || 0, `Import ${provider}`);
    setImportFooter(data.imported || 0, data.total || 0, `Import ${provider}`);
    statusText.textContent = `Import da ${provider} in corso... ${(data.done || 0)}/${data.total || 0}${cur}`;
    if (data.status === 'done') return data.result;
    if (data.status === 'error') throw new Error(data.error || 'Errore import');
    await new Promise((r) => setTimeout(r, 2000));
  }
}

function onFiltersChanged() {
  const filtered = renderRoutes(allRoutes);
  statusText.textContent = filtered.length ? `${filtered.length} percorsi filtrati` : 'Nessun percorso corrisponde ai filtri.';
}

function bindFilters() {
  document.querySelectorAll('.filter-input').forEach(el => {
    el.addEventListener('input', onFiltersChanged);
    el.addEventListener('change', onFiltersChanged);
  });
}

function resetFilters() {
  document.querySelectorAll('.filter-input').forEach(el => {
    el.value = '';
  });
  const filtered = renderRoutes(allRoutes);
  statusText.textContent = filtered.length ? `${filtered.length} percorsi in cache locale` : 'Nessun percorso in cache.';
}

function shortSkipMessage(count, names) {
  if (!count) return '';
  const list = (names || []).slice(0, 3).join(', ');
  return list ? `, distanza<500m=${count} (${list})` : `, distanza<500m=${count}`;
}

function wrongTypeSkipMessage(count, names) {
  if (!count) return '';
  const list = (names || []).slice(0, 3).join(', ');
  return list ? `, tipo_attivita_non_valido=${count} (${list})` : `, tipo_attivita_non_valido=${count}`;
}

function renderRoute(route) {
  const d = route.difficulty || {};
  const f = d.factors || {};
  const dd = d.declared_difficulty || null;
  const trackQuality = route.enrichment?.track_quality || {};
  const trackAlignment = route.enrichment?.track_alignment || {};
  const routeId = safeNum(route.id, 0);
  const score = safeNum(d.score, 0);
  const scoreLabel = escapeHtml(d.label || '');
  const title = escapeHtml(route.name || 'Percorso senza nome');
  const type = escapeHtml(route.type || 'activity');
  const startDate = escapeHtml(route.start_date_local || '');
  const declaredLevel = escapeHtml(dd?.level || 'n.d.');
  const declaredSource = escapeHtml(dd?.source || 'n.d.');
  const declaredTitle = dd ? `Difficoltà dichiarata: ${declaredLevel}` : 'Difficoltà dichiarata: n.d.';
  const declaredClass = declaredDifficultyClasses[dd?.level] || declaredDifficultyClasses.Unknown;
  const href = `/routes/${routeId}`;
  return `
    <article class="route-card route-card-compact" data-route-href="${href}" tabindex="0" role="link" aria-label="Apri dettagli di ${safeAttr(route.name || 'percorso')}">
      <div class="route-top">
        <div class="score ${badgeClass(score)}">
          <strong>${score}</strong>
          <span>${scoreLabel}</span>
        </div>
        <div>
          <h2 class="route-title">
            <span class="route-declared-dot ${declaredClass}" title="${safeAttr(declaredTitle)}"></span>
            <span class="route-link">${title}</span>
          </h2>
          <p>${type} · ${startDate}</p>
        </div>
        ${isAdmin ? `<div class="route-actions"><button class="route-delete" type="button" data-route-id="${routeId}">Cancella</button></div>` : ''}
      </div>

      <div class="route-compact-metrics">
        <div><span>Distanza</span><strong>${safeText(route.distance_km, ' km')}</strong></div>
        <div><span>Dislivello</span><strong>${safeText(route.elevation_gain_m, ' m')}</strong></div>
        <div><span>Pendenza max</span><strong>${safeText(f.max_slope_pct, '%')}</strong></div>
        <div><span>Velocità media</span><strong>${safeText(route.average_speed_kmh, ' km/h')}</strong></div>
      </div>
      <div class="route-compact-meta">
        <span>${safeText(route.import_user_label)}</span>
        <span>${safeText(route.source_label)}</span>
        <span class="${trackQuality.status === 'warning' || trackAlignment.status === 'warning' ? 'route-alert' : ''}">${trackQuality.status === 'warning' || trackAlignment.status === 'warning' ? 'Verifica traccia' : 'Traccia verificata'}</span>
        <strong>Apri dettagli</strong>
      </div>
    </article>
  `;
}

function renderUserFilter(users = []) {
  if (!userFilter) return;
  const cur = userFilter.value;
  userFilter.innerHTML = `<option value="">Tutti</option>${users.map((u) => `<option value="${safeAttr(safeNum(u.id, 0))}">${escapeHtml(u.label || '')}</option>`).join('')}`;
  userFilter.value = users.some((u) => String(u.id) === String(cur)) ? cur : '';
}

function setGpxProgress(done, total, label = 'Import GPX') {
  if (!gpxProgress || !gpxProgressValue || !gpxProgressFill || !gpxProgressLabel) return;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  if (total > 0) gpxProgress.classList.remove('hidden');
  gpxProgressLabel.textContent = label;
  gpxProgressValue.textContent = `${pct}%`;
  gpxProgressFill.max = 100;
  gpxProgressFill.value = pct;
}

function resetGpxProgress() {
  if (!gpxProgress) return;
  setGpxProgress(0, 0);
  gpxProgress.classList.add('hidden');
}

function bindDeleteButtons() {
  document.querySelectorAll('.route-delete').forEach(btn => {
    btn.addEventListener('click', async (event) => {
      event.stopPropagation();
      const id = Number(btn.dataset.routeId);
      if (!id) return;
      btn.disabled = true;
      statusText.textContent = `Cancellazione percorso ${id} in corso...`;
      try {
        const response = await fetch(`/api/routes/${id}`, { method: 'DELETE', headers: csrfHeaders() });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Errore cancellazione');
        renderUserFilter(data.users || []);
        allRoutes = data.routes || [];
        const filtered = renderRoutes(allRoutes);
        statusText.textContent = filtered.length ? `Percorso eliminato. Totale cache: ${data.total_cached}` : 'Nessun percorso in cache.';
      } catch (err) {
        statusText.textContent = `Errore: ${err.message}`;
        btn.disabled = false;
      }
    });
  });
}

function bindRouteCards() {
  document.querySelectorAll('.route-card[data-route-href]').forEach(card => {
    const open = () => { window.location.assign(card.dataset.routeHref); };
    card.addEventListener('click', (event) => {
      if (!event.target.closest('button, a, input, select, textarea')) open();
    });
    card.addEventListener('keydown', (event) => {
      if (event.target !== card) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    });
  });
}

function readWeights() {
  const out = {};
  document.querySelectorAll('.weight-slider').forEach(el => {
    out[el.dataset.key] = Number(el.value);
  });
  return out;
}

function totalWeights(next = null) {
  const vals = Object.values(next || readWeights());
  return vals.reduce((a, b) => a + b, 0);
}

function updateWeightsTotal() {
  if (!weightsTotal) return;
  const total = totalWeights();
  weightsTotal.textContent = total;
  weightsTotal.className = total === 100 ? 'ok' : 'bad';
}

function renderWeights(weights) {
  if (!weightsGrid) return;
  weightsGrid.innerHTML = Object.entries(weights).map(([k, v]) => `
    <label class="weight-card">
      <span>${weightLabels[k] || k}</span>
      <input class="weight-slider" type="range" min="0" max="100" step="1" value="${v}" data-key="${k}">
      <strong class="weight-value" data-key="${k}">${v}</strong>
    </label>
  `).join('');
  weightsGrid.querySelectorAll('.weight-slider').forEach(el => {
    el.dataset.prev = String(el.value);
    el.addEventListener('input', e => {
      const prev = Number(e.target.dataset.prev || e.target.value);
      const next = Number(e.target.value);
      const draft = readWeights();
      draft[e.target.dataset.key] = next;
      if (totalWeights(draft) > 100) {
        e.target.value = String(prev);
        return;
      }
      const key = e.target.dataset.key;
      const val = e.target.value;
      const out = weightsGrid.querySelector(`.weight-value[data-key="${key}"]`);
      if (out) out.textContent = val;
      e.target.dataset.prev = String(val);
      updateWeightsTotal();
    });
  });
  updateWeightsTotal();
}

function renderDuplicateSettings(s) {
  if (!dupDistanceDiffPct || !dupEndpointToleranceM || !dupAllowReverseMatch) return;
  dupDistanceDiffPct.value = Number(s.distance_diff_pct ?? 5);
  dupEndpointToleranceM.value = Number(s.endpoint_tolerance_m ?? 300);
  dupAllowReverseMatch.checked = Boolean(s.allow_reverse_match);
  dupDistanceDiffPctValue.textContent = String(dupDistanceDiffPct.value);
  dupEndpointToleranceMValue.textContent = String(dupEndpointToleranceM.value);
  dupAllowReverseMatchValue.textContent = dupAllowReverseMatch.checked ? 'Attivo' : 'Disattivo';
}

function readDuplicateSettings() {
  return {
    distance_diff_pct: Number(dupDistanceDiffPct?.value || 0),
    endpoint_tolerance_m: Number(dupEndpointToleranceM?.value || 0),
    allow_reverse_match: Boolean(dupAllowReverseMatch?.checked),
  };
}

function renderSlopeSettings(s) {
  if (!slopeSmoothingWindow || !slopeMinRunDistanceM || !slopeMaxCapPct) return;
  slopeSmoothingWindow.value = Number(s.smoothing_window ?? 5);
  slopeMinRunDistanceM.value = Number(s.min_run_distance_m ?? 20);
  slopeMaxCapPct.value = Number(s.max_cap_pct ?? 35);
  slopeSmoothingWindowValue.textContent = String(slopeSmoothingWindow.value);
  slopeMinRunDistanceMValue.textContent = String(slopeMinRunDistanceM.value);
  slopeMaxCapPctValue.textContent = String(slopeMaxCapPct.value);
}

function readSlopeSettings() {
  return {
    smoothing_window: Number(slopeSmoothingWindow?.value || 0),
    min_run_distance_m: Number(slopeMinRunDistanceM?.value || 0),
    max_cap_pct: Number(slopeMaxCapPct?.value || 0),
  };
}

function fmtAuditText(v) {
  if (v === null || v === undefined || v === '') return 'n.d.';
  return String(v);
}

function renderAuditLogs(logs) {
  if (!auditLogsBody) return;
  if (!logs.length) {
    auditLogsBody.innerHTML = '<tr><td class="audit-empty" colspan="4">Nessun log disponibile</td></tr>';
    return;
  }
  auditLogsBody.innerHTML = logs.map((row) => `
    <tr>
      <td>${escapeHtml(fmtAuditText(row.occurred_at_label))}</td>
      <td>${escapeHtml(fmtAuditText(row.action))}</td>
      <td>${escapeHtml(fmtAuditText(row.actor_label))}</td>
      <td>${escapeHtml(fmtAuditText(row.error))}</td>
    </tr>
  `).join('');
}

function renderProviderButtonSettings(providers) {
  if (!providerButtonsBody) return;
  const entries = Object.entries(providers || {});
  if (!entries.length) {
    providerButtonsBody.innerHTML = '<tr><td colspan="4">Nessun provider disponibile</td></tr>';
    return;
  }
  providerButtonsBody.innerHTML = entries.map(([key, meta]) => `
    <tr>
      <td>
        <strong>${escapeHtml(meta.label || key)}</strong>
        <div class="provider-admin-note">${escapeHtml(key)}</div>
      </td>
      <td>${escapeHtml(meta.implementation?.settings || 'Unknown')}</td>
      <td><input class="provider-flag" type="checkbox" data-provider="${safeAttr(key)}" data-flag="visible" ${meta.button_visible ? 'checked' : ''}></td>
      <td><input class="provider-flag" type="checkbox" data-provider="${safeAttr(key)}" data-flag="enabled" ${meta.button_enabled ? 'checked' : ''}></td>
    </tr>
  `).join('');
}

function readProviderButtonSettings() {
  const out = {};
  document.querySelectorAll('.provider-flag').forEach((el) => {
    const key = el.dataset.provider || '';
    const flag = el.dataset.flag || '';
    if (!key || !flag) return;
    if (!out[key]) out[key] = { visible: false, enabled: false };
    out[key][flag] = Boolean(el.checked);
  });
  return out;
}

async function loadAuditLogs() {
  if (!auditLogsBody || !isAdmin) return;
  try {
    const response = await fetch('/api/admin/audit-logs?limit=100');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Errore audit log');
    renderAuditLogs(data.logs || []);
  } catch (err) {
    auditLogsBody.innerHTML = `<tr><td colspan="4">Errore caricamento log: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function loadStatus() {
  if (!isAdmin) return;
  try {
    const response = await fetch('/api/status');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Errore status');
    renderWeights(data.difficulty_weights || {});
    renderDuplicateSettings(data.duplicate_settings || {});
    renderSlopeSettings(data.slope_settings || {});
    renderProviderButtonSettings(data.providers || {});
    await loadAuditLogs();
  } catch (err) {
    statusText.textContent = `Errore: ${err.message}`;
  }
}

async function saveWeights() {
  const payload = readWeights();
  const total = Object.values(payload).reduce((a, b) => a + b, 0);
  if (total !== 100) {
    statusText.textContent = 'Errore: la somma dei pesi deve essere 100';
    return;
  }
  saveWeightsBtn.disabled = true;
  statusText.textContent = 'Salvataggio pesi in corso...';
  try {
    const response = await fetch('/api/admin/difficulty-weights', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Errore salvataggio');
    renderWeights(data.difficulty_weights || payload);
    statusText.textContent = 'Pesi aggiornati';
  } catch (err) {
    statusText.textContent = `Errore: ${err.message}`;
  } finally {
    saveWeightsBtn.disabled = false;
  }
}

async function saveDuplicateSettings() {
  const payload = readDuplicateSettings();
  saveDuplicateSettingsBtn.disabled = true;
  statusText.textContent = 'Salvataggio controlli duplicati in corso...';
  try {
    const response = await fetch('/api/admin/duplicate-settings', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Errore salvataggio');
    renderDuplicateSettings(data.duplicate_settings || payload);
    statusText.textContent = 'Controlli duplicati aggiornati';
  } catch (err) {
    statusText.textContent = `Errore: ${err.message}`;
  } finally {
    saveDuplicateSettingsBtn.disabled = false;
  }
}

async function saveSlopeSettings() {
  const payload = readSlopeSettings();
  saveSlopeSettingsBtn.disabled = true;
  statusText.textContent = 'Salvataggio pendenze in corso...';
  try {
    const response = await fetch('/api/admin/slope-settings', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Errore salvataggio');
    renderSlopeSettings(data.slope_settings || payload);
    statusText.textContent = 'Pendenze aggiornate';
  } catch (err) {
    statusText.textContent = `Errore: ${err.message}`;
  } finally {
    saveSlopeSettingsBtn.disabled = false;
  }
}

async function saveProviderButtonSettings() {
  const payload = readProviderButtonSettings();
  saveProviderButtonsBtn.disabled = true;
  statusText.textContent = 'Salvataggio pulsanti provider in corso...';
  try {
    const response = await fetch('/api/admin/provider-button-settings', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Errore salvataggio');
    renderProviderButtonSettings(data.providers || {});
    statusText.textContent = 'Pulsanti provider aggiornati';
  } catch (err) {
    statusText.textContent = `Errore: ${err.message}`;
  } finally {
    saveProviderButtonsBtn.disabled = false;
  }
}

async function readImportResponse(response) {
  const raw = await response.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    const reasons = {
      413: 'File troppo grandi per il proxy: verifica client_max_body_size in nginx.',
      502: 'Il proxy non riesce a raggiungere il servizio web.',
      503: 'Il servizio web non è disponibile.',
      504: 'Timeout del proxy durante l’import.',
      522: 'Timeout di connessione al server di produzione.',
      524: 'Timeout Cloudflare durante l’import.',
    };
    if (response.redirected) throw new Error('Sessione scaduta: accedi nuovamente e riprova.');
    throw new Error(`HTTP ${response.status}: ${reasons[response.status] || 'Il server ha restituito una pagina HTML o una risposta non valida. Controlla i log di nginx e del servizio web.'}`);
  }
  if (!response.ok) {
    const detail = data.detail || data.error || 'Errore import GPX';
    throw new Error(`HTTP ${response.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
  }
  return data;
}

function gpxBatches(groups, maxCount = 5, maxBytes = 4 * 1024 * 1024) {
  const batches = [];
  let batch = [];
  let size = 0;
  for (const group of groups) {
    const bytes = group.reduce((sum, file) => sum + file.size, 0);
    if (batch.length && (batch.length >= maxCount || size + bytes > maxBytes)) {
      batches.push(batch);
      batch = [];
      size = 0;
    }
    batch.push(group);
    size += bytes;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

async function importJobStatus(id, onRetry = () => {}, path = '/api/manual-import/jobs/') {
  for (let attempt = 0; ; attempt += 1) {
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 30000);
    try {
      const response = await fetch(`${path}${encodeURIComponent(id)}`, { cache: 'no-store', signal: ctrl.signal });
      if ([408, 429, 502, 503, 504, 522, 524].includes(response.status)) {
        throw Object.assign(new Error(`HTTP ${response.status}: stato import temporaneamente non disponibile`), { retryable: true });
      }
      return await readImportResponse(response);
    } catch (err) {
      if (!(ctrl.signal.aborted || err.retryable || err.name === 'TypeError' || err.name === 'AbortError')) throw err;
      window.clearTimeout(timer);
      onRetry(attempt + 1);
      await new Promise((resolve) => window.setTimeout(resolve, Math.min(15000, 2000 * (attempt + 1))));
    } finally {
      window.clearTimeout(timer);
    }
  }
}

async function uploadGpx(ev) {
  if (ev) ev.preventDefault();
  if (uploadGpxBtn.disabled) return;
  const files = [...(gpxFiles?.files || [])];
  if (!files.length) {
    statusText.textContent = 'Errore: seleziona almeno un file GPX o JSON';
    if (gpxUploadState) gpxUploadState.textContent = 'Seleziona almeno un file GPX o JSON';
    return;
  }
  const bundles = new Map();
  files.forEach((file) => {
    const match = /^(.*)\.(gpx|json)$/i.exec(file.name || '');
    if (!match) return;
    const item = bundles.get(match[1]) || [];
    item.push(file);
    bundles.set(match[1], item);
  });
  const groups = [...bundles.values()].filter((group) => group.some((file) => /\.gpx$/i.test(file.name)));
  if (!groups.length) {
    const message = 'Nessun file GPX selezionato. Per l’export adidas apri Sport-sessions/GPS-data e seleziona i .gpx, con i .json omonimi opzionali. I JSON nella cartella Sport-sessions sono i dati descrittivi delle attività.';
    statusText.textContent = `Errore: ${message}`;
    if (gpxUploadState) gpxUploadState.textContent = message;
    return;
  }
  const large = groups.find((group) => group.reduce((sum, file) => sum + file.size, 0) > 20 * 1024 * 1024);
  if (large) {
    const message = `Il percorso ${large[0].name} con i relativi file supera 20 MB. Carica un GPX più piccolo oppure seleziona il solo GPX senza il JSON opzionale.`;
    statusText.textContent = message;
    if (gpxUploadState) gpxUploadState.textContent = message;
    return;
  }
  const end = window.sessionGuard?.beginTask() || (() => {});
  uploadGpxBtn.disabled = true;
  let totalCached = 0;
  let imported = 0;
  let skippedFilename = 0;
  let skippedShort = 0;
  let skippedWrongType = 0;
  let skippedShortNames = [];
  let skippedWrongTypeNames = [];
  let processedBundles = 0;
  const batches = gpxBatches(groups);
  statusText.textContent = `Import in corso: 0/${groups.length} percorsi`;
  if (gpxUploadState) gpxUploadState.textContent = `Preparazione di ${groups.length} bundle GPX/JSON...`;
  setGpxProgress(0, groups.length, `0 / ${groups.length} percorsi`);
  try {
    for (const batch of batches) {
      const fd = new FormData();
      fd.append('csrf_token', csrfToken);
      batch.flat().forEach((file) => fd.append('files', file));
      statusText.textContent = `Import in corso: ${processedBundles}/${groups.length} percorsi`;
      if (gpxUploadState) gpxUploadState.textContent = `Elaborazione percorsi ${processedBundles + 1}-${processedBundles + batch.length} di ${groups.length}`;
      const response = await fetch('/api/manual-import/gpx/start', { method: 'POST', headers: csrfHeaders(), body: fd });
      const start = await readImportResponse(response);
      if (!start.job_id) throw new Error('Il server non ha restituito un identificativo di import valido.');
      let job;
      do {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        job = await importJobStatus(start.job_id, () => {
          const message = `Connessione rallentata: riconnessione allo stesso import in corso (${processedBundles}/${groups.length} percorsi completati). Non ricaricare i file.`;
          statusText.textContent = message;
          if (gpxUploadState) gpxUploadState.textContent = message;
        });
        const done = processedBundles + Math.min(batch.length, Number(job.done || 0));
        statusText.textContent = `Import in corso: ${done}/${groups.length} percorsi`;
        setGpxProgress(done, groups.length, `${done} / ${groups.length} percorsi`);
        if (gpxUploadState && job.status === 'running') {
          gpxUploadState.textContent = job.phase === 'queued'
            ? `Blocco in attesa di elaborazione: ${done}/${groups.length} percorsi`
            : `Elaborazione ${done}/${groups.length}: ${job.current || 'salvataggio in corso'}`;
        }
      } while (job.status === 'running');
      if (job.status !== 'done' || !job.result) throw new Error(job.error || 'Import non completato: verifica i percorsi già salvati.');
      const data = job.result;
      imported += Number(data.imported || 0);
      skippedFilename += Number(data.skipped_filename || 0);
      skippedShort += Number(data.skipped_short || 0);
      skippedWrongType += Number(data.skipped_wrong_type || 0);
      skippedShortNames = skippedShortNames.concat(data.skipped_short_names || []);
      skippedWrongTypeNames = skippedWrongTypeNames.concat(data.skipped_wrong_type_names || []);
      totalCached = Number(data.total_cached || totalCached);
      processedBundles += batch.length;
      setGpxProgress(processedBundles, groups.length, `${processedBundles} / ${groups.length} percorsi`);
    }
    await loadRoutes();
    const shortMsg = shortSkipMessage(skippedShort, skippedShortNames);
    const wrongTypeMsg = wrongTypeSkipMessage(skippedWrongType, skippedWrongTypeNames);
    const bundleMsg = `${processedBundles} bundle GPX/JSON`;
    statusText.textContent = `Importati ${imported} percorsi da ${bundleMsg}. Scartati nome file=${skippedFilename}${shortMsg}${wrongTypeMsg}. Totale cache: ${totalCached}`;
    if (gpxUploadState) gpxUploadState.textContent = `Import completato: ${imported} percorsi da ${bundleMsg}, scartati nome file=${skippedFilename}${shortMsg}${wrongTypeMsg}, totale cache ${totalCached}`;
    gpxUploadForm.reset();
  } catch (err) {
    if (processedBundles > 0) await loadRoutes();
    statusText.textContent = `Errore: ${err.message}`;
    if (gpxUploadState) gpxUploadState.textContent = `Errore import GPX: ${err.message}. I percorsi già salvati restano disponibili; puoi riprovare la stessa selezione.`;
  } finally {
    end();
    uploadGpxBtn.disabled = false;
    setTimeout(() => resetGpxProgress(), 1200);
  }
}

async function loadRoutes() {
  if (!grid || !statusText) return;
  statusText.textContent = 'Caricamento cache locale...';
  grid.innerHTML = '';
  try {
    const response = await fetch('/api/routes');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || 'Errore API');
    const routes = data.routes || [];
    renderUserFilter(data.users || []);
    allRoutes = routes;
    if (provider === 'admin') {
      const filtered = renderRoutes(allRoutes);
      statusText.textContent = filtered.length ? `${filtered.length} percorsi in archivio` : 'Admin: nessun percorso disponibile.';
      importBtn.disabled = true;
      return;
    }
    if (!routes.length) {
      renderRoutes(allRoutes);
      statusText.textContent = isReadOnly ? 'Archivio globale vuoto o non ancora popolato.' : importAllowed ? 'Nessun percorso in cache. Premi “Importa”.' : 'Nessun percorso in cache. Import disabilitato per mancato consenso.';
      return;
    }
    const filtered = renderRoutes(allRoutes);
    statusText.textContent = isReadOnly ? `${filtered.length} percorsi disponibili in archivio globale` : `${filtered.length} percorsi in cache locale`;
  } catch (err) {
    statusText.textContent = `Errore: ${err.message}`;
  }
}

async function importRoutes() {
  if (!provider || provider === 'admin' || !statusText || !importAllowed) return;
  if (importBtn.disabled) return;
  const end = window.sessionGuard?.beginTask() || (() => {});
  statusText.textContent = `Import da ${provider} in corso...`;
  importBtn.disabled = true;
  startImportPulse(`Import ${provider}`);
  setImportFooter(0, 0, `Import ${provider}`);
  try {
    const start = await fetch(`/api/import/${provider}/start`, { method: 'POST', headers: csrfHeaders() });
    const startData = await readImportResponse(start);
    let data = startData.result;
    stopImportPulse();
    setImportProgress(startData.done || 0, startData.total || 0, `Import ${provider}`);
    setImportFooter(startData.imported || 0, startData.total || 0, `Import ${provider}`);
    data = await pollImportJob(startData.job_id);
    allRoutes = data.routes || [];
    renderRoutes(allRoutes);
    statusText.textContent = `Importati ${data.imported} percorsi${shortSkipMessage(data.skipped_short || 0, data.skipped_short_names || [])}. Totale cache: ${data.total_cached}`;
    if (data.warnings?.length) statusText.textContent += ` Avvisi: ${data.warnings.join(' ')}`;
    setImportFooter(data.imported || 0, data.total || 0, `Import ${provider} completato`);
    hideImportFooter(8000);
  } catch (err) {
    statusText.textContent = `Errore: ${err.message}`;
  } finally {
    end();
    stopImportPulse();
    importBtn.disabled = false;
  }
}

if (reloadBtn) reloadBtn.addEventListener('click', loadRoutes);
if (importBtn) importBtn.addEventListener('click', importRoutes);
if (saveWeightsBtn) saveWeightsBtn.addEventListener('click', saveWeights);
if (saveDuplicateSettingsBtn) saveDuplicateSettingsBtn.addEventListener('click', saveDuplicateSettings);
if (saveSlopeSettingsBtn) saveSlopeSettingsBtn.addEventListener('click', saveSlopeSettings);
if (saveProviderButtonsBtn) saveProviderButtonsBtn.addEventListener('click', saveProviderButtonSettings);
if (resetFiltersBtn) resetFiltersBtn.addEventListener('click', resetFilters);
if (gpxUploadForm) gpxUploadForm.addEventListener('submit', uploadGpx);
if (uploadGpxBtn) uploadGpxBtn.addEventListener('click', uploadGpx);
if (refreshAuditLogsBtn) refreshAuditLogsBtn.addEventListener('click', loadAuditLogs);
if (dupDistanceDiffPct) dupDistanceDiffPct.addEventListener('input', () => { dupDistanceDiffPctValue.textContent = dupDistanceDiffPct.value; });
if (dupEndpointToleranceM) dupEndpointToleranceM.addEventListener('input', () => { dupEndpointToleranceMValue.textContent = dupEndpointToleranceM.value; });
if (dupAllowReverseMatch) dupAllowReverseMatch.addEventListener('change', () => { dupAllowReverseMatchValue.textContent = dupAllowReverseMatch.checked ? 'Attivo' : 'Disattivo'; });
if (slopeSmoothingWindow) slopeSmoothingWindow.addEventListener('input', () => { slopeSmoothingWindowValue.textContent = slopeSmoothingWindow.value; });
if (slopeMinRunDistanceM) slopeMinRunDistanceM.addEventListener('input', () => { slopeMinRunDistanceMValue.textContent = slopeMinRunDistanceM.value; });
if (slopeMaxCapPct) slopeMaxCapPct.addEventListener('input', () => { slopeMaxCapPctValue.textContent = slopeMaxCapPct.value; });
bindFilters();
loadStatus();
if (grid) {
  if (importConsentModal && !importConsentModal.classList.contains('hidden')) {
    document.body.classList.add('modal-open');
  }
  const bootRoutes = parseInitialJson(initialRoutesData, []);
  const bootUsers = parseInitialJson(initialUsersData, []);
  renderUserFilter(bootUsers);
  allRoutes = Array.isArray(bootRoutes) ? bootRoutes : [];
  if (provider === 'admin') {
    const filtered = renderRoutes(allRoutes);
    statusText.textContent = filtered.length ? `${filtered.length} percorsi in archivio` : 'Admin: nessun percorso disponibile.';
    importBtn.disabled = true;
  } else if (!allRoutes.length) {
    renderRoutes(allRoutes);
    statusText.textContent = isReadOnly
      ? 'Archivio globale vuoto o non ancora popolato.'
      : importAllowed
      ? 'Nessun percorso in cache per l\'utente loggato. Premi "Importa".'
      : 'Nessun percorso in cache per l\'utente loggato. Import disabilitato per mancato consenso.';
  } else {
    const filtered = renderRoutes(allRoutes);
    statusText.textContent = isReadOnly
      ? `${filtered.length} percorsi disponibili in archivio globale`
      : `${filtered.length} percorsi in cache per l'utente loggato`;
  }
  if (importAutostart && importAllowed && !importConsentModal?.classList.contains('hidden')) {
    hideImportFooter();
  } else if (importAutostart && importAllowed) {
    window.setTimeout(() => { importRoutes(); }, 150);
  }
}
