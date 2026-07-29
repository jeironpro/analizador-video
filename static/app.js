(function () {
  const uploadZone = document.getElementById('uploadZone');
  const fileInput = document.getElementById('fileInput');
  const progressBar = document.getElementById('progressBar');
  const progressFill = document.getElementById('progressFill');

  // Confirm modal
  const confirmModal = new bootstrap.Modal('#confirmModal');
  const confirmMessage = document.getElementById('confirmMessage');
  const confirmOk = document.getElementById('confirmOk');

  function showConfirm(msg) {
    return new Promise(resolve => {
      confirmMessage.textContent = msg;
      confirmOk.addEventListener('click', okHandler);
      document.querySelector('#confirmModal [data-bs-dismiss="modal"]').addEventListener('click', cancelHandler);
      document.getElementById('confirmModal').addEventListener('hidden.bs.modal', hideHandler);
      confirmModal.show();
      function cleanup() {
        confirmOk.removeEventListener('click', okHandler);
        document.querySelector('#confirmModal [data-bs-dismiss="modal"]').removeEventListener('click', cancelHandler);
        document.getElementById('confirmModal').removeEventListener('hidden.bs.modal', hideHandler);
      }
      function okHandler() { cleanup(); confirmModal.hide(); resolve(true); }
      function cancelHandler() { cleanup(); resolve(false); }
      function hideHandler() { cleanup(); resolve(false); }
    });
  }

  // Join session modal
  const joinModal = new bootstrap.Modal('#joinModal');
  const joinInput = document.getElementById('joinCodeInput');
  document.getElementById('joinOk').addEventListener('click', () => {
    const code = joinInput.value.trim().toUpperCase();
    if (code.length === 8) {
      joinModal.hide();
      window.location.href = '/s/' + code + '/';
    }
  });
  joinInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('joinOk').click();
  });
  joinInput.addEventListener('input', () => {
    joinInput.value = joinInput.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
  });
  joinModal._element.addEventListener('hidden.bs.modal', () => { joinInput.value = ''; });

  const queueCard = document.getElementById('queueCard');
  const queueContainer = document.getElementById('queueContainer');
  const videoContainer = document.getElementById('videoContainer');
  const sessionCodeEl = document.getElementById('sessionCode');
  const esMap = {};

  const STATUS_MAP = {
    uploaded:   ['bg-secondary',  'Subido'],
    queued:     ['bg-warning text-dark', 'En cola'],
    processing: ['bg-primary',    'Procesando'],
    done:       ['bg-success',    'Completado'],
    error:      ['bg-danger',     'Error'],
  };

  function formatSize(bytes) {
    const mb = bytes / 1024 / 1024;
    if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB';
    return mb.toFixed(1) + ' MB';
  }
  function formatDate(iso) {
    return new Date(iso).toLocaleString('es-ES');
  }
  function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }
  function iconForExt(ext) {
    const map = { mp4: 'file-play', webm: 'file-play', mkv: 'file-earmark', avi: 'file-earmark', mov: 'file-play', mpeg: 'file-play', wmv: 'file-earmark' };
    return 'bi-' + (map[ext.replace('.','')] || 'file-earmark');
  }

  // ───────── Session ─────────
  function loadSession() {
    fetch('/api/session')
      .then(r => r.json())
      .then(d => { if (d.code) sessionCodeEl.textContent = d.code; })
      .catch(() => {});
  }

  window.unirseSesion = function () { joinModal.show(); };

  window.copiarEnlace = function () {
    const url = window.location.href;
    navigator.clipboard.writeText(url).then(() => {
      const btn = document.querySelector('[onclick="copiarEnlace()"]');
      if (!btn) return;
      const orig = btn.innerHTML;
      btn.innerHTML = '<i class="bi bi-check-lg"></i> Copiado';
      setTimeout(() => btn.innerHTML = orig, 2000);
    }).catch(() => {});
  };

  window.eliminarSesion = async function () {
    const ok = await showConfirm('¿Eliminar toda la sesión? Se borrarán todos los videos, archivos y datos. Esta acción no se puede deshacer.');
    if (!ok) return;
    fetch('/api/session/delete', { method: 'POST' })
      .then(r => {
        if (r.ok) window.location.href = '/';
      })
      .catch(() => {});
  };

  // ───────── Upload ─────────
  uploadZone.addEventListener('click', () => fileInput.click());
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', e => { e.preventDefault(); uploadZone.classList.remove('dragover'); handleFiles(e.dataTransfer.files); });
  fileInput.addEventListener('change', () => { if (fileInput.files.length) handleFiles(fileInput.files); });

  function handleFiles(files) {
    progressBar.classList.remove('d-none');
    progressFill.style.width = '0%';
    const totalSize = Array.from(files).reduce((s, f) => s + f.size, 0);
    const uploadedBytes = new Array(files.length).fill(0);
    let done = 0;
    Array.from(files).forEach((file, index) => {
      const formData = new FormData();
      formData.append('video', file);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload', true);
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) {
          uploadedBytes[index] = e.loaded;
          const totalUploaded = uploadedBytes.reduce((a, b) => a + b, 0);
          const pct = Math.min(totalUploaded / totalSize * 100, 99.9);
          progressFill.style.width = pct + '%';
        }
      };
      xhr.onload = () => {
        uploadedBytes[index] = file.size;
        done++;
        if (done === files.length) {
          progressFill.style.width = '100%';
          setTimeout(() => {
            progressBar.classList.add('d-none');
            progressFill.style.width = '0%';
          }, 300);
          loadQueue();
        }
      };
      xhr.onerror = () => { done++; };
      xhr.send(formData);
    });
  }

  // ───────── Queue ─────────
  window._processItem = function (tempId) {
    setQueueStatus(tempId, 'queued');
    const actions = document.getElementById('q-actions-' + tempId);
    if (actions) actions.innerHTML = '<span class="text-muted small">En cola...</span>';
    fetch('/api/queue/' + tempId + '/process', { method: 'POST' })
      .then(r => {
        if (r.ok) startQueueStream(tempId);
        else setQueueStatus(tempId, 'error');
      })
      .catch(() => setQueueStatus(tempId, 'error'));
  };

  window._removeQueueItem = function (tempId) {
    fetch('/api/queue/' + tempId, { method: 'DELETE' })
      .then(r => { if (r.ok) loadQueue(); })
      .catch(() => {});
  };

  function startQueueStream(tempId) {
    if (esMap[tempId]) esMap[tempId].close();
    const es = new EventSource('/api/queue/' + tempId + '/stream');
    esMap[tempId] = es;

    let stopBtn = null;
    const removeStopBtn = () => { if (stopBtn) { stopBtn.remove(); stopBtn = null; } };
    const addStopBtn = () => {
      if (stopBtn) return;
      const term = document.getElementById('q-terminal-' + tempId);
      if (!term) return;
      stopBtn = document.createElement('button');
      stopBtn.id = 'q-stop-' + tempId;
      stopBtn.className = 'btn btn-outline-danger btn-sm w-100 mt-2 rounded-3';
      stopBtn.innerHTML = '<i class="bi bi-stop-fill"></i> Detener';
      stopBtn.onclick = () => {
        stopBtn.disabled = true;
        stopBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Deteniendo...';
        fetch('/api/queue/' + tempId + '/cancel', { method: 'POST' }).catch(() => {});
      };
      term.parentNode.insertBefore(stopBtn, term.nextSibling);
    };

    es.addEventListener('step', e => {
      addStopBtn();
      const d = JSON.parse(e.data);
      appendTerminal(tempId, d.status, d.message);
      const row = document.getElementById('q-' + tempId);
      if (row) row.classList.add('processing');
      setQueueStatus(tempId, 'processing');
    });

    es.addEventListener('complete', () => {
      removeStopBtn();
      es.close();
      delete esMap[tempId];
      appendTerminal(tempId, 'complete', '\u2713 Completado');
      setQueueStatus(tempId, 'done');
      loadVideos();
      setTimeout(() => window._removeQueueItem(tempId), 2000);
    });

    es.addEventListener('error', e => {
      removeStopBtn();
      es.close();
      delete esMap[tempId];
      let msg = 'Error';
      if (e.data) {
        try { const d = JSON.parse(e.data); msg = d.message || msg; } catch (_) {}
      }
      appendTerminal(tempId, 'error', '\u2717 ' + msg);
      setQueueStatus(tempId, 'error');
      setTimeout(() => window._removeQueueItem(tempId), 3000);
    });
  }

  function appendTerminal(tempId, type, text) {
    const term = document.getElementById('q-terminal-' + tempId);
    if (!term) return;
    term.classList.remove('d-none');
    const line = document.createElement('div');
    line.className = 'line';
    if (type === 'ok' || type === 'complete') line.classList.add('ok');
    else if (type === 'error') line.classList.add('error');
    else if (type === 'info') line.classList.add('info');
    else if (type === 'checking') line.classList.add('checking');
    else if (type === 'complete') line.classList.add('complete');
    const ts = document.createElement('span');
    ts.className = 'timestamp';
    ts.textContent = new Date().toLocaleTimeString('es-ES');
    line.appendChild(ts);
    line.appendChild(document.createTextNode(text));
    term.appendChild(line);
    term.scrollTop = term.scrollHeight;
  }

  function setQueueStatus(tempId, status) {
    const el = document.getElementById('q-status-' + tempId);
    if (!el) return;
    const [cls, label] = STATUS_MAP[status] || ['bg-info', status];
    el.className = 'badge ' + cls;
    el.textContent = label;
    const row = document.getElementById('q-' + tempId);
    if (row) row.classList.toggle('processing', status === 'processing');
  }

  function makeQueueItem(item) {
    const tempId = escapeHtml(item.temp_id);
    const name = escapeHtml(item.original_name || 'video');
    const status = item.status;
    const statusCls = (STATUS_MAP[status] || ['bg-info'])[0];
    const statusLabel = (STATUS_MAP[status] || [,''])[1];

    let actionsHtml = '';
    if (status === 'uploaded') {
      actionsHtml = `
        <button class="btn btn-sm btn-primary" onclick="window._processItem('${tempId}')"><i class="bi bi-play-fill"></i> Procesar</button>
        <button class="btn btn-sm btn-outline-danger" onclick="window._removeQueueItem('${tempId}')"><i class="bi bi-trash"></i> Eliminar</button>`;
    } else if (status === 'queued' || status === 'processing') {
      actionsHtml = '<span class="text-muted small">En cola...</span>';
    }

    return `
      <div id="q-${tempId}" class="queue-item p-3 mb-2${status === 'processing' ? ' processing' : ''}">
        <div class="d-flex justify-content-between align-items-center gap-2">
          <div class="d-flex align-items-center gap-2 text-truncate">
            <i class="bi bi-file-play text-primary flex-shrink-0"></i>
            <span class="fw-medium text-truncate">${name}</span>
          </div>
          <div class="d-flex align-items-center gap-2 flex-shrink-0">
            <span class="badge ${statusCls}" id="q-status-${tempId}">${statusLabel}</span>
            <div id="q-actions-${tempId}">${actionsHtml}</div>
          </div>
        </div>
        <div id="q-terminal-${tempId}" class="terminal mt-2 d-none"></div>
      </div>`;
  }

  function renderQueue(items) {
    const existingIds = new Set(items.map(i => i.temp_id));
    let activeCount = 0;

    items.forEach(item => {
      if (item.status !== 'done' && item.status !== 'error') activeCount++;
      const el = document.getElementById('q-' + item.temp_id);
      if (el) {
        setQueueStatus(item.temp_id, item.status);
        if (item.status === 'uploaded') {
          const actions = document.getElementById('q-actions-' + item.temp_id);
          if (actions) {
            actions.innerHTML = `
              <button class="btn btn-sm btn-primary" onclick="window._processItem('${item.temp_id}')"><i class="bi bi-play-fill"></i> Procesar</button>
              <button class="btn btn-sm btn-outline-danger" onclick="window._removeQueueItem('${item.temp_id}')"><i class="bi bi-trash"></i> Eliminar</button>`;
          }
        }
        if (item.status === 'done' || item.status === 'error') {
          const actions = document.getElementById('q-actions-' + item.temp_id);
          if (actions) actions.innerHTML = '';
        }
        if (item.status === 'processing' || item.status === 'queued') {
          if (!esMap[item.temp_id]) startQueueStream(item.temp_id);
        }
      } else {
        queueContainer.insertAdjacentHTML('beforeend', makeQueueItem(item));
        if (item.status === 'processing' || item.status === 'queued') {
          startQueueStream(item.temp_id);
        }
      }
    });

    document.querySelectorAll('#queueContainer > .queue-item').forEach(el => {
      const id = el.id.replace(/^q-/, '');
      if (id && !existingIds.has(id)) el.remove();
    });

    document.getElementById('queueCount').textContent = activeCount;
    queueCard.classList.toggle('d-none', items.length === 0);
    if (items.length === 0) queueContainer.innerHTML = '';
  }

  function loadQueue() {
    fetch('/api/queue')
      .then(r => r.json())
      .then(items => renderQueue(items))
      .catch(() => {});
  }

  // ───────── Videos ─────────
  function renderVideos(videos) {
    document.getElementById('videoCount').textContent = videos.length + ' video' + (videos.length !== 1 ? 's' : '');
    if (videos.length === 0) {
      videoContainer.innerHTML = '<div class="empty-state"><i class="bi bi-film"></i><p class="mb-0">No hay videos almacenados</p></div>';
      return;
    }
    let html = '';
    videos.forEach(v => {
      const ext = v.container || '?';
      const icon = iconForExt(ext);
      html += `
        <div class="video-card p-3">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <div class="text-truncate me-2">
              <i class="bi ${icon} text-primary me-1"></i>
              <span class="fw-semibold" style="font-size:.9rem">${escapeHtml(v.original_name)}</span>
            </div>
            <span class="badge bg-light text-dark border flex-shrink-0">${escapeHtml(ext.toUpperCase())}</span>
          </div>
          <div class="d-flex gap-2 mb-2">
            <span class="badge bg-info bg-opacity-10 text-info border border-info border-opacity-25">${formatSize(v.size)}</span>
          </div>
          <p class="text-muted small mb-2">${formatDate(v.uploaded_at)}</p>
          <div class="d-flex gap-2">
            <a href="/api/download/${v.id}" class="btn btn-sm btn-outline-success"><i class="bi bi-download"></i> Descargar</a>
            <button class="btn btn-sm btn-outline-danger" onclick="window._deleteVideo('${v.id}')"><i class="bi bi-trash"></i> Eliminar</button>
          </div>
        </div>`;
    });
    videoContainer.innerHTML = html;
  }

  function loadVideos() {
    fetch('/api/videos')
      .then(r => r.json())
      .then(videos => renderVideos(videos))
      .catch(() => {});
  }

  window._deleteVideo = async function (id) {
    const ok = await showConfirm('¿Eliminar este video definitivamente?');
    if (!ok) return;
    fetch('/api/delete/' + id, { method: 'DELETE' })
      .then(r => { if (r.ok) loadVideos(); })
      .catch(() => {});
  };

  // ───────── Init ─────────
  loadSession();
  loadQueue();
  loadVideos();

  let queueES = null;
  function connectQueueSSE() {
    if (queueES) queueES.close();
    queueES = new EventSource('/api/queue/events');
    queueES.onmessage = e => {
      try { renderQueue(JSON.parse(e.data)); } catch (_) {}
    };
    queueES.onerror = () => { queueES.close(); queueES = null; };
  }
  connectQueueSSE();
  setInterval(() => { if (!queueES) loadQueue(); }, 15000);
})();