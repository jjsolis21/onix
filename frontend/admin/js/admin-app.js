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
          dashboard:   '01-estado-global',
          biblioteca:  '02-biblioteca-musical',
          pautas:      '03-programacion-pautas',
          cartuchera:  '04-editor-cartuchera',
          logs:        '05-historial-emision',
          engine:      '06-motor-audio',
        };
        this.loadSection(routes[module]);
      });
    });
  },
  async loadSection(sectionName) {
    const container = document.getElementById(this.config.containerId);
    if (!container) return;
    try {
      const url = `${this.config.basePath}${sectionName}/${sectionName.split('-').slice(1).join('-')}.html`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`No se pudo cargar: ${sectionName}`);
      container.innerHTML = await response.text();
      const titleEl = document.getElementById('module-title');
      if (titleEl) titleEl.textContent = sectionName.replace(/-/g, ' ').toUpperCase().slice(3);
    } catch (error) {
      container.innerHTML = `<div class="error">Error al cargar pieza: ${error.message}</div>`;
    }
  },
};
document.addEventListener('DOMContentLoaded', () => Shell.init());

/* ════════════════════════════════════════════════════════════════════════════
   BIBLIOTECA MUSICAL
════════════════════════════════════════════════════════════════════════════ */
'use strict';

/* ── §1 · Schema de categorías ────────────────────────────────────────────── */
let schema = { categorias: [] };
const getCatValues = n => { const c = schema.categorias.find(x => x.nombre_interno === n); return c ? c.valores.map(v => v.valor) : []; };

/* ── §2 · Estado ──────────────────────────────────────────────────────────── */
let library      = [];
let filtered     = [];
let sortKey      = null;
let sortDir      = 1;
let editingId    = null;
let playingId    = null;
let audioEl      = null;
let playerTimer  = null;
let selectedFile = null;

/* ── §3 · Helpers DOM (sin colisión con onix-waveform.js) ───────────────── */
const $            = id => document.getElementById(id);
const getOverlay   = () => $('jz-overlay');
const getTbody     = () => $('jz-tbody');
const getCountEl   = () => $('jz-count');
const getStatusMsg = () => $('jz-status-msg');
const getEmptyEl   = () => $('jz-empty');

/* ── §4 · Populación de selects ──────────────────────────────────────────── */
const populateSelect = (selId, items, allLabel) => {
  const sel = $(selId);
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = `<option value="">${allLabel}</option>`;
  items.forEach(v => {
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    if (v === cur) o.selected = true;
    sel.appendChild(o);
  });
};

/* ── §5 · Renderizado de tabla ───────────────────────────────────────────── */
const fmtDur = s => {
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
};

const voxBadge = voz => {
  const m = { Female: 'badge-mujer', Male: 'badge-hombre', Duo: 'badge-duo', Group: 'badge-grupo' };
  return `<span class="jz-badge ${m[voz] || 'badge-grupo'}">${voz || '—'}</span>`;
};

const render = () => {
  const tbody   = getTbody();
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
      <td class="jz-cell-title">${t.title}</td>
      <td class="jz-cell-art">${t.artist}</td>
      <td class="jz-cell-sc">${t.soundCode || '—'}</td>
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

/* ── §6 · Filtrado y ordenamiento ────────────────────────────────────────── */
const applyFilters = () => {
  const q   = ($('jz-search')?.value || '').toLowerCase();
  const sc  = $('jz-filter-sc')?.value  || '';
  const pop = $('jz-filter-pop')?.value || '';
  const voz = $('jz-filter-voz')?.value || '';
  filtered = library.filter(t =>
    (!q   || t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q)) &&
    (!sc  || t.soundCode  === sc)  &&
    (!pop || t.popularity === pop) &&
    (!voz || t.voz        === voz)
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

/* ── §7 · Mini Player ────────────────────────────────────────────────────── */
const setPlayerInfo = track => {
  if ($('jz-player-title'))  $('jz-player-title').textContent  = track ? track.title  : '—';
  if ($('jz-player-artist')) $('jz-player-artist').textContent = track ? track.artist : 'Sin pista cargada';
  if ($('jz-player-sc'))     $('jz-player-sc').textContent     = track ? (track.soundCode || '') : '';
  if ($('jz-time-total'))    $('jz-time-total').textContent    = track ? fmtDur(track.duration || 0) : '0:00';
  if ($('jz-timeline-fill')) $('jz-timeline-fill').style.width = '0%';
  if ($('jz-time-cur'))      $('jz-time-cur').textContent      = '0:00';
};
const stopPlayer = () => {
  if (audioEl) { audioEl.pause(); audioEl.src = ''; audioEl = null; }
  clearInterval(playerTimer);
  const prev = playingId; playingId = null;
  if (prev) getTbody()?.querySelector(`tr[data-id="${prev}"]`)?.classList.remove('jz-row--playing');
  if ($('jz-timeline-fill')) $('jz-timeline-fill').style.width = '0%';
  if ($('jz-time-cur'))      $('jz-time-cur').textContent = '0:00';
  if ($('jz-player-icon'))   $('jz-player-icon').innerHTML = '<polygon points="5,3 19,12 5,21"/>';
};
const updateTimeline = (cur, total) => {
  if ($('jz-timeline-fill')) $('jz-timeline-fill').style.width = `${(cur / total) * 100}%`;
  if ($('jz-time-cur'))      $('jz-time-cur').textContent = fmtDur(Math.floor(cur));
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
  audioEl.addEventListener('timeupdate', () => {
    if (audioEl && !isNaN(audioEl.duration)) updateTimeline(audioEl.currentTime, track.duration);
  });
  audioEl.addEventListener('ended', () => { stopPlayer(); setPlayerInfo(null); render(); });
  audioEl.addEventListener('error',  () => {
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

/* ── §8 · Carga de audios ─────────────────────────────────────────────────── */
const loadAudios = async () => {
  try {
    const res = await fetch(`${API_BASE}/api/v1/audios?limit=500&offset=0`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    library = (data.data || []).map(a => ({
      id:           a.id,
      title:        a.titulo         || '—',
      artist:       a.artista        || '—',
      album:        a.album          || '',
      duration:     a.duracion       || 0,
      bpm:          a.bpm            || null,
      year:         a.fecha_lanzamiento || null,
      soundCode:    a.cat1           || '',
      popularity:   a.cat2           || '',
      voz:          a.voz            || '',
      codigoAuto:   a.id ? String(a.id).padStart(6, '0') : '—',
      archivo_path: a.archivo_path   || '',
      /* Cue points */
      cue_inicio:      a.cue_inicio      || 0,
      cue_intro:       a.cue_intro       || a.intro || 0,
      cue_inicio_coro: a.cue_inicio_coro || 0,
      cue_final_coro:  a.cue_final_coro  || 0,
      cue_outro:       a.cue_outro       || a.outro || 0,
      cue_mezcla:      a.cue_mezcla      || a.mix   || 0,
    }));
    filtered = [...library];
    applyFilters();
    if (getCountEl()) getCountEl().textContent = library.length;
    /* Sync sidebar count badge */
    const sbCount = document.getElementById('jz-sb-count-lib');
    if (sbCount) sbCount.textContent = library.length;
  } catch (err) {
    console.error('[BM·loadAudios]', err);
    showToast('No se pudo cargar la biblioteca', 'error');
  }
};

/* ── §9 · MODAL: ABRIR ────────────────────────────────────────────────────── */
function openModal(id = null) {
  const overlay = getOverlay();
  if (!overlay) return;
  editingId = id;

  /* Fecha de ingreso */
  const fechaEl = $('modal-fecha');
  if (fechaEl) fechaEl.textContent = id ? '—' : new Date().toLocaleDateString('es-ES', { weekday:'long', year:'numeric', month:'long', day:'numeric' });

  if (id) {
    const t = library.find(x => x.id === id);
    if (!t) return;
    if ($('modal-title-bar'))      $('modal-title-bar').textContent      = `${t.artist.toUpperCase()} - ${t.title.toUpperCase()}`;
    if ($('modal-artist'))         $('modal-artist').value               = t.artist;
    if ($('modal-title-input'))    $('modal-title-input').value          = t.title;
    if ($('modal-album'))          $('modal-album').value                = t.album  || '';
    if ($('modal-cod-auto'))       $('modal-cod-auto').value             = t.codigoAuto || '';
    if ($('modal-year'))           $('modal-year').value                 = t.year   || '';
    if ($('modal-bpm'))            $('modal-bpm').value                  = t.bpm    || '';
    if ($('modal-popularity'))     $('modal-popularity').value           = t.popularity || '';
    if ($('modal-voz'))            $('modal-voz').value                  = t.voz    || '';
    if ($('modal-dropzone-wrap'))  $('modal-dropzone-wrap').style.display = 'none';
    if ($('modal-archivo-path'))   $('modal-archivo-path').value          = t.archivo_path || '';
  } else {
    if ($('modal-title-bar'))     $('modal-title-bar').textContent     = 'NUEVA CANCIÓN';
    ['modal-artist','modal-title-input','modal-album','modal-year','modal-bpm',
     'modal-comentarios','modal-escritor','modal-compositor','modal-etiqueta',
     'modal-cdkey','modal-barras','modal-archivo-path'].forEach(fid => {
       const el = $(fid); if (el) el.value = '';
     });
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'block';
    if ($('modal-file-info'))     $('modal-file-info').style.display    = 'none';
    if ($('modal-activado'))      $('modal-activado').checked           = true;
    if ($('modal-congelado'))     $('modal-congelado').checked          = false;
  }

  overlay.classList.add('active');
  overlay.style.display  = 'flex';
  overlay.style.zIndex   = '9000';
  setTimeout(() => { const inp = $('modal-artist'); if (inp) inp.focus(); }, 100);
}

/* ── §10 · MODAL: CERRAR ─────────────────────────────────────────────────── */
function closeModal() {
  const overlay = getOverlay();
  if (!overlay) return;
  overlay.classList.remove('active');
  overlay.style.display = '';
  editingId = null; selectedFile = null;
  if (typeof window.wfDestroy === 'function') window.wfDestroy();
}

/* ── §11 · MODAL: GUARDAR ────────────────────────────────────────────────── */
async function saveModal() {
  const titleVal  = $('modal-title-input')?.value.trim();
  const artistVal = $('modal-artist')?.value.trim();
  if (!titleVal || !artistVal) { showToast('Título y artista son obligatorios', 'error'); return; }
  if (!editingId && !selectedFile) { showToast('Selecciona un archivo de audio', 'error'); return; }

  const formData = new FormData();
  formData.append('titulo',  titleVal);
  formData.append('artista', artistVal);

  const optionals = {
    album:             $('modal-album')?.value.trim(),
    bpm:               $('modal-bpm')?.value,
    fecha_lanzamiento: $('modal-year')?.value.trim(),
    cat1:              $('modal-sc1')?.value || $('modal-sc2')?.value,
    cat2:              $('modal-popularity')?.value,
    voz:               $('modal-voz')?.value,
    notas:             $('modal-comentarios')?.value.trim(),
    activado:          $('modal-activado')?.checked ? '1' : '0',
  };
  Object.entries(optionals).forEach(([k, v]) => { if (v) formData.append(k, v); });

  /* Cue points de los 6 marcadores */
  if (typeof window.wfGetMarkers === 'function') {
    const mk = window.wfGetMarkers();
    formData.append('cue_inicio',      mk.inicio      || 0);
    formData.append('cue_intro',       mk.intro       || 0);
    formData.append('cue_inicio_coro', mk.inicio_coro || 0);
    formData.append('cue_final_coro',  mk.final_coro  || 0);
    formData.append('cue_outro',       mk.outro       || 0);
    formData.append('cue_mezcla',      mk.mezcla      || 0);
    formData.append('fade_in',         mk.fade_in     || 0);
    formData.append('fade_out',        mk.fade_out    || 0);
    /* Retrocompatibilidad con campos intro/outro simples */
    formData.append('intro', mk.intro || 0);
    formData.append('outro', mk.outro || 0);
    formData.append('mix',   mk.mezcla || 0);
  }

  if (!editingId && selectedFile) {
    formData.append('file', selectedFile);
  }

  const btnSave = $('jz-modal-save');
  if (btnSave) { btnSave.disabled = true; btnSave.textContent = 'Guardando…'; }

  try {
    let res;
    if (editingId) {
      const json = {};
      formData.forEach((v, k) => json[k] = v);
      res = await fetch(`${API_BASE}/api/v1/audios/${editingId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(json),
      });
    } else {
      res = await fetch(`${API_BASE}/api/v1/audios`, { method: 'POST', body: formData });
    }
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail;
      showToast(typeof detail === 'object' ? (detail.mensaje || JSON.stringify(detail)) : (detail || `Error ${res.status}`), 'error');
      return;
    }
    wsCmd(editingId ? 'UPDATE' : 'UPLOAD', { id: data.data?.id, titulo: titleVal });
    closeModal();
    await loadAudios();
    showToast(editingId ? `Actualizado: ${titleVal}` : `Cargado: ${titleVal}`, 'success');
  } catch (err) {
    showToast('Error de conexión con la API', 'error');
    console.error('[BM·save]', err);
  } finally {
    if (btnSave) { btnSave.disabled = false; btnSave.textContent = 'OK'; }
  }
}

/* ── §12 · Manejo de archivo ─────────────────────────────────────────────── */
const handleFile = file => {
  if (!file || !file.type.startsWith('audio/')) { showToast('El archivo seleccionado no es audio', 'error'); return; }
  selectedFile = file;
  const info = $('modal-file-info');
  if (info) { info.textContent = `✓ ${file.name}  ·  ${(file.size / 1048576).toFixed(1)} MB`; info.style.display = 'block'; }
  const ap = $('modal-archivo-path');
  if (ap && !ap.value) ap.value = file.name;
  const ti = $('modal-title-input');
  if (ti && !ti.value) ti.value = file.name.replace(/\.[^.]+$/, '');
  if (typeof window.wfLoadFile === 'function') window.wfLoadFile(file);
  wsCmd('FILE_SELECTED', { name: file.name, size: file.size });
};

/* ── §13 · Inicialización ────────────────────────────────────────────────── */
const initApp = async () => {
  /* Inicializar WaveSurfer — _waitWFStarted evita loops paralelos
     si initApp() se llamara más de una vez.                        */
  if (!window._waitWFStarted) {
    window._waitWFStarted = true;
    const waitWF = () => {
      if (document.getElementById('waveform') && typeof window.wfInit === 'function') {
        window.wfInit();
        console.log('[BM] WaveSurfer inicializado');
      } else {
        setTimeout(waitWF, 80);
      }
    };
    waitWF();
  }

  setStatus('Cargando schema…');
  try {
    const r = await fetch(`${API_BASE}/api/v1/config/biblioteca/schema`);
    if (r.ok) schema = await r.json();
  } catch (e) { console.warn('[BM·schema]', e.message); }

  setPlayerInfo(null);
  setStatus('Cargando biblioteca…');
  await loadAudios();
  const n = library.length;
  setStatus(`Sistema listo · ${n} pista${n !== 1 ? 's' : ''} cargada${n !== 1 ? 's' : ''}`);
  showToast(`Biblioteca Musical lista · ${n} pistas`, 'success');
};

/* ── §14 · WS Bridge & Toast ─────────────────────────────────────────────── */
const wsCmd = (cmd, data = {}) => {
  const msg = { module: 'biblioteca-musical', cmd, ts: Date.now(), data };
  if (window.ShellWS?.send) { window.ShellWS.send(JSON.stringify(msg)); }
  else { console.info('[BM·WS]', msg); }
};

const setStatus = msg => {
  const el = getStatusMsg(); if (el) el.textContent = msg;
};

const showToast = (msg, type = 'info') => {
  const icons = { success: '✓', error: '✕', info: '·' };
  const t = document.createElement('div');
  t.className = `jz-toast jz-toast--${type}`;
  t.innerHTML = `<span>${icons[type] || '·'}</span>${msg}`;
  const container = $('jz-toasts');
  if (container) container.appendChild(t);
  setTimeout(() => {
    t.style.cssText += 'opacity:0;transform:translateX(18px);transition:.3s';
    setTimeout(() => t.remove(), 350);
  }, 2800);
};

/* ── §15 · Delegación global de eventos ──────────────────────────────────── */
document.addEventListener('click', e => {

  if (e.target.closest('#jz-btn-add') || e.target.closest('#jz-sb-nueva'))     { openModal();  return; }
  if (e.target.closest('#jz-modal-close'))                       { closeModal(); return; }
  if (e.target.closest('#jz-modal-cancel'))                      { closeModal(); return; }
  if (e.target.closest('#jz-modal-save'))                        { saveModal();  return; }

  /* Sidebar: Cargar por lote — placeholder */
  if (e.target.closest('#jz-sb-lote')) {
    showToast('Carga por lote: próximamente', 'info');
    return;
  }

  /* Sidebar: Eliminar seleccionados */
  if (e.target.closest('#jz-sb-eliminar')) {
    showToast('Selecciona una canción de la lista para eliminarla', 'info');
    return;
  }

  /* Sidebar: Tareas */
  if (e.target.closest('#jz-task-artistas'))     { showToast('Editar Artistas: próximamente', 'info');           return; }
  if (e.target.closest('#jz-task-categorias'))   { showToast('Editar Categorías: próximamente', 'info');         return; }
  if (e.target.closest('#jz-task-estadisticas')) { showToast(`Estadísticas · ${library.length} pistas`, 'info'); return; }
  if (e.target.closest('#jz-task-exportar'))     { showToast('Exportar BD: próximamente', 'info');               return; }
  if (e.target.closest('#jz-task-integridad'))   { showToast('Chequeo de integridad: próximamente', 'info');     return; }
  if (e.target.closest('#jz-task-difusion'))     { showToast('Análisis de difusión: próximamente', 'info');      return; }

  const overlay = getOverlay();
  if (overlay && e.target === overlay)                           { closeModal(); return; }

  /* Play en tabla */
  const pb = e.target.closest('.jz-play-btn');
  if (pb && pb.closest('#jz-tbody')) {
    const track = library.find(t => t.id === +pb.dataset.id);
    if (track) playTrack(track);
    return;
  }

  /* Editar */
  const eb = e.target.closest('.jz-action-edit');
  if (eb) { openModal(+eb.dataset.id); return; }

  /* Eliminar */
  const db = e.target.closest('.jz-action-del');
  if (db) {
    const id    = +db.dataset.id;
    const track = library.find(t => t.id === id);
    if (!track || !confirm(`¿Eliminar "${track.title}"?`)) return;
    if (playingId === id) stopPlayer();
    fetch(`${API_BASE}/api/v1/audios/${id}`, { method: 'DELETE' })
      .then(async res => {
        if (!res.ok) { const d = await res.json().catch(() => ({})); showToast(d.detail || `Error ${res.status}`, 'error'); return; }
        wsCmd('DELETE', { id });
        showToast(`Eliminado: ${track.title}`, 'error');
        loadAudios();
      })
      .catch(() => showToast('Error de conexión al eliminar', 'error'));
    return;
  }

  /* Sort */
  const th = e.target.closest('.jz-table thead th[data-sort]');
  if (th) {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = 1; }
    document.querySelectorAll('.jz-table thead th').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
    th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
    applyFilters();
    return;
  }

  /* Mini player toggle */
  if (e.target.closest('#jz-player-toggle')) { if (playingId) stopPlayer(); return; }

  /* Timeline click */
  const tl = e.target.closest('#jz-timeline');
  if (tl && playingId) {
    const rect = tl.getBoundingClientRect();
    const pct  = (e.clientX - rect.left) / rect.width;
    if (audioEl?.duration) audioEl.currentTime = pct * audioEl.duration;
    return;
  }

  /* Dropzone */
  if (e.target.closest('#jz-dropzone')) {
    if (e.target.id === 'modal-file') return;
    const fi = $('modal-file'); if (fi) fi.click();
    return;
  }

});

/* Filtros */
const FIDS = new Set(['jz-search','jz-filter-sc','jz-filter-pop','jz-filter-voz']);
document.addEventListener('input',  e => { if (FIDS.has(e.target.id)) applyFilters(); });
document.addEventListener('change', e => {
  if (FIDS.has(e.target.id)) applyFilters();
  if (e.target.id === 'modal-file' && e.target.files?.[0]) handleFile(e.target.files[0]);
});

/* Drag & Drop */
document.addEventListener('dragover',  e => { const dz = e.target.closest('#jz-dropzone'); if (dz) { e.preventDefault(); dz.classList.add('drag-over'); } });
document.addEventListener('dragleave', e => { const dz = e.target.closest('#jz-dropzone'); if (dz) dz.classList.remove('drag-over'); });
document.addEventListener('drop', e => {
  const dz = e.target.closest('#jz-dropzone');
  if (dz) { e.preventDefault(); dz.classList.remove('drag-over'); handleFile(e.dataTransfer.files[0]); }
});

/* Teclado */
document.addEventListener('keydown', e => {
  const isOpen = getOverlay()?.classList.contains('active');
  if (e.key === 'Escape'    && isOpen)   { closeModal(); return; }
  if (e.key === 'n' && e.ctrlKey && !isOpen) { e.preventDefault(); openModal(); return; }
});

/* ── §16 · Exportar a window ─────────────────────────────────────────────── */
window.openModal  = openModal;
window.closeModal = closeModal;
window.saveModal  = saveModal;

console.log('[ÓNIX FM] admin-app.js cargado — funciones vinculadas a window.');

/* ── §17 · Arranque ──────────────────────────────────────────────────────── */
initApp();
