(() => {
  const form = document.getElementById('download-form');
  const url = document.getElementById('source-url');
  const quality = document.getElementById('quality');
  const status = document.getElementById('status');
  const label = document.getElementById('status-label');
  const percent = document.getElementById('status-percent');
  const progress = document.getElementById('progress-fill');
  const detail = document.getElementById('status-detail');
  const link = document.getElementById('file-link');
  const submit = document.getElementById('submit-button');
  let mode = 'video';
  let polling = false;
  let pollTimer;
  const qualityPresets = {
    video: [['360', '360p · liviano'], ['720', '720p · recomendado'], ['1080', '1080p · alta calidad']],
    audio: [['128', '128 kbps · liviano'], ['192', '192 kbps · recomendado'], ['320', '320 kbps · máxima calidad']]
  };
  function setMode(next) {
    mode = next;
    document.querySelectorAll('.mode').forEach(btn => {
      const selected = btn.dataset.mode === next;
      btn.classList.toggle('selected', selected);
      btn.setAttribute('aria-checked', String(selected));
    });
    quality.innerHTML = '';
    qualityPresets[mode].forEach(([value, name]) => {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = name;
      quality.append(opt);
    });
    quality.selectedIndex = 1;
  }
  document.querySelectorAll('.mode').forEach(btn => btn.addEventListener('click', () => setMode(btn.dataset.mode)));
  document.getElementById('paste-button').addEventListener('click', async () => {
    try {
      url.value = await navigator.clipboard.readText();
      url.focus();
    } catch (_) {
      url.focus();
      url.select();
    }
  });
  function show(current, text, pct, message) {
    status.hidden = false;
    label.textContent = text;
    percent.textContent = pct + '%';
    progress.style.width = pct + '%';
    detail.textContent = message || '';
    status.dataset.state = current;
  }
  function finish() {
    polling = false;
    clearTimeout(pollTimer);
    submit.disabled = false;
    submit.firstElementChild.textContent = 'Preparar otra descarga';
  }
  function fail(message) {
    show('error', 'No se pudo completar', 0, message || 'Probá con otro enlace o más tarde.');
    link.hidden = true;
    finish();
  }
  async function json(url, options) {
    const response = await fetch(url, options);
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw Error(payload.error || 'El servidor no respondió correctamente.');
    return payload;
  }
  async function poll(job) {
    if (!polling) return;
    try {
      const r = await json('/api/job/' + job);
      if (r.status === 'error') return fail(r.error);
      if (r.status === 'ready') {
        show('ready', '¡Tu archivo está listo!', 100, r.title || 'Presioná el botón para guardar el archivo.');
        link.href = '/api/file/' + job;
        link.hidden = false;
        link.setAttribute('download', r.filename || 'descarga');
        finish();
        link.click();
        return;
      }
      const states = {
        queued: 'Esperando turno...',
        extracting: 'Buscando la mejor fuente...',
        downloading: 'Descargando el contenido...',
        processing: 'Convirtiendo el archivo...'
      };
      show(r.status, states[r.status] || 'Procesando...', r.percent || 0, 'No cierres esta pestaña durante la preparación.');
      pollTimer = setTimeout(() => poll(job), 1250);
    } catch (err) {
      fail(err.message);
    }
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (polling) return;
    const value = url.value.trim();
    if (!value) return url.focus();
    submit.disabled = true;
    submit.firstElementChild.textContent = 'Preparando...';
    link.hidden = true;
    show('queued', 'Iniciando descarga...', 0, 'Estamos preparando tu solicitud.');
    try {
      const r = await json('/api/download', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: value, mode, quality: quality.value})
      });
      polling = true;
      poll(r.job);
    } catch (err) {
      fail(err.message);
    }
  });
})();
