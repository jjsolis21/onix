// ONIX/frontend/admin/js/admin-app.js
// ─────────────────────────────────────────────────────────────────────────────
// Shell + Biblioteca Musical — Ónix FM
// Paleta Jazler Soho. 6 cue points: inicio, intro, inicio_coro, final_coro,
// outro, mezcla.  Todas las funciones de modal exportadas a window al final.
// ─────────────────────────────────────────────────────────────────────────────

/* ── SHELL ────────────────────────────────────────────────────────────────── */
const Shell = {
  config: { basePath: 'sections/', containerId: 'module-view', activeClass: 'active' },
  init() {
    console.log('Ónix FM Shell — Sistema Iniciado');
    this.bindEvents();
    this.loadSection('01-estado-global');
  },
  bindEvents() {
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', () => {
        const module = btn.getAttribute('data-module');
        if (!module) return;
        document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const routes = {
          dashboard: '01-estado-global',
          biblioteca: '02-biblioteca-musical',
          pautas: '03-pautas',              // PARCHE 1
          cartuchera: '04-editor-cartuchera',
          logs: '05-historial-emision',
          engine: '06-motor-audio',
        };
        // PARCHE 2: hook post-carga PautasController
        this.loadSection(routes[module]).then(() => {
          if (module === 'pautas') {
            if (!window.PautasController) {
              const s = document.createElement('script');
              s.src = 'sections/03-pautas/pautas-controller.js';
              s.async = true;
              s.onload = () => window.PautasController?.init();
              s.onerror = () => console.error('[Ónix] No se pudo cargar pautas-controller.js');
              document.head.appendChild(s);
            } else {
              window.PautasController.init();
            }
          }
        });
      });
    });
  },
  // PARCHE 3: viewport-main + HTML_OVERRIDES
  async loadSection(sectionName) {
    const container = document.getElementById('viewport-main')
      || document.getElementById(this.config.containerId);
    if (!container) return;
    const HTML_OVERRIDES = {
      '03-pautas': 'sections/03-programacion-pautas/programacion-pautas.html',
    };
    try {
      const url = HTML_OVERRIDES[sectionName]
        || `${this.config.basePath}${sectionName}/${sectionName.split('-').slice(1).join('-')}.html`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`No se pudo cargar: ${sectionName}`);
      container.innerHTML = await response.text();
      const titleEl = document.getElementById('module-title');
      if (titleEl) titleEl.textContent = sectionName.replace(/-/g, ' ').toUpperCase().slice(3);
    } catch (error) {
      container.innerHTML = `Error al cargar pieza: ${error.message}`;
    }
  },
};
document.addEventListener('DOMContentLoaded', () => Shell.init());

'use strict';

let schema = { categorias: [] };
const getCatValues = n => { const c = schema.categorias.find(x => x.nombre_interno === n); return c ? c.valores.map(v => v.valor) : []; };

let library = [];
let filtered = [];
let sortKey = null;
let sortDir = 1;
let editingId = null;
let playingId = null;
let audioEl = null;
let playerTimer = null;
let selectedFile = null;

const $ = id => document.getElementById(id);
const getOverlay = () => $('jz-overlay');
const getTbody = () => $('jz-tbody');
const getCountEl = () => $('jz-count');
const getStatusMsg = () => $('jz-status-msg');
const getEmptyEl = () => $('jz-empty');

const populateSelect = (selId, items, allLabel) => {
  const sel = $(selId);
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = `${allLabel}`;
  items.forEach(v => {
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    if (v === cur) o.selected = true;
    sel.appendChild(o);
  });
};

const fmtDur = s => {
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
};

const voxBadge = voz => {
  const m = { Female: 'badge-mujer', Male: 'badge-hombre', Duo: 'badge-duo', Group: 'badge-grupo' };
  return `<span class="jz-badge ${m[voz] || 'badge-grupo'}">${voz || '—'}</span>`;
};

const render = () => {
  const tbody = getTbody();
  const countEl = getCountEl();
  const emptyEl = getEmptyEl();
  if (!tbody) return;
  if (countEl) countEl.textContent = filtered.length;
  if (!filtered.length) {
    tbody.innerHTML = '';
    if (emptyEl) emptyEl.style.display = 'block';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  tbody.innerHTML = filtered.map(t => `
    <tr data-id="${t.id}" class="${playingId === t.id ? 'jz-row--playing' : ''}">
      <td class="jz-cell-id">${t.id}</td>
      <td class="jz-cell-play">
        <button class="jz-play-btn ${playingId === t.id ? 'playing' : ''}"
          data-id="${t.id}" title="Preescucha">
          ${playingId === t.id
      ? `<svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`
      : `<svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`}
        </button>
      </td>
      <td class="jz-cell-cod">${t.codigoAuto || '—'}</td>
      <td class="jz-cell-title">${(t.title || '').toUpperCase()}</td>
      <td class="jz-cell-art">${(t.artist || '').toUpperCase()}</td>
      <td class="jz-cell-sc">${(t.soundCode || '').toUpperCase() || '—'}</td>
      <td class="jz-cell-pop">${t.popularity || '—'}</td>
      <td>${voxBadge(t.voz)}</td>
      <td style="font-family:var(--font-mono);font-size:11px;color:var(--jz-text3);text-align:right">${t.bpm || '—'}</td>
      <td class="jz-cell-dur">${fmtDur(t.duration || 0)}</td>
      <td>
        <div class="jz-actions">
          <button class="jz-btn jz-btn--sm jz-action-edit" data-id="${t.id}" title="Editar">✎</button>
          <button class="jz-btn jz-btn--sm jz-btn--danger jz-action-del" data-id="${t.id}" title="Eliminar">✕</button>
        </div>
      </td>
    </tr>
  `).join('');
};

const applyFilters = () => {
  const q = ($('jz-search')?.value || '').toLowerCase();
  const sc = $('jz-filter-sc')?.value || '';
  const pop = $('jz-filter-pop')?.value || '';
  const voz = $('jz-filter-voz')?.value || '';
  filtered = library.filter(t =>
    (!q || t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q)) &&
    (!sc || t.soundCode === sc) && (!pop || t.popularity === pop) && (!voz || t.voz === voz)
  );
  if (sortKey) {
    filtered.sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey];
      if (typeof va === 'string') va = va.toLowerCase();
      if (typeof vb === 'string') vb = vb.toLowerCase();
      return va < vb ? -sortDir : va > vb ? sortDir : 0;
    });
  }
  render();
};

const setPlayerInfo = track => {
  if ($('jz-player-title')) $('jz-player-title').textContent = track ? track.title : '—';
  if ($('jz-player-artist')) $('jz-player-artist').textContent = track ? track.artist : 'Sin pista cargada';
  if ($('jz-player-sc')) $('jz-player-sc').textContent = track ? (track.soundCode || '') : '';
  if ($('jz-time-total')) $('jz-time-total').textContent = track ? fmtDur(track.duration || 0) : '0:00';
  if ($('jz-timeline-fill')) $('jz-timeline-fill').style.width = '0%';
  if ($('jz-time-cur')) $('jz-time-cur').textContent = '0:00';
};
const stopPlayer = () => {
  if (audioEl) { audioEl.pause(); audioEl.src = ''; audioEl = null; }
  clearInterval(playerTimer);
  const prev = playingId; playingId = null;
  if (prev) getTbody()?.querySelector(`tr[data-id="${prev}"]`)?.classList.remove('jz-row--playing');
  if ($('jz-timeline-fill')) $('jz-timeline-fill').style.width = '0%';
  if ($('jz-time-cur')) $('jz-time-cur').textContent = '0:00';
  if ($('jz-player-icon')) $('jz-player-icon').innerHTML = '';
};
const updateTimeline = (cur, total) => {
  if ($('jz-timeline-fill')) $('jz-timeline-fill').style.width = `${(cur / total) * 100}%`;
  if ($('jz-time-cur')) $('jz-time-cur').textContent = fmtDur(Math.floor(cur));
};
const playTrack = track => {
  if (playingId === track.id) { stopPlayer(); setPlayerInfo(null); return; }
  stopPlayer(); playingId = track.id; setPlayerInfo(track);
  let streamUrl = `${API_BASE}/stream/unknown.mp3`;
  if (track.archivo_path) {
    const parts = track.archivo_path.replace(/\\/g, '/').split('/');
    streamUrl = `${API_BASE || 'http://localhost:8000'}/stream/${parts.slice(-2).join('/')}`;
  }
  audioEl = new Audio(streamUrl);
  audioEl.volume = 0.9;
  audioEl.play().catch(() => {
    let t = 0;
    playerTimer = setInterval(() => {
      t += 0.5;
      if (t >= track.duration) { clearInterval(playerTimer); stopPlayer(); setPlayerInfo(null); render(); return; }
      updateTimeline(t, track.duration);
    }, 500);
  });
  audioEl.addEventListener('timeupdate', () => { if (audioEl && !isNaN(audioEl.duration)) updateTimeline(audioEl.currentTime, track.duration); });
  audioEl.addEventListener('ended', () => { stopPlayer(); setPlayerInfo(null); render(); });
  audioEl.addEventListener('error', () => {
    let t = 0;
    playerTimer = setInterval(() => {
      t += 0.5;
      if (t >= track.duration) { clearInterval(playerTimer); stopPlayer(); setPlayerInfo(null); render(); return; }
      updateTimeline(t, track.duration);
    }, 500);
  });
  render();
  wsCmd('PREVIEW', { id: track.id });
};

const loadAudios = async () => {
  try {
    const res = await fetch(`${API_BASE}/api/v1/audios?limit=500&offset=0`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    library = (data.data || []).map(a => ({
      id: a.id, title: a.titulo || '—', artist: a.artista || '—', album: a.album || '',
      duration: a.duracion || 0, bpm: a.bpm || null, year: a.fecha_lanzamiento || null,
      soundCode: a.cat1 || '', popularity: a.cat2 || '', voz: a.voz || '',
      codigoAuto: a.id ? String(a.id).padStart(6, '0') : '—', archivo_path: a.archivo_path || '',
      cue_inicio: a.cue_inicio ?? a.intro ?? 0, cue_intro: a.cue_intro ?? a.intro ?? 0,
      cue_inicio_coro: a.cue_inicio_coro ?? 0, cue_final_coro: a.cue_final_coro ?? 0,
      cue_outro: a.cue_outro ?? a.outro ?? 0, cue_mezcla: a.cue_mezcla ?? 0,
      fade_in: a.fade_in ?? 0, fade_out: a.fade_out ?? 0,
      intro: a.intro ?? a.cue_intro ?? 0, outro: a.outro ?? a.cue_outro ?? 0,
    }));
    filtered = [...library];
    applyFilters();
    if (getCountEl()) getCountEl().textContent = library.length;
    const sbCount = document.getElementById('jz-sb-count-lib');
    if (sbCount) sbCount.textContent = library.length;
  } catch (err) {
    console.error('[BM·loadAudios]', err);
    showToast('No se pudo cargar la biblioteca', 'error');
  }
};

async function openModal(id = null) {
  const overlay = getOverlay();
  if (!overlay) return;
  if (typeof window.onixCategories?.syncSelects === 'function') window.onixCategories.syncSelects();
  if (typeof window.wfDestroy === 'function') window.wfDestroy();
  overlay.classList.add('active'); overlay.style.display = 'flex'; overlay.style.zIndex = '9000';
  if (typeof window.wfInit === 'function') {
    try { await window.wfInit(); } catch (e) { console.error('[Ónix] Error waveform:', e); }
  }
  const p = $('wf-panel'); if (p) p.style.display = 'block';
  editingId = id;
  const fechaEl = $('modal-fecha');
  if (fechaEl) fechaEl.textContent = id ? '—' : new Date().toLocaleDateString('es-ES', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  if (id) {
    const t = library.find(x => x.id === id); if (!t) return;
    if ($('modal-title-bar')) $('modal-title-bar').textContent = `${(t.artist || '').toUpperCase()} - ${(t.title || '').toUpperCase()}`;
    if ($('modal-artist')) $('modal-artist').value = (t.artist || '').toUpperCase();
    if ($('modal-title-input')) $('modal-title-input').value = (t.title || '').toUpperCase();
    if ($('modal-album')) $('modal-album').value = (t.album || '').toUpperCase();
    if ($('modal-cod-auto')) $('modal-cod-auto').value = t.codigoAuto || '';
    if ($('modal-year')) $('modal-year').value = t.year || '';
    if ($('modal-bpm')) $('modal-bpm').value = t.bpm || '';
    if ($('modal-popularity')) $('modal-popularity').value = t.popularity || '';
    if ($('modal-voz')) $('modal-voz').value = t.voz || '';
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'none';
    if ($('modal-archivo-path')) $('modal-archivo-path').value = t.archivo_path || '';
    if (typeof window.wfLoadUrl === 'function' && t.archivo_path) {
      const parts = t.archivo_path.replace(/\\/g, '/').split('/');
      const fileName = parts.pop(), folderName = parts.pop() || 'musica';
      const streamUrl = `${API_BASE || 'http://localhost:8000'}/stream/${folderName}/${fileName}`;
      const cues = { inicio: t.cue_inicio || 0, intro: t.cue_intro || 0, inicio_coro: t.cue_inicio_coro || 0, final_coro: t.cue_final_coro || 0, outro: t.cue_outro || 0, mezcla: t.cue_mezcla || 0 };
      window.wfLoadUrl(streamUrl, cues);
    }
  } else {
    if ($('modal-title-bar')) $('modal-title-bar').textContent = 'NUEVA CANCIÓN';
    ['modal-artist', 'modal-title-input', 'modal-album', 'modal-year', 'modal-bpm', 'modal-comentarios', 'modal-escritor', 'modal-compositor', 'modal-etiqueta', 'modal-cdkey', 'modal-barras', 'modal-archivo-path'].forEach(fid => { const el = $(fid); if (el) el.value = ''; });
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'block';
    if ($('modal-file-info')) $('modal-file-info').style.display = 'none';
    if ($('modal-activado')) $('modal-activado').checked = true;
    if ($('modal-congelado')) $('modal-congelado').checked = false;
  }
  overlay.classList.add('active'); overlay.style.display = 'flex'; overlay.style.zIndex = '9000';
  setTimeout(() => { const inp = $('modal-artist'); if (inp) inp.focus(); }, 100);
}

function closeModal() {
  const overlay = getOverlay(); if (!overlay) return;
  overlay.classList.remove('active'); overlay.style.display = '';
  editingId = null; selectedFile = null;
  if (typeof window.wfDestroy === 'function') window.wfDestroy();
}

async function saveModal() {
  const titleVal = $('modal-title-input')?.value.trim();
  const artistVal = $('modal-artist')?.value.trim();
  if (!titleVal || !artistVal) { showToast('Título y artista son obligatorios', 'error'); return; }
  if (!editingId && !selectedFile) { showToast('Selecciona un archivo de audio', 'error'); return; }
  let markers = { inicio: 0, intro: 0, inicio_coro: 0, final_coro: 0, outro: 0, mezcla: 0, fade_in: 0, fade_out: 0 };
  if (typeof window.wfGetMarkers === 'function') markers = window.wfGetMarkers();
  const btnSave = $('jz-modal-save');
  if (btnSave) { btnSave.disabled = true; btnSave.textContent = 'Guardando…'; }
  try {
    let res;
    if (editingId) {
      const toUpper = v => (v && typeof v === 'string') ? v.trim().toUpperCase() : v;
      const payload = { titulo: titleVal, artista: artistVal, album: $('modal-album')?.value.trim() || null, fecha_lanzamiento: $('modal-year')?.value.trim() || null, bpm: $('modal-bpm')?.value ? Number($('modal-bpm').value) : null, cat1: toUpper($('modal-sc1')?.value || $('modal-sc2')?.value) || null, cat2: toUpper($('modal-popularity')?.value) || null, voz: toUpper($('modal-voz')?.value) || null, cue_inicio: markers.inicio ?? 0, cue_intro: markers.intro ?? 0, cue_inicio_coro: markers.inicio_coro ?? 0, cue_final_coro: markers.final_coro ?? 0, cue_mezcla: markers.mezcla ?? 0, fade_in: markers.fade_in ?? 0, fade_out: markers.fade_out ?? 0, intro: markers.intro ?? 0, outro: markers.mezcla ?? 0 };
      Object.keys(payload).forEach(k => { if (payload[k] === null || payload[k] === '') delete payload[k]; });
      res = await fetch(`${API_BASE}/api/v1/audios/${editingId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    } else {
      const formData = new FormData();
      formData.append('titulo', titleVal); formData.append('artista', artistVal);
      const opt = { album: $('modal-album')?.value.trim(), bpm: $('modal-bpm')?.value, fecha_lanzamiento: $('modal-year')?.value.trim(), cat1: $('modal-sc1')?.value || $('modal-sc2')?.value, cat2: $('modal-popularity')?.value, voz: $('modal-voz')?.value };
      Object.entries(opt).forEach(([k, v]) => { if (v) formData.append(k, v); });
      formData.append('cue_inicio', markers.inicio ?? 0); formData.append('cue_intro', markers.intro ?? 0); formData.append('cue_inicio_coro', markers.inicio_coro ?? 0); formData.append('cue_final_coro', markers.final_coro ?? 0); formData.append('cue_mezcla', markers.mezcla ?? 0); formData.append('fade_in', markers.fade_in ?? 0); formData.append('fade_out', markers.fade_out ?? 0); formData.append('intro', markers.intro ?? 0); formData.append('outro', markers.mezcla ?? 0); formData.append('file', selectedFile);
      res = await fetch(`${API_BASE}/api/v1/audios`, { method: 'POST', body: formData });
    }
    const data = await res.json();
    if (!res.ok) { const detail = data.detail; showToast(typeof detail === 'object' ? (detail.mensaje || JSON.stringify(detail)) : (detail || `Error ${res.status}`), 'error'); return; }
    wsCmd('library_updated', { id: data.data?.id, titulo: titleVal, action: editingId ? 'update' : 'create' });
    closeModal(); await loadAudios();
    showToast(editingId ? `✓ Actualizado: ${titleVal}` : `✓ Cargado: ${titleVal}`, 'success');
  } catch (err) {
    showToast('Error de conexión con la API', 'error'); console.error('[BM·save]', err);
  } finally {
    if (btnSave) { btnSave.disabled = false; btnSave.textContent = 'OK'; }
  }
}

const handleFile = file => {
  if (!file || !file.type.startsWith('audio/')) { showToast('El archivo seleccionado no es audio', 'error'); return; }
  selectedFile = file;
  const info = $('modal-file-info');
  if (info) { info.textContent = `✓ ${file.name}  ·  ${(file.size / 1048576).toFixed(1)} MB`; info.style.display = 'block'; }
  const ap = $('modal-archivo-path'); if (ap && !ap.value) ap.value = file.name;
  const ti = $('modal-title-input'); if (ti && !ti.value) ti.value = file.name.replace(/\.[^.]+$/, '');
  if (typeof window.wfLoadFile === 'function') window.wfLoadFile(file);
  wsCmd('FILE_SELECTED', { name: file.name, size: file.size });
};

const initApp = async () => {
  if (!window._waitWFStarted) {
    window._waitWFStarted = true;
    const waitWF = () => {
      if (document.getElementById('waveform') && typeof window.wfInit === 'function') { window.wfInit(); console.log('[BM] WaveSurfer inicializado'); }
      else { setTimeout(waitWF, 80); }
    };
    waitWF();
  }
  setStatus('Cargando schema…');
  try { const r = await fetch(`${API_BASE}/api/v1/config/biblioteca/schema`); if (r.ok) schema = await r.json(); } catch (e) { console.warn('[BM·schema]', e.message); }
  setPlayerInfo(null); setStatus('Cargando biblioteca…');
  await loadAudios();
  const n = library.length;
  setStatus(`Sistema listo · ${n} pista${n !== 1 ? 's' : ''} cargada${n !== 1 ? 's' : ''}`);
  showToast(`Biblioteca Musical lista · ${n} pistas`, 'success');
};

const wsCmd = (cmd, data = {}) => {
  const msg = { module: 'biblioteca-musical', cmd, ts: Date.now(), data };
  if (window.ShellWS?.send) window.ShellWS.send(JSON.stringify(msg));
  else console.info('[BM·WS]', msg);
};
const setStatus = msg => { const el = getStatusMsg(); if (el) el.textContent = msg; };
const showToast = (msg, type = 'info') => {
  const icons = { success: '✓', error: '✕', info: '·' };
  const t = document.createElement('div');
  t.className = `jz-toast jz-toast--${type}`;
  t.innerHTML = `${icons[type] || '·'}${msg}`;
  const container = $('jz-toasts'); if (container) container.appendChild(t);
  setTimeout(() => { t.style.cssText += 'opacity:0;transform:translateX(18px);transition:.3s'; setTimeout(() => t.remove(), 350); }, 2800);
};

document.addEventListener('click', e => {
  if (e.target.closest('#jz-btn-add') || e.target.closest('#jz-sb-nueva')) { openModal(); return; }
  if (e.target.closest('#jz-modal-close')) { closeModal(); return; }
  if (e.target.closest('#jz-modal-cancel')) { closeModal(); return; }
  if (e.target.closest('#jz-modal-save')) { saveModal(); return; }
  if (e.target.closest('#jz-sb-lote')) { showToast('Carga por lote: próximamente', 'info'); return; }
  if (e.target.closest('#jz-sb-eliminar')) { showToast('Selecciona una canción de la lista para eliminarla', 'info'); return; }
  if (e.target.closest('#jz-task-artistas')) { showToast('Editar Artistas: próximamente', 'info'); return; }
  if (e.target.closest('#jz-task-categorias')) { if (window.onixCategories?.open) window.onixCategories.open(); else showToast('Editar Categorías: módulo no cargado', 'error'); return; }
  if (e.target.closest('#jz-task-estadisticas')) { showToast(`Estadísticas · ${library.length} pistas`, 'info'); return; }
  if (e.target.closest('#jz-task-exportar')) { showToast('Exportar BD: próximamente', 'info'); return; }
  if (e.target.closest('#jz-task-integridad')) { showToast('Chequeo de integridad: próximamente', 'info'); return; }
  if (e.target.closest('#jz-task-difusion')) { showToast('Análisis de difusión: próximamente', 'info'); return; }
  const overlay = getOverlay();
  /* Bloqueo del cierre por clic en el overlay (Desactivado por seguridad) */
  /* if (overlay && e.target === overlay) { closeModal(); return; } */
  const pb = e.target.closest('.jz-play-btn'); if (pb && pb.closest('#jz-tbody')) { const track = library.find(t => t.id === +pb.dataset.id); if (track) playTrack(track); return; }
  const eb = e.target.closest('.jz-action-edit'); if (eb) { openModal(+eb.dataset.id); return; }
  const db = e.target.closest('.jz-action-del');
  if (db) {
    const id = +db.dataset.id, track = library.find(t => t.id === id);
    if (!track || !confirm(`¿Eliminar "${track.title}"?`)) return;
    if (playingId === id) stopPlayer();
    fetch(`${API_BASE}/api/v1/audios/${id}`, { method: 'DELETE' })
      .then(async res => { if (!res.ok) { const d = await res.json().catch(() => ({})); showToast(d.detail || `Error ${res.status}`, 'error'); return; } wsCmd('DELETE', { id }); showToast(`Eliminado: ${track.title}`, 'error'); loadAudios(); })
      .catch(() => showToast('Error de conexión al eliminar', 'error'));
    return;
  }
  const th = e.target.closest('.jz-table thead th[data-sort]');
  if (th) { const key = th.dataset.sort; if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = 1; } document.querySelectorAll('.jz-table thead th').forEach(h => h.classList.remove('sort-asc', 'sort-desc')); th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc'); applyFilters(); return; }
  if (e.target.closest('#jz-player-toggle')) { if (playingId) stopPlayer(); return; }
  const tl = e.target.closest('#jz-timeline'); if (tl && playingId) { const rect = tl.getBoundingClientRect(); const pct = (e.clientX - rect.left) / rect.width; if (audioEl?.duration) audioEl.currentTime = pct * audioEl.duration; return; }
  if (e.target.closest('#jz-dropzone')) { if (e.target.id === 'modal-file') return; const fi = $('modal-file'); if (fi) fi.click(); return; }
});

const FIDS = new Set(['jz-search', 'jz-filter-sc', 'jz-filter-pop', 'jz-filter-voz']);
document.addEventListener('input', e => { if (FIDS.has(e.target.id)) applyFilters(); });
document.addEventListener('change', e => { if (FIDS.has(e.target.id)) applyFilters(); if (e.target.id === 'modal-file' && e.target.files?.[0]) handleFile(e.target.files[0]); });
document.addEventListener('dragover', e => { const dz = e.target.closest('#jz-dropzone'); if (dz) { e.preventDefault(); dz.classList.add('drag-over'); } });
document.addEventListener('dragleave', e => { const dz = e.target.closest('#jz-dropzone'); if (dz) dz.classList.remove('drag-over'); });
document.addEventListener('drop', e => { const dz = e.target.closest('#jz-dropzone'); if (dz) { e.preventDefault(); dz.classList.remove('drag-over'); handleFile(e.dataTransfer.files[0]); } });
document.addEventListener('keydown', e => {
  const isOpen = getOverlay()?.classList.contains('active');
  /* Bloqueo del cierre por tecla Escape (Desactivado por seguridad) */
  /* if (e.key === 'Escape' && isOpen) { closeModal(); return; } */
  if (e.key === 'n' && e.ctrlKey && !isOpen) { e.preventDefault(); openModal(); return; }
});

function populateUploadSelects() {
  if (typeof window.onixCategories === 'undefined') return;
  if (typeof window.onixCategories.syncSelects === 'function') window.onixCategories.syncSelects();
}

window.openModal = openModal;
window.closeModal = closeModal;
window.saveModal = saveModal;
window.populateUploadSelects = populateUploadSelects;
console.log('[ÓNIX FM] admin-app.js cargado — funciones vinculadas a window.');

const CategoriesModule = (function () {
  const LS_KEY = 'onix_categories';
  const CATEGORY_KEYS = ['soundCode', 'popularity', 'era', 'voz', 'propiedades'];
  const DEFAULTS = {
    soundCode: { name: 'Sound Code', canRename: true, items: [{ label: 'POP', color: '#8e44ad', comment: '' }, { label: 'ROCK', color: '#6b7a1e', comment: '' }, { label: 'DANCE', color: '#cc0000', comment: '' }, { label: 'ALTERNATIVE', color: '#808080', comment: '' }] },
    popularity: { name: 'Popularity', canRename: true, items: [{ label: 'HOT CURRENT', color: '#e67e22', comment: 'Default Category' }, { label: 'CURRENT', color: '#d35400', comment: '' }, { label: 'OLDIES 1', color: '#2980b9', comment: '' }, { label: 'OLDIES 2', color: '#1a5276', comment: '' }] },
    era: { name: 'Era', canRename: true, items: [{ label: '60s', color: '#4a4a6a', comment: 'Default Category' }, { label: '70s', color: '#4a5a3a', comment: 'Default Category' }, { label: '80s', color: '#5a3a5a', comment: 'Default Category' }, { label: '90s', color: '#3a5a6a', comment: 'Default Category' }, { label: '2000s', color: '#5a4a2a', comment: '' }, { label: '2010s', color: '#2a4a5a', comment: '' }, { label: '2020s', color: '#3a3a5a', comment: '' }] },
    voz: { name: 'Voz', canRename: false, items: [{ label: 'Female', color: '#8e44ad', comment: 'Default Category' }, { label: 'Male', color: '#555555', comment: 'Default Category' }, { label: 'Duo', color: '#555555', comment: 'Default Category' }, { label: 'Group', color: '#555555', comment: 'Default Category' }, { label: 'Collaboration', color: '#555555', comment: 'Default Category' }] },
    propiedades: { name: 'Propiedades', canRename: false, items: [] }
  };
  let categories = {}, activeKey = 'soundCode', selectedIdx = -1;
  function load() { try { const stored = localStorage.getItem(LS_KEY); if (stored) { const parsed = JSON.parse(stored); CATEGORY_KEYS.forEach(k => { categories[k] = Object.assign({}, DEFAULTS[k], parsed[k] || {}); if (!Array.isArray(categories[k].items)) categories[k].items = []; }); } else { reset(); } } catch (e) { reset(); } }
  function save() { localStorage.setItem(LS_KEY, JSON.stringify(categories)); }
  function reset() { CATEGORY_KEYS.forEach(k => { categories[k] = JSON.parse(JSON.stringify(DEFAULTS[k])); }); }
  const SELECT_MAP = { soundCode: ['modal-sc1', 'modal-sc2', 'modal-sc3', 'jz-filter-sc'], popularity: ['modal-popularity', 'jz-filter-pop'], era: ['modal-era'], voz: ['modal-voz', 'jz-filter-voz'], propiedades: [] };
  const SELECT_LABELS = { 'modal-sc1': '— SC1 —', 'modal-sc2': '— SC2 —', 'modal-sc3': '— SC3 —', 'jz-filter-sc': 'Sound Code', 'modal-popularity': '— —', 'jz-filter-pop': 'Popularity', 'modal-era': '—', 'modal-voz': '—', 'jz-filter-voz': 'Voz' };
  function sync() { CATEGORY_KEYS.forEach(key => { const ids = SELECT_MAP[key] || [], items = categories[key]?.items || []; ids.forEach(id => { const el = document.getElementById(id); if (!el) return; const cur = el.value; el.innerHTML = `${SELECT_LABELS[id] || '—'}`; items.forEach(it => { const o = document.createElement('option'); o.value = it.label; o.textContent = it.label; el.appendChild(o); }); if (cur && [...el.options].some(o => o.value === cur)) el.value = cur; }); }); }
  function renderSidebar() { const sb = document.getElementById('cat-sidebar'); if (!sb) return; sb.innerHTML = CATEGORY_KEYS.map(key => `♪${categories[key].name}`).join(''); if (!categories[activeKey]?.canRename) { const n = document.createElement('div'); n.className = 'cat-sidebar__note'; n.textContent = 'Esta categoría no puede ser renombrada.'; sb.appendChild(n); } }
  function renderItems() { const list = document.getElementById('cat-items-list'); if (!list) return; const items = categories[activeKey]?.items || []; if (!items.length) { list.innerHTML = '— Sin ítems —'; return; } list.innerHTML = items.map((it, i) => `${it.label}${it.comment || ''}`).join(''); }
  function updateHeader() { const cat = categories[activeKey], p = document.getElementById('cat-panel-prefix'), n = document.getElementById('cat-panel-name'); if (p) p.textContent = cat.canRename ? 'Cambiar Categoría por' : 'Cambiar'; if (n) n.textContent = cat.name; }
  const publicApi = {
    init() { load(); sync(); },
    syncSelects() { sync(); },
    open() { const overlay = document.getElementById('cat-overlay'); if (!overlay) return; activeKey = 'soundCode'; selectedIdx = -1; overlay.classList.add('active'); renderSidebar(); renderItems(); updateHeader(); },
    close() { document.getElementById('cat-overlay')?.classList.remove('active'); },
    select(key) { activeKey = key; selectedIdx = -1; renderSidebar(); renderItems(); updateHeader(); },
    add() { const label = document.getElementById('cat-new-name')?.value.trim().toUpperCase(); if (!label) return; if (categories[activeKey].items.some(i => i.label === label)) return; categories[activeKey].items.push({ label, color: document.getElementById('cat-new-color')?.value || '#8e44ad', comment: document.getElementById('cat-new-comment')?.value.trim() || '' }); save(); sync(); renderItems(); document.getElementById('cat-add-form')?.classList.remove('visible'); },
    openEdit() { if (selectedIdx < 0) { showToast('Selecciona un ítem para editar', 'info'); return; } const item = categories[activeKey].items[selectedIdx]; if (!item) return; document.getElementById('cat-add-form')?.classList.remove('visible'); const nameEl = document.getElementById('cat-edit-name'), colorEl = document.getElementById('cat-edit-color'), commentEl = document.getElementById('cat-edit-comment'); if (nameEl) nameEl.value = item.label; if (colorEl) colorEl.value = item.color || '#8e44ad'; if (commentEl) commentEl.value = item.comment || ''; document.getElementById('cat-edit-form')?.classList.add('visible'); if (nameEl) nameEl.focus(); },
    confirmEdit() { if (selectedIdx < 0) return; const newLabel = document.getElementById('cat-edit-name')?.value.trim().toUpperCase(); if (!newLabel) { showToast('El nombre no puede estar vacío', 'error'); return; } const item = categories[activeKey].items[selectedIdx]; if (!item) return; const isDuplicate = categories[activeKey].items.some((it, i) => i !== selectedIdx && it.label === newLabel); if (isDuplicate) { showToast('Ya existe un ítem con ese nombre', 'error'); return; } item.label = newLabel; item.color = document.getElementById('cat-edit-color')?.value || item.color; item.comment = document.getElementById('cat-edit-comment')?.value.trim() ?? item.comment; save(); sync(); renderItems(); document.getElementById('cat-edit-form')?.classList.remove('visible'); showToast(`✓ Ítem actualizado: ${newLabel}`, 'success'); },
    delete() { if (selectedIdx < 0) return; categories[activeKey].items.splice(selectedIdx, 1); selectedIdx = -1; save(); sync(); renderItems(); },
    move(dir) { const items = categories[activeKey].items; if (selectedIdx < 0 || selectedIdx + dir < 0 || selectedIdx + dir >= items.length) return;[items[selectedIdx], items[selectedIdx + dir]] = [items[selectedIdx + dir], items[selectedIdx]]; selectedIdx += dir; save(); sync(); renderItems(); }
  };
  document.addEventListener('click', e => {
    const t = e.target;
    if (t.closest('.cat-sidebar__item')) { document.getElementById('cat-add-form')?.classList.remove('visible'); document.getElementById('cat-edit-form')?.classList.remove('visible'); publicApi.select(t.closest('.cat-sidebar__item').dataset.key); }
    if (t.closest('.cat-item-row')) { selectedIdx = parseInt(t.closest('.cat-item-row').dataset.idx); renderItems(); }
    if (t.closest('#cat-btn-agregar')) { document.getElementById('cat-edit-form')?.classList.remove('visible'); document.getElementById('cat-add-form')?.classList.add('visible'); }
    if (t.closest('#cat-confirm-add')) publicApi.add();
    if (t.closest('#cat-cancel-add')) document.getElementById('cat-add-form')?.classList.remove('visible');
    if (t.closest('#cat-btn-editar')) publicApi.openEdit();
    if (t.closest('#cat-confirm-edit')) publicApi.confirmEdit();
    if (t.closest('#cat-cancel-edit')) document.getElementById('cat-edit-form')?.classList.remove('visible');
    if (t.closest('#cat-btn-eliminar')) publicApi.delete();
    if (t.closest('#cat-move-up')) publicApi.move(-1);
    if (t.closest('#cat-move-down')) publicApi.move(1);
    if (t.closest('#cat-close-btn') || t.closest('#cat-btn-ok')) publicApi.close();
    if (t.id === 'cat-overlay') publicApi.close();
  });
  return publicApi;
})();

CategoriesModule.init();
window.onixCategories = CategoriesModule;
initApp();
