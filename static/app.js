(function () {
  const uploadZone = document.getElementById('uploadZone');
  const fileInput = document.getElementById('fileInput');
  const progressBar = document.getElementById('progressBar');
  const progressFill = document.getElementById('progressFill');
  const queueCard = document.getElementById('queueCard');
  const queueContainer = document.getElementById('queueContainer');
  const videoListEl = document.getElementById('videoList');
  const esMap = {};

  const STATUS_MAP = {
    uploaded:   ['bg-secondary',  'Subido'],
    queued:     ['bg-warning text-dark', 'En cola'],
    processing: ['bg-primary',    'Procesando'],
    done:       ['bg-success',    'Completado'],
    error:      ['bg-danger',     'Error'],
  };

  // ───────────────────────── Upload ─────────────────────────
  uploadZone.addEventListener('click', () => fileInput.click());
  uploadZone.addEventListener('dragover', e => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
  });
  uploadZone.addEventListener('dragleave', () => {
    uploadZone.classList.remove('dragover');
  });
  uploadZone.addEventListener('drop', e => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    handleFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) handleFiles(fileInput.files);
  });

  function handleFiles(files) {
    progressBar.classList.remove('d-none');
    progressFill.style.width = '0%';
    let done = 0;
    for (const file of files) {
      const formData = new FormData();
      formData.append('video', file);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload', true);
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) {
          const total = Array.from(files).reduce((s, f) => s + f.size, 0);
          const uploaded = Array.from(files).slice(0, done).reduce((s, f) => s + f.size, 0) + e.loaded;
          progressFill.style.width = (uploaded / total * 100) + '%';
        }
      };
      xhr.onload = () => {
        done++;
        if (xhr.status === 201) {
          const data = JSON.parse(xhr.responseText);
          addQueueItem(data.temp_id, data.original_name);
        }
        if (done === files.length) {
          progressBar.classList.add('d-none');
          progressFill.style.width = '0%';
          loadQueue();
        }
      };
      xhr.onerror = () => { done++; };
      xhr.send(formData);
    }
  }

  // ───────────────────────── Queue items ─────────────────────────
  function addQueueItem(tempId, name) {
    queueCard.classList.remove('d-none');
    const row = document.createElement('div');
    row.id = 'q-' + tempId;
    row.className = 'queue-item p-3 mb-2';
    row.innerHTML = `
      <div class="d-flex justify-content-between align-items-center gap-2">
        <div class="d-flex align-items-center gap-2 text-truncate">
          <i class="bi bi-file-play text-primary"></i>
          <strong class="text-truncate">${escapeHtml(name)}</strong>
        </div>
        <div class="d-flex align-items-center gap-2 flex-shrink-0">
          <span class="badge badge-status ${STATUS_MAP.uploaded[0]}" id="q-status-${tempId}">${STATUS_MAP.uploaded[1]}</span>
          <div id="q-actions-${tempId}">
            <button class="btn btn-primary btn-action" onclick="window._processItem('${tempId}')">Procesar</button>
            <button class="btn btn-outline-danger btn-icon" onclick="window._removeQueueItem('${tempId}')" title="Eliminar">
              <i class="bi bi-trash"></i>
            </button>
          </div>
        </div>
      </div>
      <div id="q-terminal-${tempId}" class="terminal mt-2 d-none"></div>
    `;
    queueContainer.appendChild(row);
    // re-trigger animation
    row.style.animation = 'none';
    row.offsetHeight;
    row.style.animation = '';
  }

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

    es.addEventListener('step', e => {
      const d = JSON.parse(e.data);
      appendTerminal(tempId, d.status, d.message);
      const row = document.getElementById('q-' + tempId);
      if (row) row.classList.add('processing');
      setQueueStatus(tempId, 'processing');
    });

    es.addEventListener('complete', () => {
      es.close();
      delete esMap[tempId];
      appendTerminal(tempId, 'complete', '\u2713 Completado');
      setQueueStatus(tempId, 'done');
      loadVideos();
      setTimeout(() => window._removeQueueItem(tempId), 2000);
    });

    es.addEventListener('error', e => {
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
    el.className = 'badge badge-status ' + cls;
    el.textContent = label;
    const row = document.getElementById('q-' + tempId);
    if (row) row.classList.toggle('processing', status === 'processing');
  }

  // ───────────────────────── Queue rendering ─────────────────────────
  function renderQueue(items) {
    const existingIds = new Set(items.map(i => i.temp_id));
    let queueCount = 0;
    items.forEach(item => {
      if (item.status !== 'done' && item.status !== 'error') queueCount++;
      if (!document.getElementById('q-' + item.temp_id)) {
        addQueueItem(item.temp_id, item.original_name || 'video');
      }
      setQueueStatus(item.temp_id, item.status);
      if (item.status === 'processing' || item.status === 'queued') {
        if (!esMap[item.temp_id]) startQueueStream(item.temp_id);
      }
      if (item.status === 'uploaded') {
        const actions = document.getElementById('q-actions-' + item.temp_id);
        if (actions) {
          actions.innerHTML = `
            <button class="btn btn-primary btn-action" onclick="window._processItem('${item.temp_id}')">Procesar</button>
            <button class="btn btn-outline-danger btn-icon" onclick="window._removeQueueItem('${item.temp_id}')" title="Eliminar">
              <i class="bi bi-trash"></i>
            </button>
          `;
        }
      }
      if (item.status === 'done' || item.status === 'error') {
        const actions = document.getElementById('q-actions-' + item.temp_id);
        if (actions) actions.innerHTML = '';
      }
    });
    document.querySelectorAll('#queueContainer > .queue-item').forEach(el => {
      const id = el.id.replace(/^q-/, '');
      if (id && !existingIds.has(id)) el.remove();
    });
    const countEl = document.getElementById('queueCount');
    if (countEl) countEl.textContent = queueCount;
    if (items.length === 0) {
      queueCard.classList.add('d-none');
      queueContainer.innerHTML = '';
    } else {
      queueCard.classList.remove('d-none');
    }
  }

  function loadQueue() {
    fetch('/api/queue')
      .then(r => r.json())
      .then(items => renderQueue(items))
      .catch(() => {});
  }

  // ───────────────────────── Videos ─────────────────────────
  function loadVideos() {
    fetch('/api/videos')
      .then(r => r.json())
      .then(videos => {
        document.getElementById('videoCount').textContent = videos.length + ' video' + (videos.length !== 1 ? 's' : '');
        if (videos.length === 0) {
          videoListEl.innerHTML = '<div class="empty-state"><i class="bi bi-film"></i><p class="mb-0">No hay videos almacenados</p></div>';
          return;
        }
        let html = '<div class="table-responsive"><table class="table table-videos"><thead><tr><th>Nombre</th><th>Tamaño</th><th>Formato</th><th>Subido</th><th class="text-end">Acciones</th></tr></thead><tbody>';
        videos.forEach(v => {
          const size = (v.size / 1024 / 1024).toFixed(1);
          const date = new Date(v.uploaded_at).toLocaleString('es-ES');
          html += `<tr>
            <td><strong>${escapeHtml(v.original_name)}</strong></td>
            <td><span class="badge bg-info bg-opacity-10 text-info">${size} MB</span></td>
            <td><span class="badge bg-success bg-opacity-10 text-success">${escapeHtml(v.container || '?')}</span></td>
            <td class="text-muted small">${date}</td>
            <td class="text-end">
              <a href="/api/download/${v.id}" class="btn btn-success btn-action me-1"><i class="bi bi-download me-1"></i>Descargar</a>
              <button class="btn btn-outline-danger btn-icon" onclick="window._deleteVideo('${v.id}')" title="Eliminar"><i class="bi bi-trash"></i></button>
            </td>
          </tr>`;
        });
        html += '</tbody></table></div>';
        videoListEl.innerHTML = html;
      })
      .catch(() => {});
  }

  window._deleteVideo = function (id) {
    if (!confirm('¿Eliminar este video definitivamente?')) return;
    fetch('/api/delete/' + id, { method: 'DELETE' })
      .then(r => { if (r.ok) loadVideos(); })
      .catch(() => {});
  };

  function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }

  // ───────────────────────── Bootstrap Tooltips ─────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
    }
  });

  // ───────────────────────── Init ─────────────────────────
  loadQueue();
  loadVideos();

  let queueES = null;
  function connectQueueSSE() {
    if (queueES) queueES.close();
    queueES = new EventSource('/api/queue/events');
    queueES.onmessage = e => {
      try { renderQueue(JSON.parse(e.data)); } catch (_) {}
    };
    queueES.onerror = () => {
      queueES.close();
      queueES = null;
    };
  }
  connectQueueSSE();
  setInterval(() => { if (!queueES) loadQueue(); }, 15000);
})();
