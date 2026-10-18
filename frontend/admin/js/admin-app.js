// ONIX/frontend/admin/js/admin-app.js
// ─────────────────────────────────────────────────────────────────────────────
// Shell + Biblioteca Musical — Ónix FM
// Paleta Jazler Soho. 6 cue points: inicio, intro, inicio_coro, final_coro,
// outro, mezcla.  Todas las funciones de modal exportadas a window al final.
// ─────────────────────────────────────────────────────────────────────────────

/* La navegación entre secciones la hace el shell de admin/index.html.
   (Aquí había un segundo shell que buscaba un contenedor inexistente.) */

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
  tbody.innerHTML = filtered.map(t => {
    const cat1Color = (window.onixCategories?.getCategories?.()?.soundCode?.items || []).find(i => i.label === t.soundCode)?.color;
    const bgStyle = cat1Color ? `background: linear-gradient(90deg, ${cat1Color}22 0%, ${cat1Color}08 100%) !important; border-left: 3px solid ${cat1Color} !important;` : '';
    return `
    <tr data-id="${t.id}" class="${playingId === t.id ? 'jz-row--playing' : ''}" style="${bgStyle}">
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
      <td class="jz-cell-sc" style="${cat1Color ? `color:${cat1Color};font-weight:bold` : ''}">${t.soundCode || '—'}</td>
      <td class="jz-cell-pop">${t.popularity || '—'}</td>
      <td>${voxBadge(t.voz)}</td>
      <td style="font-family:var(--font-mono);font-size:11px;color:var(--jz-text3);text-align:right">${t.bpm || '—'}</td>
      <td class="jz-cell-dur">${fmtDur(t.duration || 0)}</td>
      <td>
        <div class="jz-actions">
          <button class="jz-row-action jz-row-action--edit jz-action-edit" data-id="${t.id}" title="Editar">
             <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <button class="jz-row-action jz-row-action--del jz-action-del" data-id="${t.id}" title="Eliminar">
             <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </button>
        </div>
      </td>
    </tr>
  `;
  }).join('');
};

const applyFilters = () => {
  const q = ($('jz-search')?.value || '').toLowerCase();
  const sc = $('jz-filter-sc')?.value || '';
  const pop = $('jz-filter-pop')?.value || '';
  const voz = $('jz-filter-voz')?.value || '';
  const cat3 = $('jz-filter-cat3')?.value || '';
  const year = $('jz-filter-year')?.value || '';

  filtered = library.filter(t =>
    (!q || t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q) || (t.album && t.album.toLowerCase().includes(q))) &&
    (!sc || t.soundCode === sc) &&
    (!pop || t.popularity === pop) &&
    (!voz || t.voz === voz) &&
    (!cat3 || t.era === cat3) &&
    (!year || String(t.year) === year)
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
  // La API resuelve la ruta real del archivo a partir del id
  const streamUrl = `${API_BASE}/api/v1/audios/${track.id}/stream`;
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
    // Toda la biblioteca (antes solo las primeras 500) y sin cuñas: las
    // publicidades tienen su propia biblioteca en Programación de Pautas.
    // Lista ligera y comprimida (con 20 000 audios, /api/v1/audios pesa 15 MB y tarda 2 s)
    // La Biblioteca musical no muestra cuñas ni identificadores (jingles, sweepers, tips): tienen su propia sección
    const lista = await OnixBuscador.lista({ sin_tipo: 'Cuña,Efecto,Sweeper,Tips,Locucion' });
    library = lista.map(a => ({
      id: a.id, title: a.titulo || '—', artist: a.artista || '—', album: a.album || '',
      duration: a.duracion || 0, bpm: a.bpm || null, year: a.fecha_lanzamiento || null,
      soundCode: a.cat1 || '', popularity: a.cat2 || '', voz: a.voz || '',
      origen: a.origen || '', energia: a.energia ?? '',
      epoca: a.epoca || '',
      tipo: a.tipo_audio || 'Musica',
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

/* Exportar BD = hacer una copia de respaldo ahora y descargarla (solo administrador) */
async function exportarBD() {
  const r = await fetch(`${API_BASE}/api/v1/respaldos`, { method: 'POST' });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    showToast(r.status === 403 ? 'Solo el administrador puede exportar la base de datos' : (d.detail || `Error ${r.status}`), 'error');
    return;
  }
  const c = await r.json();
  location.href = `${API_BASE}/api/v1/respaldos/${encodeURIComponent(c.archivo)}`;
  showToast(`✓ Copia ${c.archivo} descargándose`, 'success');
}

/* Chequeo de integridad = las revisiones del panel de problemas, hechas ahora */
async function chequeoIntegridad() {
  showToast('Revisando la biblioteca…', 'info');
  try {
    const d = await fetch(`${API_BASE}/api/v1/problemas?forzar=true`).then(r => r.json());
    const lista = d.activos.filter(p => ['archivos', 'relojes', 'pautas', 'biblioteca', 'respaldo'].includes(p.clave));
    if (!lista.length) { showToast('✓ Biblioteca en orden: no faltan archivos ni hay relojes o pautas sin audio', 'success'); return; }
    alert('Chequeo de integridad\n\n' + lista.map(p =>
      `• ${p.titulo}${p.detalle ? `\n  ${p.detalle}` : ''}${p.ejemplos.length ? `\n  - ${p.ejemplos.slice(0, 8).join('\n  - ')}` : ''}`).join('\n\n'));
  } catch (e) { showToast('No se pudo hacer el chequeo', 'error'); }
}

/* Autocompletar del artista con los ya cargados (como Jazler) */
async function cargarArtistas() {
  const input = $('modal-artist');
  if (!input) return;
  let dl = $('bm-artistas');
  if (!dl) {
    dl = document.createElement('datalist'); dl.id = 'bm-artistas';
    input.parentNode.appendChild(dl);
    input.setAttribute('list', 'bm-artistas');
    input.setAttribute('autocomplete', 'off');
  }
  try {
    const lista = await fetch(`${API_BASE}/api/v1/artistas?tipo=Musica`).then(r => r.ok ? r.json() : []);
    dl.innerHTML = lista.map(a => `<option value="${String(a.artista).replace(/"/g, '&quot;')}">${a.audios} tema(s)</option>`).join('');
  } catch { /* sin lista: se escribe a mano */ }
}

function calcularEpocaJS(anio) {
  if (anio === null || anio === undefined || anio === '') return 'Sin Clasificar';
  const a = parseInt(anio, 10);
  if (isNaN(a) || a <= 0) return 'Sin Clasificar';
  if (a >= 2020) return 'Actual';
  if (a >= 2010 && a <= 2019) return '2010s';
  if (a >= 2000 && a <= 2009) return '2000s';
  if (a >= 1990 && a <= 1999) return '90s';
  if (a >= 1980 && a <= 1989) return '80s';
  if (a >= 1970 && a <= 1979) return '70s';
  return 'Oldies';
}

function actualizarEpocaModal() {
  const y = $('modal-year')?.value;
  const ep = $('modal-epoca');
  if (ep) ep.value = calcularEpocaJS(y);
}

async function openModal(id = null) {
  const overlay = getOverlay();
  if (!overlay) return;
  if (typeof window.onixCategories?.syncSelects === 'function') window.onixCategories.syncSelects();
  if (typeof window.wfDestroy === 'function') window.wfDestroy();
  overlay.classList.add('active'); overlay.style.display = 'flex'; overlay.style.zIndex = '9000';
  if (typeof window.wfInit === 'function') {
    try { await window.wfInit('bm-waveform'); } catch (e) { console.error('[Ónix] Error waveform:', e); }
  }
  const p = $('wf-panel'); if (p) p.style.display = 'block';
  editingId = id;
  cargarArtistas();
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
    if ($('modal-epoca')) $('modal-epoca').value = t.epoca || calcularEpocaJS(t.year);
    if ($('modal-bpm')) $('modal-bpm').value = t.bpm || '';
    if ($('modal-popularity')) $('modal-popularity').value = t.popularity || '';
    if ($('modal-voz')) $('modal-voz').value = t.voz || '';
    if ($('modal-tipo')) $('modal-tipo').value = t.tipo || 'Musica';
    if ($('modal-origen')) $('modal-origen').value = t.origen || '';
    if ($('modal-energia')) $('modal-energia').value = t.energia ?? '';
    selectedFile = null;
    if ($('modal-file')) $('modal-file').value = '';
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'block';
    renderFileInfo({ name: (t.archivo_path || '').split(/[/\\]/).pop() || 'Archivo actual', size: 0 }, null);
    if ($('modal-archivo-path')) $('modal-archivo-path').value = t.archivo_path || '';
    if (typeof window.wfLoadUrl === 'function' && t.archivo_path) {
      const streamUrl = `${API_BASE}/api/v1/audios/${t.id}/stream`;
      const cues = { inicio: t.cue_inicio || 0, intro: t.cue_intro || 0, inicio_coro: t.cue_inicio_coro || 0, final_coro: t.cue_final_coro || 0, outro: t.cue_outro || 0, mezcla: t.cue_mezcla || 0 };
      window.wfLoadUrl(streamUrl, cues);
    }
  } else {
    if ($('modal-title-bar')) $('modal-title-bar').textContent = 'NUEVA CANCIÓN';
    ['modal-artist', 'modal-title-input', 'modal-album', 'modal-year', 'modal-bpm', 'modal-escritor', 'modal-compositor', 'modal-etiqueta', 'modal-cdkey', 'modal-barras', 'modal-archivo-path'].forEach(fid => { const el = $(fid); if (el) el.value = ''; });
    if ($('modal-epoca')) $('modal-epoca').value = 'Sin Clasificar';
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'block';
    if ($('jz-dropzone')) $('jz-dropzone').style.display = 'block';
    if ($('modal-file-info')) { $('modal-file-info').innerHTML = ''; $('modal-file-info').style.display = 'none'; }
    if ($('modal-file')) $('modal-file').value = '';
    selectedFile = null;
    if ($('modal-tipo')) $('modal-tipo').value = 'Musica';
    if ($('modal-origen')) $('modal-origen').value = '';
    if ($('modal-energia')) $('modal-energia').value = '';
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
  if ($('modal-file')) $('modal-file').value = '';
  if ($('modal-file-info')) { $('modal-file-info').innerHTML = ''; $('modal-file-info').style.display = 'none'; }
  if ($('jz-dropzone')) $('jz-dropzone').style.display = 'block';
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
      if (selectedFile) {
        btnSave.textContent = 'Reemplazando archivo…';
        const fdArch = new FormData();
        fdArch.append('file', selectedFile);
        const rArch = await fetch(`${API_BASE}/api/v1/audios/${editingId}/archivo`, { method: 'POST', body: fdArch });
        if (!rArch.ok) {
          const dErr = await rArch.json().catch(() => ({}));
          showToast(dErr.detail || 'Error al reemplazar archivo de audio', 'error');
          return;
        }
      }
      const toUpper = v => (v && typeof v === 'string') ? v.trim().toUpperCase() : v;
      const payload = { titulo: titleVal, artista: artistVal, album: $('modal-album')?.value.trim() || null, fecha_lanzamiento: $('modal-year')?.value.trim() || null, bpm: $('modal-bpm')?.value ? Number($('modal-bpm').value) : null, cat1: toUpper($('modal-sc1')?.value || $('modal-sc2')?.value) || null, cat2: toUpper($('modal-popularity')?.value) || null, voz: toUpper($('modal-voz')?.value) || null, cue_inicio: markers.inicio ?? 0, cue_intro: markers.intro ?? 0, cue_inicio_coro: markers.inicio_coro ?? 0, cue_final_coro: markers.final_coro ?? 0, cue_mezcla: markers.mezcla ?? 0, fade_in: markers.fade_in ?? 0, fade_out: markers.fade_out ?? 0, intro: markers.intro ?? 0, outro: markers.mezcla ?? 0 };
      payload.tipo_audio = $('modal-tipo')?.value || null;
      payload.origen = $('modal-origen')?.value || null;
      payload.energia = $('modal-energia')?.value ? Number($('modal-energia').value) : null;
      Object.keys(payload).forEach(k => { if (payload[k] === null || payload[k] === '') delete payload[k]; });
      res = await fetch(`${API_BASE}/api/v1/audios/${editingId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    } else {
      const formData = new FormData();
      formData.append('titulo', titleVal); formData.append('artista', artistVal);
      const opt = { album: $('modal-album')?.value.trim(), bpm: $('modal-bpm')?.value, fecha_lanzamiento: $('modal-year')?.value.trim(), cat1: $('modal-sc1')?.value || $('modal-sc2')?.value, cat2: $('modal-popularity')?.value, voz: $('modal-voz')?.value };
      opt.tipo_audio = $('modal-tipo')?.value;
      opt.origen = $('modal-origen')?.value;
      opt.energia = $('modal-energia')?.value;
      Object.entries(opt).forEach(([k, v]) => { if (v) formData.append(k, v); });
      formData.append('cue_inicio', markers.inicio ?? 0); formData.append('cue_intro', markers.intro ?? 0); formData.append('cue_inicio_coro', markers.inicio_coro ?? 0); formData.append('cue_final_coro', markers.final_coro ?? 0); formData.append('cue_mezcla', markers.mezcla ?? 0); formData.append('fade_in', markers.fade_in ?? 0); formData.append('fade_out', markers.fade_out ?? 0); formData.append('intro', markers.intro ?? 0); formData.append('outro', markers.mezcla ?? 0); formData.append('file', selectedFile);
      res = await fetch(`${API_BASE}/api/v1/audios`, { method: 'POST', body: formData });
    }
    const data = await res.json();
    if (!res.ok) { const detail = data.detail; showToast(typeof detail === 'object' ? (detail.mensaje || JSON.stringify(detail)) : (detail || `Error ${res.status}`), 'error'); return; }
    wsCmd('library_updated', { id: data.data?.id, titulo: titleVal, action: editingId ? 'update' : 'create' });
    closeModal(); await loadAudios();
    showToast(editingId ? (selectedFile ? `✓ Audio reemplazado y actualizado: ${titleVal}` : `✓ Actualizado: ${titleVal}`) : `✓ Cargado: ${titleVal}`, 'success');
  } catch (err) {
    showToast('Error de conexión con la API', 'error'); console.error('[BM·save]', err);
  } finally {
    if (btnSave) { btnSave.disabled = false; btnSave.textContent = 'OK'; }
  }
}

function triggerModalFilePicker() {
  const fi = $('modal-file');
  if (fi) {
    fi.value = '';
    fi.click();
  }
}

function removeModalFile() {
  selectedFile = null;
  const fi = $('modal-file'); if (fi) fi.value = '';
  if (editingId) {
    const t = library.find(x => x.id === editingId);
    if (t) {
      renderFileInfo({ name: (t.archivo_path || '').split(/[/\\]/).pop() || 'Archivo actual', size: 0 }, null);
      const ap = $('modal-archivo-path'); if (ap) ap.value = t.archivo_path || '';
      if (typeof window.wfLoadUrl === 'function' && t.archivo_path) {
        const streamUrl = `${API_BASE}/api/v1/audios/${t.id}/stream`;
        const cues = { inicio: t.cue_inicio || 0, intro: t.cue_intro || 0, inicio_coro: t.cue_inicio_coro || 0, final_coro: t.cue_final_coro || 0, outro: t.cue_outro || 0, mezcla: t.cue_mezcla || 0 };
        window.wfLoadUrl(streamUrl, cues);
      }
      showToast('Reemplazo cancelado. Se conserva el archivo original.', 'info');
      return;
    }
  }
  const ap = $('modal-archivo-path'); if (ap) ap.value = '';
  const info = $('modal-file-info');
  if (info) { info.innerHTML = ''; info.style.display = 'none'; }
  const dz = $('jz-dropzone');
  if (dz) dz.style.display = 'block';
  if (typeof window.wfDestroy === 'function') window.wfDestroy();
  showToast('Archivo quitado. Selecciona un archivo de audio.', 'info');
}

function renderFileInfo(file, tagData = null) {
  const info = $('modal-file-info');
  if (!info) return;
  const sizeMb = file.size > 0 ? `${(file.size / 1048576).toFixed(1)} MB` : 'Archivo en servidor';
  const tagNote = tagData?.origen_datos === 'etiquetas' ? ' · 🏷 Metadatos leídos' : '';
  const d = tagData?.duplicados?.[0];
  const isExistingUnchanged = !!editingId && !selectedFile;
  const dupHtml = d
    ? `<div style="margin-top:8px;padding:6px 10px;background:rgba(255,90,95,0.12);border:1px solid rgba(255,90,95,0.3);border-radius:4px;font-size:11px;color:#ff5a5f;">
        ⚠ Ya existe en la biblioteca: <strong>${d.titulo}</strong> — ${d.artista} (#${d.id}). ¿Seguro que quieres cargarlo de nuevo?
       </div>`
    : '';

  info.innerHTML = `
    <div style="background:rgba(0,200,240,0.06);border:1.5px solid rgba(0,200,240,0.35);border-radius:6px;padding:10px 12px;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
        <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;">
          <div style="font-size:24px;line-height:1;">🎵</div>
          <div style="min-width:0;flex:1;">
            <div style="font-weight:700;font-size:12px;color:var(--jz-text,#fff);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${file.name}">
              ${file.name}
            </div>
            <div style="font-size:10px;color:var(--jz-text3,#888);margin-top:2px;font-family:var(--font-mono);">
              ${sizeMb} ${tagNote} ${editingId && selectedFile ? '· <b style="color:#00f0ff">🔄 Nuevo audio seleccionado (pendiente de guardar)</b>' : ''}
            </div>
          </div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-shrink:0;">
          <button type="button" id="modal-btn-cambiar-archivo" class="jz-btn jz-btn--sm" style="background:rgba(0,200,240,0.18);color:var(--neon-cyan,#00f0ff);border:1px solid rgba(0,200,240,0.5);cursor:pointer;padding:6px 12px;font-size:11px;font-weight:700;display:inline-flex;align-items:center;gap:5px;">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            Cambiar archivo
          </button>
          ${!isExistingUnchanged ? `<button type="button" id="modal-btn-quitar-archivo" class="jz-btn jz-btn--sm" style="background:rgba(255,60,60,0.12);color:#ff5a5f;border:1px solid rgba(255,60,60,0.3);cursor:pointer;padding:6px 10px;font-size:11px;font-weight:600;" title="${editingId ? 'Cancelar cambio' : 'Quitar este archivo'}">
            ✕ ${editingId ? 'Cancelar' : 'Quitar'}
          </button>` : ''}
        </div>
      </div>
      ${dupHtml}
    </div>
  `;
  info.style.display = 'block';
  const dz = $('jz-dropzone');
  if (dz) dz.style.display = 'none';
}

const handleFile = file => {
  if (!file || !file.type.startsWith('audio/')) { showToast('El archivo seleccionado no es audio', 'error'); return; }
  const prevFile = selectedFile;
  selectedFile = file;

  renderFileInfo(file, null);

  const ap = $('modal-archivo-path');
  if (ap) ap.value = file.name;

  const ti = $('modal-title-input');
  const prevBase = prevFile ? prevFile.name.replace(/\.[^.]+$/, '').toUpperCase() : '';
  if (ti && (!ti.value || ti.value === prevBase)) {
    ti.value = file.name.replace(/\.[^.]+$/, '').toUpperCase();
  }

  if (typeof window.wfLoadFile === 'function') window.wfLoadFile(file);

  // Etiquetas del MP3 y aviso de duplicado (como Jazler al importar)
  leerEtiquetas(file).then(t => {
    const poner = (id, v) => {
      const el = $(id);
      if (el && v && (!el.value || el.value === file.name.replace(/\.[^.]+$/, '').toUpperCase() || el.value === prevBase)) {
        el.value = String(v).toUpperCase();
      }
    };
    poner('modal-title-input', t.titulo);
    poner('modal-artist', t.artista);
    poner('modal-album', t.album);
    const y = $('modal-year');
    if (y && !y.value && t.anio) {
      y.value = t.anio;
      actualizarEpocaModal();
    }
    renderFileInfo(file, t);
  }).catch(() => { /* sin servidor de etiquetas: se completa a mano */ });

  wsCmd('FILE_SELECTED', { name: file.name, size: file.size });
};

const populateYearFilters = () => {
  const sel = $('jz-filter-year');
  if (!sel) {
    if (!window._yearRetries) window._yearRetries = 0;
    if (window._yearRetries < 20) {
      window._yearRetries++;
      setTimeout(populateYearFilters, 250);
    }
    return;
  }
  if (sel.options.length > 1) return;
  const currentYear = new Date().getFullYear();
  sel.innerHTML = '<option value="">Todos</option>';
  for (let y = currentYear; y >= 1950; y--) {
    const o = document.createElement('option');
    o.value = y; o.textContent = y;
    sel.appendChild(o);
  }
};

const initApp = async () => {
  if (!window._waitWFStarted) {
    window._waitWFStarted = true;
    const waitWF = () => {
      if (document.getElementById('bm-waveform') && typeof window.wfInit === 'function') { window.wfInit('bm-waveform'); console.log('[BM] WaveSurfer inicializado'); }
      else { setTimeout(waitWF, 80); }
    };
    waitWF();
  }
  populateYearFilters();
  setStatus('Cargando schema…');
  try { const r = await fetch(`${API_BASE}/api/v1/config/biblioteca/schema`); if (r.ok) schema = await r.json(); } catch (e) { console.warn('[BM·schema]', e.message); }
  setPlayerInfo(null); setStatus('Cargando biblioteca…');
  await loadAudios();
  const n = library.length;
  setStatus(`Sistema listo · ${n} pista${n !== 1 ? 's' : ''} cargada${n !== 1 ? 's' : ''}`);
  // (sin aviso: la vista nueva ya muestra el resumen)
};

const wsCmd = (cmd, data = {}) => {
  const msg = { module: 'biblioteca-musical', cmd, ts: Date.now(), data };
  if (window.ShellWS?.send) window.ShellWS.send(JSON.stringify(msg));
  else console.info('[BM·WS]', msg);
};
const setStatus = msg => { const el = getStatusMsg(); if (el) el.textContent = msg; };
const showToast = (msg, type = 'info', title = null) => {
  let container = document.getElementById('jz-toasts');
  if (!container) {
    container = document.createElement('div');
    container.id = 'jz-toasts';
    container.className = 'jz-toasts';
    document.body.appendChild(container);
  }

  const icons = {
    success: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`,
    error: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
    warning: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`
  };

  const defaultTitles = {
    success: 'Operación Exitosa',
    error: 'Error del Sistema',
    warning: 'Atención Requerida',
    info: 'Notificación'
  };

  const toast = document.createElement('div');
  toast.className = `onix-toast-2026 onix-toast--${type}`;
  toast.innerHTML = `
    <div class="onix-toast__icon">
      ${icons[type] || icons.info}
    </div>
    <div class="onix-toast__body">
      <span class="onix-toast__title">${title || defaultTitles[type] || 'Notificación'}</span>
      <span class="onix-toast__msg">${msg}</span>
    </div>
    <button class="onix-toast__close" title="Cerrar">✕</button>
    <div class="onix-toast__bar"><div class="onix-toast__progress"></div></div>
  `;

  let closed = false;
  const closeToast = () => {
    if (closed) return;
    closed = true;
    toast.classList.add('onix-toast--closing');
    setTimeout(() => { toast.remove(); }, 260);
  };

  toast.querySelector('.onix-toast__close').addEventListener('click', closeToast);
  container.appendChild(toast);

  // Auto-cierre sincronizado
  setTimeout(closeToast, 4000);
};
window.showToast = showToast;

document.addEventListener('click', e => {
  if (e.target.closest('#jz-btn-add') || e.target.closest('#jz-sb-nueva')) { openModal(); return; }
  if (e.target.closest('#jz-modal-close')) { closeModal(); return; }
  if (e.target.closest('#jz-modal-cancel')) { closeModal(); return; }
  if (e.target.closest('#jz-modal-save')) { saveModal(); return; }
  if (e.target.closest('#jz-sb-lote')) { openLoteModal(); return; }
  if (e.target.closest('#jz-sb-eliminar')) { showToast('Selecciona una canción de la lista para eliminarla', 'info'); return; }
  if (e.target.closest('#jz-task-artistas')) { $('jz-search')?.focus(); showToast('Busca el artista y edita sus canciones (doble clic)', 'info'); return; }
  if (e.target.closest('#jz-task-categorias')) { if (window.onixCategories?.open) window.onixCategories.open(); else showToast('Editar Categorías: módulo no cargado', 'error'); return; }
  if (e.target.closest('#jz-task-estadisticas')) { window.ShellNav.activate('07-estadisticas'); return; }
  if (e.target.closest('#jz-task-exportar')) { exportarBD(); return; }
  if (e.target.closest('#jz-task-integridad')) { chequeoIntegridad(); return; }
  if (e.target.closest('#jz-task-difusion')) { window.ShellNav.activate('07-estadisticas'); return; }
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
  if (e.target.closest('#modal-btn-cambiar-archivo') || e.target.closest('#modal-btn-browse')) {
    e.preventDefault();
    e.stopPropagation();
    triggerModalFilePicker();
    return;
  }
  if (e.target.closest('#modal-btn-quitar-archivo')) {
    e.preventDefault();
    e.stopPropagation();
    removeModalFile();
    return;
  }
  if (e.target.closest('#modal-btn-edit-path')) {
    e.preventDefault();
    const ap = $('modal-archivo-path');
    if (ap) { ap.focus(); ap.select(); }
    return;
  }
  if (e.target.closest('#jz-dropzone')) {
    if (e.target.id === 'modal-file') return;
    triggerModalFilePicker();
    return;
  }

  /* ── LOTE WIZARD EVENTS ────────────────────────────────── */
  if (e.target.closest('#lote-next-step')) { loteNextStep(); return; }
  if (e.target.closest('#lote-next-prev')) { lotePrevStep(); return; }
  if (e.target.closest('#lote-btn-finish')) { closeLoteModal(); loadAudios(); return; }
  if (e.target.closest('#lote-close')) { pedirCerrarLote(); return; }
  if (e.target.closest('#lote-btn-again')) { loteReset(); return; }
  if (e.target.closest('#lote-sel-all')) { loteSelectAll(true); return; }
  if (e.target.closest('#lote-sel-none')) { loteSelectAll(false); return; }
  if (e.target.closest('#lote-p-toggle')) { lotePlayerToggle(); return; }

  // Seek in progress bar
  const lp = e.target.closest('#lote-p-progress');
  if (lp && lotePlayer && lotePlayer.duration) {
    const rect = lp.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    lotePlayer.currentTime = pct * lotePlayer.duration;
    return;
  }

  const lpb = e.target.closest('.lote-play-row'); if (lpb) { lotePlayerPlay(+lpb.dataset.idx); return; }

  // Autoplay on row click only if the checkbox is checked
  const ltr = e.target.closest('.lote-row');
  if (ltr && !e.target.closest('.lote-file-check')) {
    const isAutoplay = $('lote-p-autoplay')?.checked;
    if (isAutoplay) {
      lotePlayerPlay(+ltr.dataset.idx);
    }
    return;
  }
});

document.addEventListener('dblclick', e => {
  const row = e.target.closest('tr[data-id]');
  if (row && row.closest('#jz-tbody')) {
    const id = +row.dataset.id;
    if (id) openModal(id);
  }
});

/* ── DELEGATED LISTENERS — Búsqueda en tiempo real + filtros ──────── */
document.addEventListener('input', e => {
  if (e.target.id === 'jz-search') applyFilters();
  if (e.target.id === 'modal-year') actualizarEpocaModal();
});
document.addEventListener('change', e => {
  if (['jz-filter-sc', 'jz-filter-pop', 'jz-filter-voz', 'jz-filter-cat3', 'jz-filter-year', 'jz-filter-prioridad'].includes(e.target.id)) applyFilters();
  if (e.target.id === 'modal-year') actualizarEpocaModal();
});

/* ── LOTE WIZARD LOGIC ─────────────────────────────────────────────── */
let loteStep = 1;
let loteFiles = [];
let lotePlayer = null;
let lotePlayerIdx = -1;

function openLoteModal() {
  loteStep = 1; loteFiles = []; loteResetPlayer();
  if ($('lote-sin-clasificar')) $('lote-sin-clasificar').checked = false;     // cada carga empieza pidiendo género y rotación
  $('lote-overlay').classList.add('active');
  loteUpdateUI();
  syncLoteSelects();
}

function closeLoteModal() {
  $('lote-overlay').classList.remove('active');
  loteResetPlayer();
}

/* Cerrar con la X o Esc: no a mitad de una subida, y preguntando si ya se eligieron archivos */
let loteProcesando = false;
function pedirCerrarLote() {
  if (loteProcesando) { showToast('Espera a que termine la carga en curso', 'info'); return; }
  if (loteStep > 1 && loteStep < 4 && loteFiles.length &&
      !confirm('¿Cerrar sin cargar los archivos elegidos?')) return;
  const cargo = loteStep === 4;
  closeLoteModal();
  if (cargo) loadAudios();
}

function loteUpdateUI() {
  document.querySelectorAll('.lote-step').forEach(s => s.classList.remove('active'));
  $(`lote-step-${loteStep}`).classList.add('active');
  $('lote-step-indicator').textContent = `PASO ${loteStep} DE 4`;

  $('lote-next-prev').style.display = loteStep === 1 || loteStep === 4 ? 'none' : 'block';
  $('lote-next-step').textContent = loteStep === 3 ? 'PROCESAR LOTE' : 'SIGUIENTE';
  $('lote-next-step').style.display = loteStep === 4 ? 'none' : 'block';
  $('lote-processing-info').style.display = 'none';

  if (loteStep === 2) {
    $('lote-next-step').disabled = !loteFiles.some(f => f.selected);
  }
  if (loteStep === 3 && !loteProcesando) loteValidarPaso3();
}

/* Paso 3: género y rotación son obligatorios; la única salida es «cargar sin clasificar», dicho a propósito y por escrito */
function loteValidarPaso3() {
  const sinClasificar = $('lote-sin-clasificar')?.checked;
  const completo = !!$('lote-sc1').value && !!$('lote-pop').value;
  const hayGeneros = $('lote-sc1').options.length > 1, hayRotaciones = $('lote-pop').options.length > 1;
  const aviso = $('lote-aviso-listas');
  if (aviso) {
    const faltan = [!hayGeneros && 'géneros', !hayRotaciones && 'rotaciones'].filter(Boolean);
    aviso.style.display = faltan.length ? 'block' : 'none';
    aviso.textContent = faltan.length ? `Todavía no hay ${faltan.join(' ni ')} creados. Créalos en Configuración › Biblioteca para poder elegirlos, o carga sin clasificar.` : '';
  }
  $('lote-next-step').disabled = !(completo || sinClasificar);
  $('lote-next-step').title = (completo || sinClasificar) ? '' : 'Elige el género y la rotación (o marca «Cargar sin clasificar»)';
}

function loteNextStep() {
  if (loteStep === 1) {
    if (loteFiles.length === 0) { showToast('Selecciona una carpeta válida primero', 'error'); return; }
    loteStep = 2; renderLoteFiles(); analizarLote();
  } else if (loteStep === 2) {
    loteStep = 3;
    $('lote-final-count').textContent = loteFiles.filter(f => f.selected).length;
  } else if (loteStep === 3) {
    processLote();
  }
  loteUpdateUI();
}

function lotePrevStep() {
  if (loteStep > 1) { loteStep--; loteUpdateUI(); }
}

function loteReset() {
  loteStep = 1; loteFiles = []; loteResetPlayer(); loteUpdateUI();
  $('lote-path-info').textContent = '';
}

/* Evento manejado por delegación global para evitar problemas con carga dinámica */

/* ── Carga inteligente: etiquetas ID3 y duplicados ──
   Se manda al servidor solo el comienzo de cada archivo (ahí van las
   etiquetas del MP3): rápido aunque sean cientos y desde otra PC. */
async function leerEtiquetas(file) {
  const fd = new FormData();
  fd.append('file', file.slice(0, 2 * 1024 * 1024), file.name);
  fd.append('nombre', file.name);
  const r = await fetch(`${API_BASE}/api/v1/biblioteca/etiquetas`, { method: 'POST', body: fd });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

let loteAnalisis = null;
function analizarLote() {
  if (loteAnalisis) return loteAnalisis;
  const pendientes = loteFiles.filter(f => !f.tags && !f.analizando);
  let idx = 0, dibujar = null;
  const repintar = () => { if (!dibujar) dibujar = requestAnimationFrame(() => { dibujar = null; if (loteStep === 2) renderLoteFiles(); }); };
  const trabajador = async () => {
    while (idx < pendientes.length) {
      const f = pendientes[idx++];
      f.analizando = true; repintar();
      try {
        f.tags = await leerEtiquetas(f.file);
        // Un duplicado se desmarca solo (si el usuario no lo tocó)
        if (f.tags.duplicados?.length && !f.tocado) f.selected = false;
      } catch (e) { f.tags = { error: true }; }
      f.analizando = false; repintar();
    }
  };
  loteAnalisis = Promise.all([1, 2, 3, 4].map(trabajador)).then(() => {
    loteAnalisis = null;
    const dups = loteFiles.filter(f => f.tags?.duplicados?.length).length;
    if (dups) showToast(`${dups} archivo(s) ya están en la biblioteca: quedaron sin marcar`, 'info');
    if (loteStep === 2) { renderLoteFiles(); $('lote-next-step').disabled = !loteFiles.some(f => f.selected); }
  });
  return loteAnalisis;
}

function loteEstado(f) {
  if (f.analizando || (!f.tags && loteAnalisis)) return '<span style="color:#888">⏳ leyendo…</span>';
  if (!f.tags || f.tags.error) return '<span style="color:#888">—</span>';
  if (f.tags.duplicados?.length) {
    const d = f.tags.duplicados[0];
    return `<span style="color:#ff5a5f;font-weight:700" title="Ya existe: ${d.titulo} — ${d.artista} (#${d.id})">⚠ DUPLICADO</span>`;
  }
  return f.tags.origen_datos === 'etiquetas' ? '<span style="color:#52b788" title="Datos leídos de las etiquetas del archivo">🏷 etiquetas</span>'
                                             : '<span style="color:#e6a817" title="Sin etiquetas: datos tomados del nombre del archivo">📄 del nombre</span>';
}

function renderLoteFiles() {
  const list = $('lote-file-list');
  const e = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  list.innerHTML = loteFiles.map((f, i) => `
    <tr class="lote-row ${lotePlayerIdx === i ? 'playing' : ''}" data-idx="${i}" style="cursor: pointer;">
      <td><input type="checkbox" class="lote-file-check" data-idx="${i}" ${f.selected ? 'checked' : ''}></td>
      <td><div>${e(f.name)}</div>${f.tags && !f.tags.error ? `<div style="font-size:11px;color:#9ab">${e(f.tags.artista || '¿artista?')} — ${e(f.tags.titulo)}${f.tags.anio ? ` · ${e(f.tags.anio)}` : ''}</div>` : ''}</td>
      <td style="font-size:12px">${loteEstado(f)}</td>
      <td style="font-family: var(--font-mono); font-size: 11px; color:#666;">${f.size}</td>
      <td><button class="lote-player-btn lote-play-row" data-idx="${i}">${ICO(lotePlayerIdx === i ? 'stop2' : 'play2')}</button></td>
    </tr>
  `).join('');

  $('lote-file-count').textContent = loteFiles.length;

  list.querySelectorAll('.lote-file-check').forEach(ck => {
    ck.onchange = (e) => {
      e.stopPropagation();
      loteFiles[ck.dataset.idx].selected = ck.checked;
      loteFiles[ck.dataset.idx].tocado = true;
      $('lote-next-step').disabled = !loteFiles.some(f => f.selected);
    };
  });
}

function loteSelectAll(val) {
  loteFiles.forEach(f => f.selected = val);
  renderLoteFiles();
  $('lote-next-step').disabled = !val;
}

/* ── MINI PLAYER ── */
function loteResetPlayer() {
  if (lotePlayer) { lotePlayer.pause(); lotePlayer = null; }
  lotePlayerIdx = -1;
  if ($('lote-p-toggle')) $('lote-p-toggle').innerHTML = ICO('play2');
  if ($('lote-p-title')) $('lote-p-title').textContent = 'Sin audio...';
  if ($('lote-p-bar')) $('lote-p-bar').style.width = '0%';
}

function lotePlayerToggle() {
  if (!lotePlayer) return;
  if (lotePlayer.paused) { lotePlayer.play(); $('lote-p-toggle').innerHTML = ICO('pause2'); }
  else { lotePlayer.pause(); $('lote-p-toggle').innerHTML = ICO('play2'); }
}

function lotePlayerPlay(idx) {
  if (lotePlayerIdx === idx) { loteResetPlayer(); return; }
  loteResetPlayer();
  lotePlayerIdx = idx;
  const file = loteFiles[idx].file;
  lotePlayer = new Audio(URL.createObjectURL(file));
  $('lote-p-title').textContent = file.name;
  $('lote-p-toggle').innerHTML = ICO('pause2');
  lotePlayer.play();

  lotePlayer.ontimeupdate = () => {
    $('lote-p-bar').style.width = (lotePlayer.currentTime / lotePlayer.duration * 100) + '%';
  };
  lotePlayer.onended = () => {
    loteResetPlayer();
    if ($('lote-p-autoplay').checked && idx + 1 < loteFiles.length) {
      lotePlayerPlay(idx + 1);
    }
  };
  renderLoteFiles();
}

function syncLoteSelects() {
  if (typeof window.onixCategories?.syncSelects === 'function') {
    const s1 = $('lote-sc1'), pop = $('lote-pop'), voz = $('lote-voz');
    const getItems = (key) => window.onixCategories.getCategories()[key]?.items || [];

    const fill = (el, items, vacio) => {
      el.innerHTML = `<option value="">${vacio}</option>`;
      items.forEach(it => el.innerHTML += `<option value="${it.label}">${it.label}</option>`);
    };

    // NOTA: Reuso la lógica de CategoriesModule expuesta
    // Como CategoriesModule es una IIFE, necesito asegurarme de que exponga 'categories'
    // Hack: Usaremos los IDs directos si CategoriesModule los maneja, pero lote tiene IDs propios.
    // Llenaremos manualmente.
    const cats = ['soundCode', 'popularity', 'voz'];
    const targets = [s1, pop, voz];
    const vacios = ['— Elige el género —', '— Elige la rotación —', '— Sin definir —'];
    cats.forEach((k, i) => fill(targets[i], getItems(k), vacios[i]));   // del servidor (antes, del navegador)
    if (typeof loteValidarPaso3 === 'function' && loteStep === 3) loteValidarPaso3();
  }
}

async function processLote() {
  const selected = loteFiles.filter(f => f.selected);
  if (selected.length === 0) return;

  loteStep = 3; // Stay here during processing
  loteProcesando = true;
  loteUpdateUI();
  $('lote-next-step').disabled = true;
  $('lote-next-prev').disabled = true;
  $('lote-processing-info').style.display = 'block';

  let success = 0, error = 0, listas = 0, porCompletar = 0;
  const globalMeta = {
    cat1: $('lote-sc1').value.trim(), cat2: $('lote-pop').value.trim(),
    voz: $('lote-voz').value.trim(), year: $('lote-year').value,
    bpm: $('lote-bpm').value, active: $('lote-active').checked
  };

  for (let i = 0; i < selected.length; i++) {
    const item = selected[i];
    $('lote-processing-info').textContent = `PROCESANDO: ${i + 1} / ${selected.length}... (${item.name})`;

    try {
      const formData = new FormData();
      // Datos de las etiquetas del archivo (o de su nombre, si no tiene)
      let artist = item.tags?.artista || 'Unknown Artist', title = item.tags?.titulo || item.name.replace(/\.[^.]+$/, '');
      if (!item.tags?.artista && item.name.includes(' - ')) {
        const parts = item.name.split(' - ');
        artist = parts[0].trim();
        title = parts[1].split('.')[0].trim();
      }
      if (item.tags?.album) formData.append('album', item.tags.album);
      if (item.tags?.bpm && !globalMeta.bpm) formData.append('bpm', item.tags.bpm);
      if (item.tags?.anio && !globalMeta.year) formData.append('fecha_lanzamiento', item.tags.anio);

      formData.append('titulo', title);
      formData.append('artista', artist);
      if (globalMeta.cat1) formData.append('cat1', globalMeta.cat1);
      if (globalMeta.cat2) formData.append('cat2', globalMeta.cat2);
      if (globalMeta.voz) formData.append('voz', globalMeta.voz);
      if (globalMeta.year) formData.append('fecha_lanzamiento', globalMeta.year);
      if (globalMeta.bpm) formData.append('bpm', globalMeta.bpm);

      // Default cue points to satisfy potential API requirements
      formData.append('cue_inicio', 0);
      formData.append('cue_intro', 0);
      formData.append('cue_mezcla', 0);
      formData.append('intro', 0);
      formData.append('outro', 0);

      formData.append('file', item.file);

      const res = await fetch(`${API_BASE}/api/v1/audios`, { method: 'POST', body: formData });
      if (res.ok) {
        success++;
        const d = await res.json().catch(() => ({}));
        if (d.lista_para_sonar) listas++; else porCompletar++;
        wsCmd('library_updated', { id: d.data?.id, titulo: title, action: 'create' });
      } else {
        error++;
        const errData = await res.json().catch(() => ({}));
        console.error(`[LOTE] Error en ${item.name}:`, errData);
      }
    } catch (e) {
      console.error(`[LOTE] Excepción en ${item.name}:`, e);
      error++;
    }
  }

  loteStep = 4;
  loteProcesando = false;
  loteUpdateUI();
  $('lote-success-msg').innerHTML = `Se han procesado <strong>${selected.length}</strong> archivos.<br>
    <span style="color:#00ff88">✓ ${listas} listas para sonar</span>
    ${porCompletar ? ` | <span style="color:#FF6600">⏳ ${porCompletar} por completar (no suenan hasta ponerles género y rotación)</span>` : ''}
    ${error ? ` | <span style="color:#ff6666">✕ ${error} con error</span>` : ''}`;
  $('lote-next-step').disabled = false;
  $('lote-next-prev').disabled = false;
}

const FIDS = new Set(['jz-search', 'jz-filter-sc', 'jz-filter-pop', 'jz-filter-voz']);
document.addEventListener('input', e => { if (FIDS.has(e.target.id)) applyFilters(); });
document.addEventListener('change', e => {
  if (['lote-sc1', 'lote-pop', 'lote-sin-clasificar'].includes(e.target.id)) loteValidarPaso3();
  if (FIDS.has(e.target.id)) applyFilters();
  if (e.target.id === 'modal-file' && e.target.files?.[0]) handleFile(e.target.files[0]);

  /* ── LOTE FOLDER INPUT DELEGATION ── */
  if (e.target.id === 'lote-folder-input' && e.target.files?.length > 0) {
    const raw = Array.from(e.target.files);
    loteFiles = raw.filter(f => f.type.startsWith('audio/') || f.name.match(/\.(mp3|wav|ogg|flac)$/i))
      .map((file, i) => ({
        file,
        id: i,
        name: file.name,
        size: (file.size / 1024 / 1024).toFixed(1) + ' MB',
        selected: true
      }));
    loteAnalisis = null;      // carpeta nueva: analizar de cero

    if (loteFiles.length > 0) {
      const pathInfo = $('lote-path-info');
      if (pathInfo) pathInfo.textContent = `✓ Carpeta cargada: ${loteFiles.length} archivos de audio encontrados.`;
      loteNextStep();
    } else {
      showToast('No se encontraron archivos de audio válidos.', 'error');
    }
  }
});
document.addEventListener('dragover', e => {
  const dz = e.target.closest('#modal-dropzone-wrap, #jz-dropzone');
  if (dz) {
    e.preventDefault();
    const jz = $('jz-dropzone');
    if (jz && jz.style.display !== 'none') jz.classList.add('drag-over');
  }
});
document.addEventListener('dragleave', e => {
  const dz = e.target.closest('#modal-dropzone-wrap, #jz-dropzone');
  if (dz) {
    const jz = $('jz-dropzone');
    if (jz) jz.classList.remove('drag-over');
  }
});
document.addEventListener('drop', e => {
  const dz = e.target.closest('#modal-dropzone-wrap, #jz-dropzone');
  if (dz) {
    e.preventDefault();
    const jz = $('jz-dropzone');
    if (jz) jz.classList.remove('drag-over');
    if (e.dataTransfer?.files?.[0]) handleFile(e.dataTransfer.files[0]);
  }
});
document.addEventListener('keydown', e => {
  const isOpen = getOverlay()?.classList.contains('active') || $('lote-overlay')?.classList.contains('active');
  /* Bloqueo del cierre por tecla Escape (Desactivado por seguridad) */
  /* if (e.key === 'Escape' && isOpen) { closeModal(); closeLoteModal(); return; } */
  // El lote sí se cierra con Esc (con las mismas protecciones que la X)
  if (e.key === 'Escape' && $('lote-overlay')?.classList.contains('active')) { pedirCerrarLote(); return; }
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
window.calcularEpocaJS = calcularEpocaJS;
window.actualizarEpocaModal = actualizarEpocaModal;
console.log('[ÓNIX FM] admin-app.js cargado — funciones vinculadas a window.');

const CategoriesModule = (function () {
  const CATEGORY_KEYS = ['soundCode', 'popularity', 'era', 'voz', 'propiedades'];
  const DEFAULTS = {
    soundCode: { name: 'Categoría 1', canRename: true, items: [{ label: 'POP', color: '#8e44ad' }, { label: 'ROCK', color: '#6b7a1e' }, { label: 'DANCE', color: '#cc0000' }, { label: 'ALTERNATIVE', color: '#808080' }] },
    popularity: { name: 'Categoría 2', canRename: true, items: [{ label: 'HOT CURRENT', color: '#e67e22' }, { label: 'CURRENT', color: '#d35400' }, { label: 'OLDIES 1', color: '#2980b9' }, { label: 'OLDIES 2', color: '#1a5276' }] },
    era: { name: 'Categoría 3', canRename: true, items: [{ label: '60s', color: '#4a4a6a' }, { label: '70s', color: '#4a5a3a' }, { label: '80s', color: '#5a3a5a' }, { label: '90s', color: '#3a5a6a' }, { label: '2000s', color: '#5a4a2a' }, { label: '2010s', color: '#2a4a5a' }, { label: '2020s', color: '#3a3a5a' }] },
    voz: { name: 'Voz', canRename: false, items: [{ label: 'Female', color: '#8e44ad' }, { label: 'Male', color: '#555555' }, { label: 'Duo', color: '#555555' }, { label: 'Group', color: '#555555' }, { label: 'Collaboration', color: '#555555' }] },
    propiedades: { name: 'Propiedades', canRename: false, items: [] }
  };
  let categories = {}, activeKey = 'soundCode', selectedIdx = -1;
  /* Las categorías viven en el SERVIDOR (/api/v1/config/biblioteca): así la
     PC del estudio y la del jefe de programación ven las mismas. Antes se
     guardaban en el navegador de cada PC y cada una tenía las suyas. */
  const SERVIDOR = { soundCode: 'cat1', popularity: 'cat2', era: 'cat3', voz: 'voz' };
  const PALETA = ['#8e44ad', '#cc0000', '#d35400', '#2980b9', '#16a085', '#6b7a1e', '#c0392b', '#7f8c8d'];
  function reset() { CATEGORY_KEYS.forEach(k => { categories[k] = JSON.parse(JSON.stringify(DEFAULTS[k])); }); }
  async function load() {
    reset();
    try {
      const schema = await fetch('/api/v1/config/biblioteca/schema').then(r => r.json());
      for (const [key, interno] of Object.entries(SERVIDOR)) {
        const c = (schema.categorias || []).find(x => x.nombre_interno === interno);
        if (!c) continue;
        categories[key] = { name: c.etiqueta_visible, canRename: false,
          items: c.valores.map((v, i) => ({ id: v.id, label: v.valor, color: v.color || PALETA[i % PALETA.length] })) };
      }
    } catch (e) { showToast('No se pudieron cargar las categorías del servidor', 'error'); }
  }
  async function api(url, metodo, body) {
    const r = await fetch(url, { method: metodo, headers: { 'Content-Type': 'application/json' },
                                 body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(typeof d.detail === 'string' ? d.detail : `Error ${r.status}`);
    }
    return r.json().catch(() => ({}));
  }
  async function recargar() { await load(); sync(); renderSidebar(); renderItems(); }
  const SELECT_MAP = { soundCode: ['modal-sc1', 'modal-sc2', 'modal-sc3', 'jz-filter-sc'], popularity: ['modal-popularity', 'jz-filter-pop'], era: ['modal-era', 'jz-filter-cat3'], voz: ['modal-voz', 'jz-filter-voz'], propiedades: [] };
  const SELECT_LABELS = { 'modal-sc1': '— CAT 1 —', 'modal-sc2': '— CAT 2 —', 'modal-sc3': '— CAT 3 —', 'jz-filter-sc': 'Categoría 1', 'modal-popularity': '— —', 'jz-filter-pop': 'Categoría 2', 'modal-era': '—', 'jz-filter-cat3': 'Categoría 3', 'modal-voz': '—', 'jz-filter-voz': 'Voz' };
  function sync() { CATEGORY_KEYS.forEach(key => { const ids = SELECT_MAP[key] || [], items = categories[key]?.items || []; ids.forEach(id => { const el = document.getElementById(id); if (!el) return; const cur = el.value; el.innerHTML = `<option value="">${SELECT_LABELS[id] || '—'}</option>`; items.forEach(it => { const o = document.createElement('option'); o.value = it.label; o.textContent = it.label; el.appendChild(o); }); if (cur && [...el.options].some(o => o.value === cur)) el.value = cur; }); }); }
  function renderSidebar() { const sb = document.getElementById('cat-sidebar'); if (!sb) return; sb.innerHTML = CATEGORY_KEYS.map(key => `<button class="cat-sidebar__item ${key === activeKey ? 'active' : ''}" data-key="${key}"><span class="cat-sidebar__dot" style="background:${categories[key].items[0]?.color || '#555'}"></span>${categories[key].name}</button>`).join(''); if (!categories[activeKey]?.canRename) { const n = document.createElement('div'); n.className = 'cat-sidebar__note'; n.textContent = 'Esta categoría no puede ser renombrada.'; sb.appendChild(n); } }
  function renderItems() { const list = document.getElementById('cat-items-list'); if (!list) return; const items = categories[activeKey]?.items || []; if (!items.length) { list.innerHTML = '<div class="cat-empty">— Sin ítems —</div>'; return; } list.innerHTML = items.map((it, i) => `<div class="cat-item-row ${i === selectedIdx ? 'selected' : ''}" data-idx="${i}"><span class="cat-item__color" style="background:${it.color}"></span><span class="cat-item__label">${it.label}</span></div>`).join(''); }
  function updateHeader() {
    const cat = categories[activeKey], p = document.getElementById('cat-panel-prefix'), n = document.getElementById('cat-panel-name');
    if (p) p.textContent = cat.canRename ? 'Cambiar Categoría por' : 'Cambiar';
    if (n) n.textContent = cat.name;
    const isCat1 = activeKey === 'soundCode';
    const c1 = document.getElementById('cat-new-color'), c2 = document.getElementById('cat-edit-color');
    if (c1) c1.style.display = isCat1 ? 'inline-block' : 'none';
    if (c2) c2.style.display = isCat1 ? 'inline-block' : 'none';
  }
  const publicApi = {
    init() { load().then(sync); },
    syncSelects() { sync(); },
    getCategories() { return categories; },
    open() { const overlay = document.getElementById('cat-overlay'); if (!overlay) return; activeKey = 'soundCode'; selectedIdx = -1; overlay.classList.add('active'); renderSidebar(); renderItems(); updateHeader(); },
    close() { document.getElementById('cat-overlay')?.classList.remove('active'); },
    select(key) { activeKey = key; selectedIdx = -1; renderSidebar(); renderItems(); updateHeader(); },
    async add() {
      const label = document.getElementById('cat-new-name')?.value.trim().replace(/\s+/g, ' ');     // tal como se escribe: Pop, pop o POP
      if (!label || !SERVIDOR[activeKey]) return;
      if (categories[activeKey].items.some(i => i.label.toUpperCase() === label)) { showToast('Ya existe', 'error'); return; }
      try {
        const r = await api('/api/v1/config/biblioteca/valores', 'POST', { nombre_interno: SERVIDOR[activeKey], valor: label });
        const color = document.getElementById('cat-new-color')?.value;
        if (color && r.data?.id) await api(`/api/v1/config/biblioteca/valores/${r.data.id}`, 'PUT', { color });
        document.getElementById('cat-add-form')?.classList.remove('visible');
        await recargar();
      } catch (e) { showToast(e.message, 'error'); }
    },
    openEdit() { if (selectedIdx < 0) { showToast('Selecciona un ítem para editar', 'info'); return; } const item = categories[activeKey].items[selectedIdx]; if (!item) return; document.getElementById('cat-add-form')?.classList.remove('visible'); const nameEl = document.getElementById('cat-edit-name'), colorEl = document.getElementById('cat-edit-color'); if (nameEl) nameEl.value = item.label; if (colorEl) colorEl.value = item.color || '#8e44ad'; document.getElementById('cat-edit-form')?.classList.add('visible'); if (nameEl) nameEl.focus(); },
    confirmEdit() { if (selectedIdx < 0) return; const newLabel = document.getElementById('cat-edit-name')?.value.trim().replace(/\s+/g, ' '); if (!newLabel) { showToast('El nombre no puede estar vacío', 'error'); return; } const item = categories[activeKey].items[selectedIdx]; if (!item) return; const isDuplicate = categories[activeKey].items.some((it, i) => i !== selectedIdx && it.label === newLabel); if (isDuplicate) { showToast('Ya existe un ítem con ese nombre', 'error'); return; } const color = document.getElementById('cat-edit-color')?.value || item.color;
      api(`/api/v1/config/biblioteca/valores/${item.id}`, 'PUT', { valor: newLabel, color })
        .then(async r => {
          document.getElementById('cat-edit-form')?.classList.remove('visible');
          await recargar(); loadAudios();
          showToast(`✓ ${newLabel}${r.audios_actualizados ? ` · ${r.audios_actualizados} canción(es) actualizadas` : ''}`, 'success');
        })
        .catch(e => showToast(e.message, 'error'));
    },
    async delete() {
      const item = categories[activeKey]?.items[selectedIdx];
      if (!item) {
        showToast('Selecciona una categoría de la lista para eliminar', 'warning');
        return;
      }

      try {
        const uso = await api(`/api/v1/config/biblioteca/valores/${item.id}/uso`, 'GET');
        const modal = document.getElementById('cat-del-modal');

        if (!modal) {
          if (!confirm(`¿Eliminar "${item.label}"? Tiene ${uso.total_audios} canción(es) asociadas.`)) return;
          const res = await api(`/api/v1/config/biblioteca/valores/${item.id}?accion=desasignar`, 'DELETE');
          selectedIdx = -1;
          await recargar();
          if (typeof loadAudios === 'function') loadAudios();
          window.BibliotecaVista?.recargar?.();
          showToast(res.mensaje || 'Categoría eliminada', 'success');
          return;
        }

        const titleEl = document.getElementById('cat-del-title');
        const subtitleEl = document.getElementById('cat-del-subtitle');
        const iconBadge = document.getElementById('cat-del-icon-badge');
        const bodyEl = document.getElementById('cat-del-body');
        const confirmBtn = document.getElementById('cat-del-btn-confirmar');
        const cancelBtn = document.getElementById('cat-del-btn-cancelar');
        const closeBtn = document.getElementById('cat-del-close-btn');

        const catName = categories[activeKey]?.name || 'Categoría';

        const closeModalDel = () => {
          modal.style.display = 'none';
        };

        closeBtn.onclick = closeModalDel;
        cancelBtn.onclick = closeModalDel;
        modal.onclick = (e) => { if (e.target === modal) closeModalDel(); };

        if (uso.total_audios === 0) {
          if (iconBadge) {
            iconBadge.className = 'onix-modal-pro__icon-badge onix-modal-pro__icon-badge--danger';
            iconBadge.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>`;
          }
          if (titleEl) titleEl.textContent = 'Eliminar Categoría';
          if (subtitleEl) subtitleEl.textContent = `${catName} · Confirmación directa`;

          bodyEl.innerHTML = `
            <div style="font-size: 13.5px; line-height: 1.5; color: #e2e8f0;">
              ¿Estás seguro de eliminar <strong style="color: #ffffff; background: rgba(255,255,255,0.08); padding: 2px 6px; border-radius: 4px;">${item.label}</strong>?
            </div>
            <div style="font-size: 12px; color: #94a3b8; display: flex; align-items: center; gap: 8px;">
              <span style="display:inline-block; width: 8px; height: 8px; border-radius: 50%; background: #10b981;"></span>
              Esta categoría no está asignada a ninguna canción activa en el sistema.
            </div>
          `;

          confirmBtn.textContent = 'Eliminar';
          confirmBtn.className = 'onix-pro-btn onix-pro-btn--danger';
          confirmBtn.onclick = async () => {
            confirmBtn.disabled = true;
            try {
              const res = await api(`/api/v1/config/biblioteca/valores/${item.id}`, 'DELETE');
              closeModalDel();
              selectedIdx = -1;
              await recargar();
              if (typeof loadAudios === 'function') loadAudios();
              window.BibliotecaVista?.recargar?.();
              showToast(res.mensaje || `Categoría "${item.label}" eliminada`, 'success');
            } catch (err) {
              showToast(err.message, 'error');
            } finally {
              confirmBtn.disabled = false;
            }
          };
        } else {
          if (iconBadge) {
            iconBadge.className = 'onix-modal-pro__icon-badge';
            iconBadge.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;
          }
          if (titleEl) titleEl.textContent = 'Categoría en Uso';
          if (subtitleEl) subtitleEl.textContent = `${catName} · Resolver canciones antes de eliminar`;

          const chipsHtml = (uso.ejemplos || []).map(ej => `<span class="onix-song-chip" title="${ej}">🎵 ${ej}</span>`).join('');
          const hayAlternativas = (uso.alternativas || []).length > 0;
          const opcionesSelect = (uso.alternativas || []).map(alt => `<option value="${alt.id}">${alt.valor}</option>`).join('');

          bodyEl.innerHTML = `
            <div class="onix-count-badge">
              <span class="onix-count-num">${uso.total_audios}</span>
              <span>canción(es) tienen asignada la categoría <strong>${item.label}</strong></span>
            </div>

            ${uso.ejemplos?.length ? `
              <div class="onix-songs-preview">
                <div class="onix-songs-preview__title">Canciones afectadas (muestra reciente):</div>
                <div class="onix-songs-chips">${chipsHtml}</div>
              </div>
            ` : ''}

            <div style="font-size: 12.5px; font-weight: 600; color: #cbd5e1; margin-top: 4px;">
              Selecciona cómo debe resolver el sistema estas canciones:
            </div>

            <div class="onix-resolve-card ${hayAlternativas ? 'active' : ''}" id="card-opt-reasignar" style="${!hayAlternativas ? 'opacity: 0.5; pointer-events: none;' : ''}">
              <div class="onix-resolve-card__header">
                <div class="onix-radio-custom"></div>
                <div class="onix-resolve-card__title">Reasignar a otra categoría activa</div>
              </div>
              <div class="onix-resolve-card__desc">
                Las ${uso.total_audios} canciones pasarán automáticamente a la categoría seleccionada.
              </div>
              ${hayAlternativas ? `
                <select class="onix-resolve-select" id="cat-del-reassign-select">
                  ${opcionesSelect}
                </select>
              ` : '<div style="font-size:11.5px; color:#ef4444; margin-left:30px;">(No hay otras categorías activas para reasignar)</div>'}
            </div>

            <div class="onix-resolve-card ${!hayAlternativas ? 'active' : ''}" id="card-opt-desasignar">
              <div class="onix-resolve-card__header">
                <div class="onix-radio-custom"></div>
                <div class="onix-resolve-card__title">Desasignar (dejar sin categoría)</div>
              </div>
              <div class="onix-resolve-card__desc">
                Las canciones se mantendrán en la biblioteca, pero no mostrarán esta categoría (campo vacío).
              </div>
            </div>
          `;

          let selectedAccion = hayAlternativas ? 'reasignar' : 'desasignar';
          const cardReasignar = document.getElementById('card-opt-reasignar');
          const cardDesasignar = document.getElementById('card-opt-desasignar');
          const selectDest = document.getElementById('cat-del-reassign-select');

          if (cardReasignar && hayAlternativas) {
            cardReasignar.onclick = (e) => {
              if (e.target === selectDest) return;
              selectedAccion = 'reasignar';
              cardReasignar.classList.add('active');
              cardDesasignar.classList.remove('active');
            };
          }
          if (cardDesasignar) {
            cardDesasignar.onclick = () => {
              selectedAccion = 'desasignar';
              cardDesasignar.classList.add('active');
              if (cardReasignar) cardReasignar.classList.remove('active');
            };
          }

          confirmBtn.textContent = 'Resolver y Eliminar';
          confirmBtn.className = 'onix-pro-btn onix-pro-btn--danger';
          confirmBtn.onclick = async () => {
            confirmBtn.disabled = true;
            try {
              let url = `/api/v1/config/biblioteca/valores/${item.id}?accion=${selectedAccion}`;
              if (selectedAccion === 'reasignar') {
                const targetId = selectDest?.value;
                if (!targetId) {
                  showToast('Debes seleccionar una categoría destino', 'warning');
                  confirmBtn.disabled = false;
                  return;
                }
                url += `&reasignar_a_id=${targetId}`;
              }
              const res = await api(url, 'DELETE');
              closeModalDel();
              selectedIdx = -1;
              await recargar();
              if (typeof loadAudios === 'function') loadAudios();
              window.BibliotecaVista?.recargar?.();
              showToast(res.mensaje || `Categoría resuelta y eliminada`, 'success');
            } catch (err) {
              showToast(err.message, 'error');
            } finally {
              confirmBtn.disabled = false;
            }
          };
        }

        modal.style.display = 'flex';
      } catch (e) {
        showToast(e.message, 'error');
      }
    },
    async move(dir) {
      const items = categories[activeKey].items;
      if (selectedIdx < 0 || selectedIdx + dir < 0 || selectedIdx + dir >= items.length) return;
      [items[selectedIdx], items[selectedIdx + dir]] = [items[selectedIdx + dir], items[selectedIdx]];
      selectedIdx += dir; renderItems();
      try { await Promise.all(items.map((it, i) => api(`/api/v1/config/biblioteca/valores/${it.id}`, 'PUT', { orden: i + 1 }))); sync(); }
      catch (e) { showToast(e.message, 'error'); recargar(); }
    }
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
