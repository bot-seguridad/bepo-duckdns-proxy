(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const searchForm = $('search-form');
  const searchInput = $('search-query');
  const searchButton = $('search-button');
  const searchState = $('search-state');
  const resultsSection = $('results-section');
  const resultsNode = $('search-results');
  const resultsCount = $('results-count');
  const downloadArea = $('download-area');
  const form = $('download-form');
  const quality = $('quality');
  const status = $('status');
  const label = $('status-label');
  const percent = $('status-percent');
  const progress = $('progress-fill');
  const detail = $('status-detail');
  const link = $('file-link');
  const submit = $('submit-button');
  const qualityPresets = {
    video: [['360', '360p · liviano'], ['720', '720p · recomendado'], ['1080', '1080p · alta calidad']],
    audio: [['128', '128 kbps · liviano'], ['192', '192 kbps · recomendado'], ['320', '320 kbps · máxima calidad']]
  };
  let mode = 'video', selected = null, results = [], searching = false, polling = false, pollTimer = 0;
  const setSearchMessage = (message, error = false) => {
    searchState.hidden = !message;
    searchState.textContent = message || '';
    searchState.classList.toggle('is-error', error);
  };
  const clearDownload = () => {
    link.hidden = true;
    link.removeAttribute('href');
    link.removeAttribute('download');
  };
  const showProgress = (state, title, progressValue, description) => {
    status.hidden = false;
    status.dataset.state = state;
    label.textContent = title;
    const value = Math.max(0, Math.min(100, Number(progressValue) || 0));
    percent.textContent = value + '%';
    progress.style.width = value + '%';
    detail.textContent = description || '';
  };
  const downloadDone = () => {
    clearTimeout(pollTimer);
    polling = false;
    submit.disabled = false;
    submit.firstElementChild.textContent = mode === 'audio' ? 'Descargar audio MP3' : 'Descargar video MP4';
  };
  const downloadError = message => {
    clearDownload();
    showProgress('error', 'No se pudo descargar', 0, message || 'La plataforma no permitió obtener este archivo.');
    downloadDone();
  };
  const api = async (url, config) => {
    const response = await fetch(url, config);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(body.error || ('Error del servidor (' + response.status + ')'));
    return body;
  };
  const textLine = (tag, value, className) => {
    const el = document.createElement(tag);
    el.textContent = value || '';
    if (className) el.className = className;
    return el;
  };
  const setMode = next => {
    mode = next;
    document.querySelectorAll('.mode').forEach(button => {
      const active = button.dataset.mode === next;
      button.classList.toggle('selected', active);
      button.setAttribute('aria-checked', String(active));
    });
    quality.replaceChildren();
    qualityPresets[next].forEach(([value, title]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = title;
      quality.appendChild(option);
    });
    quality.selectedIndex = 1;
    if (!polling) downloadDone();
  };
  const selectVideo = item => {
    if (polling) return;
    selected = item;
    resultsSection.hidden = true;
    downloadArea.hidden = false;
    status.hidden = true;
    clearDownload();
    $('selected-thumb').src = item.thumbnail;
    $('selected-title').textContent = item.title;
    $('selected-channel').textContent = [item.channel, item.duration].filter(Boolean).join(' · ') || 'YouTube';
    setSearchMessage('');
    setMode(mode);
    downloadArea.scrollIntoView({behavior:'smooth', block:'nearest'});
  };
  const renderResults = () => {
    resultsNode.replaceChildren();
    results.forEach((item,index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'result-card';
      button.setAttribute('aria-label', 'Elegir ' + item.title);
      const image = document.createElement('img');
      image.src = item.thumbnail;
      image.loading = index < 3 ? 'eager' : 'lazy';
      image.alt = '';
      const info = document.createElement('span');
      info.className = 'result-meta';
      info.appendChild(textLine('strong', item.title));
      info.appendChild(textLine('span', [item.channel, item.duration].filter(Boolean).join(' · ') || 'YouTube'));
      const action = textLine('span', 'Elegir ›', 'result-action');
      button.append(image, info, action);
      button.addEventListener('click', () => selectVideo(item));
      resultsNode.appendChild(button);
    });
    resultsCount.textContent = results.length + ' videos';
    resultsSection.hidden = !results.length;
  };
  searchForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (searching) return;
    const query = searchInput.value.trim();
    if (query.length < 2) return searchInput.focus();
    selected = null;
    results = [];
    searching = true;
    searchButton.disabled = true;
    searchButton.textContent = 'Buscando...';
    downloadArea.hidden = true;
    resultsSection.hidden = true;
    status.hidden = true;
    clearDownload();
    setSearchMessage('Buscando videos y canciones en YouTube...');
    try {
      const data = await api('/api/search?q=' + encodeURIComponent(query), {cache:'no-store'});
      results = Array.isArray(data.results) ? data.results : [];
      renderResults();
      setSearchMessage(results.length ? 'Elegí el video que querés descargar.' : 'No se encontraron videos.');
    } catch (error) {
      setSearchMessage(error.message || 'La búsqueda no está disponible.', true);
    } finally {
      searching = false;
      searchButton.disabled = false;
      searchButton.textContent = 'Buscar';
    }
  });
  $('change-video').addEventListener('click', () => {
    if (polling) return;
    downloadArea.hidden = true;
    status.hidden = true;
    clearDownload();
    selected = null;
    resultsSection.hidden = !results.length;
    setSearchMessage(results.length ? 'Seleccioná otro resultado.' : 'Buscá un video para comenzar.');
    (results.length ? resultsSection : searchInput).scrollIntoView({behavior:'smooth',block:'nearest'});
  });
  document.querySelectorAll('.mode').forEach(button => {
    button.addEventListener('click', () => setMode(button.dataset.mode));
  });
  const poll = async id => {
    if (!polling) return;
    try {
      const job = await api('/api/job/' + encodeURIComponent(id), {cache:'no-store'});
      if (job.status === 'error') return downloadError(job.error);
      if (job.status === 'ready') {
        showProgress('ready', '¡Archivo listo!', 100, job.title || 'Tocá el botón para guardar el archivo.');
        link.href = '/api/file/' + encodeURIComponent(id);
        link.hidden = false;
        link.setAttribute('download', job.filename || 'descarga');
        downloadDone();
        link.click();
        return;
      }
      const states = {
        queued:'Esperando turno...',
        extracting:'Obteniendo los formatos del video...',
        downloading:'Descargando el archivo...',
        processing:'Convirtiendo el formato...'
      };
      showProgress(job.status, states[job.status] || 'Preparando...', job.percent, 'Podés dejar abierta esta pestaña hasta terminar.');
      pollTimer = setTimeout(() => poll(id), 1400);
    } catch (error) {
      downloadError(error.message);
    }
  };
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (polling || !selected) return;
    submit.disabled = true;
    submit.firstElementChild.textContent = 'Preparando...';
    clearDownload();
    showProgress('queued', 'Iniciando descarga...', 0, 'Obteniendo el archivo directamente desde la plataforma.');
    try {
      const job = await api('/api/download', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({video_id:selected.id, mode, quality:quality.value})
      });
      polling = true;
      poll(job.job);
    } catch (error) {
      downloadError(error.message);
    }
  });
  setMode('video');
})();
