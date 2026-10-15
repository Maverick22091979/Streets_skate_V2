(() => {
  const status = document.getElementById('coverageStatus');
  const fit = document.getElementById('fitCoverage');
  const reload = document.getElementById('reloadCoverage');
  const show = document.getElementById('showCoverage');
  const locateBtn = document.getElementById('locateCoverage');
  const locationStatus = document.getElementById('coverageLocationStatus');
  if (!window.L) {
    status.textContent = 'Mappa non disponibile. Ricarica la pagina per riprovare.';
    reload.disabled = true;
    return;
  }

  const map = L.map('coverageMap', { preferCanvas: true }).setView([45.4642, 9.19], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const stops = [[37, 99, 235], [6, 182, 212], [34, 197, 94], [250, 204, 21], [220, 38, 38]];
  let layer;
  let drawing;
  let marker;
  let locating = false;
  let viewChosen = false;

  function color(p) {
    const x = Math.max(0, Math.min(100, p)) / 25;
    const i = Math.min(3, Math.floor(x));
    return `rgb(${stops[i].map((v, j) => Math.round(v + (stops[i + 1][j] - v) * (x - i))).join(',')})`;
  }

  function frame() {
    if (layer && layer.getBounds().isValid()) {
      map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 16 });
      viewChosen = true;
    }
  }

  function locate() {
    if (locating) return;
    const geo = window.navigator?.geolocation;
    if (!geo || window.isSecureContext === false) {
      locationStatus.textContent = 'Posizione non disponibile: usa HTTPS oppure localhost e un browser con geolocalizzazione.';
      return;
    }
    locating = true;
    locateBtn.disabled = true;
    locationStatus.textContent = 'Consenti l’accesso alla posizione per centrare la mappa sul dispositivo.';
    geo.getCurrentPosition((pos) => {
      locating = false;
      locateBtn.disabled = false;
      const point = [pos.coords.latitude, pos.coords.longitude];
      if (marker) map.removeLayer(marker);
      marker = L.circleMarker(point, { radius: 7, color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1 })
        .addTo(map).bindPopup('Posizione del dispositivo');
      viewChosen = true;
      map.setView(point, 15);
      locationStatus.textContent = 'Mappa centrata sulla posizione del dispositivo.';
    }, (err) => {
      locating = false;
      locateBtn.disabled = false;
      locationStatus.textContent = err.code === 1
        ? 'Accesso alla posizione negato. Puoi esplorare la mappa manualmente o abilitare il permesso nel browser.'
        : 'Impossibile rilevare la posizione. Puoi esplorare la mappa o riprovare con La mia posizione.';
      if (!viewChosen) frame();
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  }

  const pause = () => new Promise((resolve) => window.setTimeout(resolve, 0));

  async function request(url, signal) {
    const res = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store', signal });
    if (res.status === 401) window.location.assign('/');
    if (res.status === 403) window.location.assign('/change-password');
    if (!res.ok) throw new Error('Richiesta non riuscita');
    return { status: res.status, data: await res.json() };
  }

  async function addLines(target, features, signal) {
    for (const f of features) {
      const lines = f.geometry.coordinates;
      for (let i = 0; i < lines.length; i += 500) {
        if (signal?.aborted) return;
        target.addData({ ...f, geometry: { type: 'MultiLineString', coordinates: lines.slice(i, i + 500) } });
        await pause();
      }
    }
  }

  async function load() {
    const end = window.sessionGuard.beginTask();
    const ctrl = new AbortController();
    let preview;
    let loaded = 0;
    let framed = Boolean(layer && layer.getBounds().isValid());
    let next;
    reload.disabled = true;
    status.textContent = 'Caricamento dei percorsi dal database…';

    function activate(target) {
      if (layer && layer !== target) map.removeLayer(layer);
      layer = target;
      if (show.checked && !map.hasLayer(layer)) layer.addTo(map);
      fit.disabled = !layer.getBounds().isValid();
      if (!framed && !fit.disabled && !locating && !viewChosen) {
        frame();
        framed = true;
      }
    }

    async function loadTracks() {
      const raw = L.geoJSON(null, { style: { color: '#64748b', weight: 3, opacity: 0.7 }, interactive: false });
      let after = 0;
      let more = true;
      while (more && !ctrl.signal.aborted) {
        const res = await request(`/api/coverage/tracks?after_id=${after}&limit=20`, ctrl.signal);
        if (ctrl.signal.aborted) return;
        await addLines(raw, [res.data.geojson], ctrl.signal);
        if (ctrl.signal.aborted) return;
        loaded += res.data.loaded_routes;
        activate(raw);
        status.textContent = `${loaded} percorsi caricati dal database. Frequenze in elaborazione; puoi già esplorare la mappa. I tratti grigi non indicano ancora una frequenza.`;
        after = res.data.next_after;
        more = res.data.has_more;
      }
    }

    try {
      let res = await request('/api/coverage');
      if (res.status === 202) {
        preview = loadTracks().catch(() => {
          if (!ctrl.signal.aborted) status.textContent = 'Frequenze in elaborazione. Anteprima dei percorsi temporaneamente non disponibile.';
        });
      }
      while (res.status === 202) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        res = await request('/api/coverage');
      }
      ctrl.abort();
      if (preview) await preview;
      const data = res.data;
      status.textContent = 'Visualizzazione delle frequenze salvate nel database…';
      next = L.geoJSON(null, {
        style: (f) => ({ color: color(f.properties.percentage), weight: 5, opacity: 0.85 }),
        onEachFeature: (f, line) => {
          const p = f.properties.percentage;
          const pct = p < 0.01 ? '<0,01' : p.toLocaleString('it-IT', { maximumFractionDigits: 2 });
          const text = `${pct}% · ${f.properties.count} pattinate su ${data.total_routes}`;
          line.bindTooltip(text, { sticky: true, direction: 'top', offset: [0, -10] });
          line.bindPopup(text);
        },
      });
      drawing = next;
      if (show.checked) next.addTo(map);
      await addLines(next, data.geojson.features);
      activate(next);
      if (!data.total_routes) {
        status.textContent = 'Nessuna pattinata in archivio. Importa i percorsi dalla dashboard.';
      } else if (!data.mapped_routes) {
        status.textContent = `${data.total_routes} pattinate in archivio, nessuna con tratti GPS utilizzabili.`;
      } else {
        status.textContent = `${data.total_routes} pattinate totali · ${data.mapped_routes} con tratti GPS utilizzabili · ${data.missing_routes} senza tratti utilizzabili. Il totale include anche queste ultime.`;
      }
      document.getElementById('coverageMethod').textContent =
        `Ogni pattinata conta una sola volta per tratto, in entrambi i sensi. Stima GPS con tolleranza di ${data.tolerance_m} m, senza aggancio alla rete stradale: strade parallele molto vicine possono risultare unite. Salti GPS oltre ${data.max_gap_m} m esclusi (${data.skipped_gaps}).`;
    } catch {
      if (next && next !== layer) map.removeLayer(next);
      status.textContent = 'Impossibile aggiornare le frequenze. I percorsi già caricati restano esplorabili. Premi Aggiorna per riprovare.';
    } finally {
      ctrl.abort();
      if (preview) await preview;
      drawing = undefined;
      end();
      reload.disabled = false;
    }
  }

  fit.addEventListener('click', frame);
  locateBtn.addEventListener('click', locate);
  reload.addEventListener('click', load);
  show.addEventListener('change', () => {
    for (const target of [layer, drawing]) {
      if (!target) continue;
      if (show.checked && !map.hasLayer(target)) target.addTo(map);
      else if (!show.checked) map.removeLayer(target);
    }
  });
  locate();
  load();
})();
