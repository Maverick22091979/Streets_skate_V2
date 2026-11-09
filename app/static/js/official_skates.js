(() => {
  const button = document.getElementById("calendarSync");
  const result = document.getElementById("calendarResult");
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!button || !result || !csrf) return;

  const configured = !button.disabled;
  const endpoint = "/api/admin/calendar/history";
  let timer;

  function render(job) {
    button.disabled = !configured || job.status === "running";
    if (job.status === "idle") return;
    const progress = `${job.completed_years || 0}/${job.total_years || "?"} anni`;
    if (job.status === "running") {
      result.textContent = `Sincronizzazione in corso: ${progress}${job.current_year ? `, anno ${job.current_year}` : ""}. Percorsi esaminati: ${job.routes_checked || 0}.`;
      timer = window.setTimeout(poll, 1500);
    } else if (job.status === "done" || job.status === "partial") {
      const failed = job.failed_years?.length ? ` Anni non riusciti: ${job.failed_years.join(", ")}. Premi il pulsante per riprovare.` : "";
      result.textContent = `Sincronizzazione ${job.status === "done" ? "completata" : "parziale"}: ${job.events_updated || 0} eventi aggiornati, ${job.routes_checked || 0} percorsi esaminati.${failed} Ricarica la pagina per aggiornare le candidature.`;
    } else {
      result.textContent = `Errore: ${job.error || "sincronizzazione non riuscita"}`;
    }
  }

  async function request(options) {
    const response = await fetch(endpoint, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Sincronizzazione non riuscita");
    return payload;
  }

  async function poll() {
    try {
      render(await request());
    } catch (error) {
      result.textContent = `Impossibile controllare la sincronizzazione: ${error.message}. Ricarica la pagina per riprovare.`;
      button.disabled = !configured;
    }
  }

  button.addEventListener("click", async () => {
    window.clearTimeout(timer);
    button.disabled = true;
    result.textContent = "Avvio sincronizzazione…";
    try {
      render(await request({ method: "POST", headers: { "X-CSRF-Token": csrf } }));
    } catch (error) {
      result.textContent = `Errore: ${error.message}`;
      button.disabled = !configured;
    }
  });
  poll();
})();
