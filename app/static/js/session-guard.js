(() => {
  const script = document.querySelector('script[data-session-guard]');
  if (!script) return;

  const csrfToken = script.dataset.csrf || '';
  const timeoutMs = Math.max(0, Number(script.dataset.timeout || 0) * 1000);
  let lastActivity = Date.now();
  let navigating = false;
  let closing = false;
  let activeRequests = 0;

  const beginTask = () => {
    activeRequests += 1;
    lastActivity = Date.now();
    let done = false;
    return () => {
      if (done) return;
      done = true;
      activeRequests = Math.max(0, activeRequests - 1);
      lastActivity = Date.now();
    };
  };
  window.sessionGuard = { beginTask };

  const isInternalRequest = (input) => {
    const target = input instanceof Request ? input.url : String(input);
    try {
      return new URL(target, window.location.href).origin === window.location.origin;
    } catch (_) {
      return false;
    }
  };

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (...args) => {
    const input = args[0];
    const isSessionClose = isInternalRequest(input) && new URL(input instanceof Request ? input.url : String(input), window.location.href).pathname === '/session/close-tab';
    if (!isInternalRequest(input) || isSessionClose) return nativeFetch(...args);
    const end = beginTask();
    return nativeFetch(...args).finally(end);
  };

  const closeSession = (reason, redirect = false) => {
    if (closing || !csrfToken) return;
    closing = true;
    const data = new FormData();
    data.append('csrf_token', csrfToken);
    data.append('reason', reason);

    if (reason === 'tab_closed' && navigator.sendBeacon) {
      navigator.sendBeacon('/session/close-tab', data);
      return;
    }

    fetch('/session/close-tab', { method: 'POST', body: data, credentials: 'same-origin' })
      .finally(() => {
        if (redirect) window.location.replace('/?status=Sessione%20scaduta%20per%20inattivita');
      });
  };

  for (const eventName of ['pointerdown', 'keydown', 'scroll', 'touchstart']) {
    window.addEventListener(eventName, () => { lastActivity = Date.now(); }, { passive: true });
  }

  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || event.metaKey || event.ctrlKey || event.shiftKey) return;
    if (new URL(link.href, window.location.href).origin === window.location.origin) navigating = true;
  });
  document.addEventListener('click', (event) => {
    if (event.target.closest('.route-card[data-route-href]')) navigating = true;
  });
  document.addEventListener('submit', () => { navigating = true; });

  window.addEventListener('pagehide', (event) => {
    if (!event.persisted && !navigating && activeRequests === 0) closeSession('tab_closed');
  });

  if (timeoutMs > 0) {
    window.setInterval(() => {
      if (activeRequests === 0 && Date.now() - lastActivity >= timeoutMs) closeSession('timeout', true);
    }, Math.min(15000, Math.max(1000, Math.floor(timeoutMs / 6))));
  }
})();
