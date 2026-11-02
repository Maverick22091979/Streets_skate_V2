(() => {
  const button = document.getElementById("calendarSync");
  const result = document.getElementById("calendarResult");
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!button || !result || !csrf) return;

  button.addEventListener("click", async () => {
    button.disabled = true;
    result.textContent = "Sincronizzazione in corso…";
    try {
      const response = await fetch("/api/admin/calendar/sync", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Sincronizzazione non riuscita");
      result.textContent = `Sincronizzazione completata: ${payload.changed || 0} eventi aggiornati.`;
      window.setTimeout(() => window.location.reload(), 800);
    } catch (error) {
      result.textContent = `Errore: ${error.message || "sincronizzazione non riuscita"}`;
      button.disabled = false;
    }
  });
})();
