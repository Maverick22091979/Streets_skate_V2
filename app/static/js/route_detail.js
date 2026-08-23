const routePayloadEl = document.getElementById('routePayload');

if (routePayloadEl && window.L) {
  const payload = JSON.parse(routePayloadEl.textContent || '{}');
  const pts = (((payload.enrichment || {}).point_profile || {}).points || [])
    .filter((p) => p && p.lat !== undefined && p.lng !== undefined)
    .map((p) => [Number(p.lat), Number(p.lng)]);
  const map = L.map('routeMap');

  function pin(cls) {
    return L.divIcon({
      className: `route-pin ${cls}`,
      html: '<span></span>',
      iconSize: [18, 18],
      iconAnchor: [9, 9],
    });
  }

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap',
  }).addTo(map);

  if (pts.length) {
    const line = L.polyline(pts, { color: '#1b5fd1', weight: 5 }).addTo(map);
    map.fitBounds(line.getBounds(), { padding: [24, 24] });
    L.marker(pts[0], { icon: pin('start') }).addTo(map).bindPopup('Partenza');
    if (pts.length > 1) {
      L.marker(pts[pts.length - 1], { icon: pin('end') }).addTo(map).bindPopup('Arrivo');
    }
  } else if (payload.bbox) {
    const b = payload.bbox;
    map.fitBounds([[b.south, b.west], [b.north, b.east]], { padding: [24, 24] });
  } else {
    map.setView([45.4642, 9.19], 10);
  }
}
