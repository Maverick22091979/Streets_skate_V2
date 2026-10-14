(() => {
  const status = document.getElementById('coverageStatus');
  const fit = document.getElementById('fitCoverage');
  const reload = document.getElementById('reloadCoverage');
  const show = document.getElementById('showCoverage');
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

  function color(p) {
    const x = Math.max(0, Math.min(100, p)) / 25;
    const i = Math.min(3, Math.floor(x));
    return `rgb(${stops[i].map((v, j) => Math.round(v + (stops[i + 1][j] - v) * (x - i))).join(',')})`;
  }

  function frame() {
    if (layer && layer.getBounds().isValid()) {
      map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 16 });
    }
  }

  async function load() {
    const end = window.sessionGuard.beginTask();
    reload.disabled = true;
    status.textContent = 'Calcolo delle frequenze su tutte le pattinate…';
    try {
      const res = await fetch('/api/coverage', { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (res.status === 401) {
        window.location.assign('/');
        return;
      }
      if (!res.ok) throw new Error('Richiesta non riuscita');
      const data = await res.json();
      const next = L.geoJSON(data.geojson, {
        style: (f) => ({ color: color(f.properties.percentage), weight: 5, opacity: 0.85 }),
        onEachFeature: (f, line) => {
          const p = f.properties.percentage;
          const pct = p < 0.01 ? '<0,01' : p.toLocaleString('it-IT', { maximumFractionDigits: 2 });
          line.bindPopup(`${pct}% · ${f.properties.count} pattinate su ${data.total_routes}`);
        },
      });
      if (layer) map.removeLayer(layer);
      layer = next;
      if (show.checked) layer.addTo(map);
      fit.disabled = !layer.getBounds().isValid();
      frame();
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
      status.textContent = 'Impossibile aggiornare le frequenze. Premi Aggiorna per riprovare.';
    } finally {
      end();
      reload.disabled = false;
    }
  }

  fit.addEventListener('click', frame);
  reload.addEventListener('click', load);
  show.addEventListener('change', () => {
    if (!layer) return;
    if (show.checked) layer.addTo(map);
    else map.removeLayer(layer);
  });
  load();
})();
