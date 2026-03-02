// ONIX/frontend/admin/js/admin-app.js
const Shell = {
  config: { basePath: 'sections/', containerId: 'module-view', activeClass: 'active' },
  init() { console.log('Ónix FM Shell — Sistema Iniciado'); this.bindEvents(); this.loadSection('01-estado-global'); },
  bindEvents() {
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', () => {
        const module = btn.getAttribute('data-module');
        if (!module) return;
        document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const routes = { 'dashboard': '01-estado-global', 'biblioteca': '02-biblioteca-musical', 'pautas': '03-programacion-pautas', 'cartuchera': '04-editor-cartuchera', 'logs': '05-historial-emision', 'engine': '06-motor-audio' };
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
      const html = await response.text();
      container.innerHTML = html;
      const titleEl = document.getElementById('module-title');
      if (titleEl) titleEl.textContent = sectionName.replace(/-/g, ' ').toUpperCase().slice(3);
    } catch (error) {
      container.innerHTML = `<div class="error">Error al cargar pieza: ${error.message}</div>`;
    }
  }
};
document.addEventListener('DOMContentLoaded', () => Shell.init());

'use strict';

let schema = { categorias: [] };
const getCatValues = nombre => { const cat = schema.categorias.find(c => c.nombre_interno === nombre); return cat ? cat.valores.map(v => v.valor) : []; };
const getCatLabel = nombre => { const cat = schema.categorias.find(c => c.nombre_interno === nombre); return cat ? cat.etiqueta_visible : nombre; };

let library = [];
let filtered = [];
let sortKey = null;
let sortDir = 1;
let editingId = null;
let playingId = null;
let audioEl = null;
let playerTimer = null;
let currentEnergy = null;
let selectedFile = null;

const $ = id => document.getElementById(id);
const getOverlay = () => document.getElementById('bm-overlay');
const getTbody = () => document.getElementById('bm-tbody');
const getCountEl = () => document.getElementById('bm-count');
const getStatusMsg = () => document.getElementById('bm-status-msg');
const getEmptyEl = () => document.getElementById('bm-empty');

const populateSelect = (selId, items, allLabel) => {
  const sel = $(selId);
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = `<option value="">${allLabel}</option>`;
  items.forEach(v => {
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    if (v === current) o.selected = true;
    sel.appendChild(o);
  });
};

const buildFilters = () => {
  populateSelect('bm-filter-cat1', getCatValues('cat1'), getCatLabel('cat1') || 'Género');
  populateSelect('bm-filter-cat2', getCatValues('cat2'), getCatLabel('cat2') || 'Rotación');
  populateSelect('bm-filter-cat3', getCatValues('cat3'), getCatLabel('cat3') || 'Extra');
  populateSelect('bm-filter-voz', getCatValues('voz'), getCatLabel('voz') || 'Voz');
};

const buildModalSelects = () => {
  populateSelect('modal-cat1', getCatValues('cat1'), '— Seleccionar —');
  populateSelect('modal-cat2', getCatValues('cat2'), '— Seleccionar —');
  populateSelect('modal-cat3', getCatValues('cat3'), '— Seleccionar —');
  populateSelect('modal-voz', getCatValues('voz'), '— Seleccionar —');
};

const buildEnergySel = () => {
  const wrap = $('modal-energy-sel');
  if (!wrap) return;
  wrap.innerHTML = '';
  for (let i = 1; i <= 9; i++) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'bm-energy-btn';
    btn.textContent = i;
    btn.dataset.val = i;
    wrap.appendChild(btn);
  }
};

const setEnergy = val => {
  currentEnergy = val;
  document.querySelectorAll('.bm-energy-btn').forEach(b => { b.classList.toggle('sel', +b.dataset.val === val); });
  const labels = ['', 'Tranquilo', 'Suave', 'Ligero', 'Moderado', 'Animado', 'Dinámico', 'Intenso', 'Potente', '¡Máximo!'];
  const lbl = $('modal-energy-label');
  if (lbl) lbl.textContent = `${val} — ${labels[val] ?? ''}`;
};

const fmtDuration = s => { const m = Math.floor(s / 60), sec = s % 60; return `${m}:${String(sec).padStart(2, '0')}`; };

const voxBadge = voz => {
  const cls = { 'Mujer': 'badge-mujer', 'Hombre': 'badge-hombre', 'Dúo': 'badge-duo', 'Grupo': 'badge-grupo' };
  return `<span class="bm-badge ${cls[voz] || 'badge-grupo'}">${voz}</span>`;
};

const energyBar = energy => {
  let html = '<div class="bm-energy">';
  for (let i = 1; i <= 9; i++) {
    html += `<div class="bm-energy__dot ${i <= energy ? 'on' : ''} ${i >= 8 ? 'high' : ''}"></div>`;
  }
  return html + '</div>';
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
    <tr data-id="${t.id}" class="${playingId === t.id ? 'bm-row--playing' : ''}">
      <td class="bm-cell-id">${t.id}</td>
      <td class="bm-cell-play">
        <button class="bm-play-btn ${playingId === t.id ? 'playing' : ''}" data-id="${t.id}" title="Preescucha">
          ${playingId === t.id ? `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>` : `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`}
        </button>
      </td>
      <td class="bm-cell-title">${t.title}</td>
      <td class="bm-cell-artist">${t.artist}</td>
      <td>${voxBadge(t.voz)}</td>
      <td class="bm-cell-cat1">${t.cat1}</td>
      <td class="bm-cell-cat2">${t.cat2}</td>
      <td>${energyBar(t.energy)}</td>
      <td class="bm-cell-dur">${fmtDuration(t.duration)}</td>
      <td>
        <div class="bm-actions">
          <button class="bm-btn bm-btn--sm bm-btn--cyan bm-action-edit" data-id="${t.id}" title="Editar">✎</button>
          <button class="bm-btn bm-btn--sm bm-btn--danger bm-action-del" data-id="${t.id}" title="Eliminar">✕</button>
        </div>
      </td>
    </tr>
  `).join('');
};

const applyFilters = () => {
  const q = ($('bm-search')?.value || '').toLowerCase();
  const cat1 = $('bm-filter-cat1')?.value || '';
  const cat2 = $('bm-filter-cat2')?.value || '';
  const cat3 = $('bm-filter-cat3')?.value || '';
  const voz = $('bm-filter-voz')?.value || '';
  filtered = library.filter(t =>
    (!q || t.title.toLowerCase().includes(q) || t.artist.toLowerCase().includes(q)) &&
    (!cat1 || t.cat1 === cat1) &&
    (!cat2 || t.cat2 === cat2) &&
    (!cat3 || t.cat3 === cat3) &&
    (!voz || t.voz === voz)
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
  if ($('bm-player-title')) $('bm-player-title').textContent = track ? track.title : '—';
  if ($('bm-player-artist')) $('bm-player-artist').textContent = track ? track.artist : 'Sin pista cargada';
  if ($('bm-player-cat')) $('bm-player-cat').textContent = track ? `${track.cat1} · ${track.cat2}` : '';
  if ($('bm-time-total')) $('bm-time-total').textContent = track ? fmtDuration(track.duration) : '0:00';
  if ($('bm-timeline-fill')) $('bm-timeline-fill').style.width = '0%';
  if ($('bm-time-cur')) $('bm-time-cur').textContent = '0:00';
};

const stopPlayer = () => {
  if (audioEl) { audioEl.pause(); audioEl.src = ''; audioEl = null; }
  clearInterval(playerTimer);
  const prevId = playingId;
  playingId = null;
  if (prevId) getTbody()?.querySelector(`tr[data-id="${prevId}"]`)?.classList.remove('bm-row--playing');
  if ($('bm-timeline-fill')) $('bm-timeline-fill').style.width = '0%';
  if ($('bm-time-cur')) $('bm-time-cur').textContent = '0:00';
  if ($('bm-player-icon')) $('bm-player-icon').innerHTML = '<polygon points="5,3 19,12 5,21"/>';
};

const updateTimeline = (cur, total) => {
  if ($('bm-timeline-fill')) $('bm-timeline-fill').style.width = `${(cur / total) * 100}%`;
  if ($('bm-time-cur')) $('bm-time-cur').textContent = fmtDuration(Math.floor(cur));
};

const simulatePlayback = track => {
  let t = 0;
  playerTimer = setInterval(() => {
    t += 0.5;
    if (t >= track.duration) { clearInterval(playerTimer); stopPlayer(); setPlayerInfo(null); render(); return; }
    updateTimeline(t, track.duration);
  }, 500);
  if ($('bm-player-icon')) $('bm-player-icon').innerHTML = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';
};

const playTrack = track => {
  if (playingId === track.id) { stopPlayer(); setPlayerInfo(null); return; }
  stopPlayer();
  playingId = track.id;
  setPlayerInfo(track);
  let streamUrl = `${API_BASE}/stream/unknown.mp3`;
  if (track.archivo_path) {
    const parts = track.archivo_path.replace(/\\/g, '/').split('/');
    const rel = parts.slice(-2).join('/');
    streamUrl = `${API_BASE || 'http://localhost:8000'}/stream/${rel}`;
  }
  audioEl = new Audio(streamUrl);
  audioEl.volume = 0.9;
  audioEl.play().catch(() => simulatePlayback(track));
  audioEl.addEventListener('timeupdate', () => { if (audioEl && !isNaN(audioEl.duration)) updateTimeline(audioEl.currentTime, track.duration); });
  audioEl.addEventListener('ended', () => { stopPlayer(); setPlayerInfo(null); render(); });
  audioEl.addEventListener('error', () => simulatePlayback(track));
  render();
  wsCmd('PREVIEW', { id: track.id });
  showToast(`▶ ${track.title}`, 'info');
};

function openModal(id = null) {
  const overlay = getOverlay();
  if (!overlay) return;
  editingId = id;
  buildModalSelects();
  buildEnergySel();
  currentEnergy = null;
  if (id) {
    const t = library.find(x => x.id === id);
    if (!t) return;
    if ($('modal-badge')) $('modal-badge').textContent = `ID ${t.id}`;
    if ($('modal-title')) $('modal-title').textContent = 'Editar Audio';
    if ($('modal-title-input')) $('modal-title-input').value = t.title;
    if ($('modal-artist')) $('modal-artist').value = t.artist;
    if ($('modal-album')) $('modal-album').value = t.album || '';
    if ($('modal-year')) $('modal-year').value = t.year || '';
    if ($('modal-bpm')) $('modal-bpm').value = t.bpm || '';
    if ($('modal-lang')) $('modal-lang').value = t.lang || 'es';
    if ($('modal-cat1')) $('modal-cat1').value = t.cat1;
    if ($('modal-cat2')) $('modal-cat2').value = t.cat2;
    if ($('modal-cat3')) $('modal-cat3').value = t.cat3;
    if ($('modal-voz')) $('modal-voz').value = t.voz;
    if ($('modal-cue-intro')) $('modal-cue-intro').value = t.intro;
    if ($('modal-cue-mix')) $('modal-cue-mix').value = t.mix;
    if ($('modal-notes')) $('modal-notes').value = t.notes || '';
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'none';
    if ($('modal-staging-field')) $('modal-staging-field').style.display = 'none';
    if ($('modal-duracion-field')) $('modal-duracion-field').style.display = 'none';
    setEnergy(t.energy);
  } else {
    if ($('modal-badge')) $('modal-badge').textContent = 'NUEVO';
    if ($('modal-title')) $('modal-title').textContent = 'Cargar Audio';
    ['modal-title-input', 'modal-artist', 'modal-album', 'modal-year', 'modal-bpm', 'modal-notes'].forEach(fid => { const el = $(fid); if (el) el.value = ''; });
    if ($('modal-lang')) $('modal-lang').value = 'es';
    if ($('modal-cat1')) $('modal-cat1').value = '';
    if ($('modal-cat2')) $('modal-cat2').value = '';
    if ($('modal-cat3')) $('modal-cat3').value = '';
    if ($('modal-voz')) $('modal-voz').value = '';
    if ($('modal-cue-intro')) $('modal-cue-intro').value = '0';
    if ($('modal-cue-mix')) $('modal-cue-mix').value = '0';
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'block';
    if ($('modal-file-info')) $('modal-file-info').style.display = 'none';
    if ($('modal-staging-field')) $('modal-staging-field').style.display = 'block';
    if ($('modal-duracion-field')) $('modal-duracion-field').style.display = 'block';
    if ($('modal-archivo-path')) $('modal-archivo-path').value = '';
    if ($('modal-duracion')) $('modal-duracion').value = '';
  }
  overlay.classList.add('active');
  overlay.style.display = 'flex';
  overlay.style.zIndex = '9999';
  setTimeout(() => { const inp = $('modal-title-input'); if (inp) inp.focus(); }, 120);
}

function closeModal() {
  const overlay = getOverlay();
  if (!overlay) return;
  overlay.classList.remove('active');
  overlay.style.display = '';
  overlay.style.zIndex = '';
  editingId = null;
  selectedFile = null;
  if (typeof wfDestroy === 'function') wfDestroy();
}

async function saveModal() {
  const titleVal = $('modal-title-input')?.value.trim();
  const artistVal = $('modal-artist')?.value.trim();
  if (!titleVal || !artistVal) { showToast('Título y artista son obligatorios', 'error'); return; }
  const bpmVal = +($('modal-bpm')?.value) || null;
  const yearVal = $('modal-year')?.value.trim() || null;
  if (!editingId && !selectedFile) { showToast('Debes seleccionar un archivo de audio', 'error'); return; }
  const formData = new FormData();
  formData.append('titulo', titleVal);
  formData.append('artista', artistVal);
  const albumVal = $('modal-album')?.value.trim();
  if (albumVal) formData.append('album', albumVal);
  if (bpmVal && bpmVal >= 40 && bpmVal <= 300) formData.append('bpm', bpmVal);
  if (yearVal) formData.append('fecha_lanzamiento', yearVal);
  if ($('modal-cat1')?.value) formData.append('cat1', $('modal-cat1').value);
  if ($('modal-cat2')?.value) formData.append('cat2', $('modal-cat2').value);
  if ($('modal-cat3')?.value) formData.append('cat3', $('modal-cat3').value);
  if ($('modal-voz')?.value) formData.append('voz', $('modal-voz').value);
  if (typeof wfGetMarkers === 'function') {
    const mk = wfGetMarkers();
    if (mk.intro > 0) formData.append('intro', mk.intro);
    if (mk.outro > 0) formData.append('outro', mk.outro);
    if (mk.hook > 0) formData.append('hook', mk.hook);
  }
  if (!editingId && selectedFile) {
    formData.append('file', selectedFile);
    formData.append('duracion', +($('modal-duracion')?.value) || 0);
  }
  const btnSave = $('bm-modal-save');
  if (btnSave) { btnSave.disabled = true; btnSave.textContent = 'Guardando…'; }
  try {
    let res, data;
    if (editingId) {
      const jsonPayload = {};
      formData.forEach((v, k) => jsonPayload[k] = v);
      res = await fetch(`${API_BASE}/api/v1/audios/${editingId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(jsonPayload) });
    } else {
      res = await fetch(`${API_BASE}/api/v1/audios`, { method: 'POST', body: formData });
    }
    data = await res.json();
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === 'object' ? (detail.mensaje || JSON.stringify(detail)) : (detail || `Error ${res.status}`);
      showToast(msg, 'error');
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
    if (btnSave) { btnSave.disabled = false; btnSave.textContent = 'Guardar'; }
  }
}

const loadAudios = async () => {
  try {
    const res = await fetch(`${API_BASE}/api/v1/audios?limit=500&offset=0`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    library = (data.data || []).map(a => ({
      id: a.id,
      title: a.titulo || '—',
      artist: a.artista || '—',
      album: a.album || '',
      duration: a.duracion || 0,
      bpm: a.bpm || null,
      year: a.fecha_lanzamiento || null,
      cat1: a.cat1 || '',
      cat2: a.cat2 || '',
      cat3: a.cat3 || '',
      voz: a.voz || '',
      archivo_path: a.archivo_path || '',
      energy: 5, intro: 0, mix: 0, lang: 'es',
    }));
    filtered = [...library];
    applyFilters();
    const countEl = getCountEl();
    if (countEl) countEl.textContent = library.length;
  } catch (err) {
    console.error('[BM·loadAudios]', err);
    showToast('No se pudo cargar la biblioteca', 'error');
  }
};

const initApp = async () => {
  const waitForWF = () => {
    const container = document.getElementById('waveform');
    if (container && typeof wfInit === 'function') {
      window.wfInit();
      console.log(">>> [SISTEMA] Ónix FM: WaveSurfer inicializado (DOM listo)");
    } else {
      setTimeout(waitForWF, 80);
    }
  };
  waitForWF();

  setStatus('Cargando schema de categorías…', false);
  try {
    const schRes = await fetch(`${API_BASE}/api/v1/config/biblioteca/schema`);
    if (schRes.ok) schema = await schRes.json();
  } catch (err) { console.warn('[BM·init] Error schema:', err.message); }
  buildFilters();
  buildModalSelects();
  buildEnergySel();
  setPlayerInfo(null);
  setStatus('Cargando biblioteca…', false);
  await loadAudios();
  const n = library.length;
  setStatus(`Sistema listo · ${n} pista${n !== 1 ? 's' : ''} cargada${n !== 1 ? 's' : ''}`, false);
  showToast(`Biblioteca Musical lista · ${n} pistas`, 'success');
};

document.addEventListener('click', e => {
  if (e.target.closest('#bm-btn-add')) { openModal(); return; }
  if (e.target.closest('#bm-modal-close') || e.target.closest('#bm-modal-cancel')) { closeModal(); return; }
  if (e.target.closest('#bm-modal-save')) { saveModal(); return; }
  const overlay = getOverlay();
  if (overlay && e.target === overlay) { closeModal(); return; }
  const playBtn = e.target.closest('.bm-play-btn');
  if (playBtn && playBtn.closest('#bm-tbody')) {
    const track = library.find(t => t.id === +playBtn.dataset.id);
    if (track) playTrack(track);
    return;
  }
  const editBtn = e.target.closest('.bm-action-edit');
  if (editBtn) { openModal(+editBtn.dataset.id); return; }
  const delBtn = e.target.closest('.bm-action-del');
  if (delBtn) {
    const id = +delBtn.dataset.id;
    const track = library.find(t => t.id === id);
    if (!track) return;
    if (!confirm(`¿Eliminar "${track.title}"?`)) return;
    if (playingId === id) stopPlayer();
    fetch(`${API_BASE}/api/v1/audios/${id}`, { method: 'DELETE' })
      .then(async res => {
        if (!res.ok) { const data = await res.json().catch(() => ({})); showToast(data.detail || `Error ${res.status} al eliminar`, 'error'); return; }
        wsCmd('DELETE', { id });
        showToast(`Eliminado: ${track.title}`, 'error');
        loadAudios();
      })
      .catch(() => showToast('Error de conexión al eliminar', 'error'));
    return;
  }
  const th = e.target.closest('.bm-table thead th[data-sort]');
  if (th) {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = 1; }
    document.querySelectorAll('.bm-table thead th').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
    th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
    applyFilters();
    return;
  }
  if (e.target.closest('#bm-player-toggle')) { if (playingId) stopPlayer(); return; }
  const tl = e.target.closest('#bm-timeline');
  if (tl && playingId) {
    const rect = tl.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    if (audioEl && audioEl.duration) audioEl.currentTime = pct * audioEl.duration;
    return;
  }
  if (e.target.closest('#bm-dropzone')) {
    if (e.target.id === 'modal-file') return;
    const fi = $('modal-file');
    if (fi) fi.click();
    return;
  }
  const energyBtn = e.target.closest('.bm-energy-btn');
  if (energyBtn?.dataset.val) { setEnergy(+energyBtn.dataset.val); return; }
  const cueBtn = e.target.closest('.bm-cue__mark-btn');
  if (cueBtn) {
    const cue = cueBtn.dataset.cue;
    const input = cue === 'intro' ? $('modal-cue-intro') : $('modal-cue-mix');
    if (input) {
      if (audioEl && !isNaN(audioEl.currentTime)) {
        input.value = audioEl.currentTime.toFixed(1);
        showToast(`Punto ${cue} marcado: ${input.value}s`, 'info');
      } else {
        input.value = (Math.random() * 10 + 1).toFixed(1);
        showToast(`Demo: punto ${cue} = ${input.value}s`, 'info');
      }
    }
    return;
  }
});

const FILTER_IDS = new Set(['bm-search', 'bm-filter-cat1', 'bm-filter-cat2', 'bm-filter-cat3', 'bm-filter-voz']);
document.addEventListener('input', e => { if (FILTER_IDS.has(e.target.id)) applyFilters(); });
document.addEventListener('change', e => {
  if (FILTER_IDS.has(e.target.id)) applyFilters();
  if (e.target.id === 'modal-file' && e.target.files?.[0]) handleFile(e.target.files[0]);
});

document.addEventListener('dragover', e => { const dz = e.target.closest('#bm-dropzone'); if (dz) { e.preventDefault(); dz.classList.add('drag-over'); } });
document.addEventListener('dragleave', e => { const dz = e.target.closest('#bm-dropzone'); if (dz) dz.classList.remove('drag-over'); });
document.addEventListener('drop', e => {
  const dz = e.target.closest('#bm-dropzone');
  if (dz) { e.preventDefault(); dz.classList.remove('drag-over'); handleFile(e.dataTransfer.files[0]); }
});

document.addEventListener('keydown', e => {
  const overlay = getOverlay();
  const isOpen = overlay?.classList.contains('active');
  if (e.key === 'Escape' && isOpen) { closeModal(); return; }
  if (e.key === 'n' && e.ctrlKey && !isOpen) { e.preventDefault(); openModal(); return; }
  if (e.key === ' ' && !isOpen && e.target === document.body) { e.preventDefault(); if (playingId) stopPlayer(); }
});

const handleFile = file => {
  if (!file || !file.type.startsWith('audio/')) { showToast('El archivo seleccionado no es audio', 'error'); return; }
  selectedFile = file;
  const info = $('modal-file-info');
  if (info) { info.textContent = `✓ ${file.name}  ·  ${(file.size / 1048576).toFixed(1)} MB`; info.style.display = 'block'; }
  const archivoPat = $('modal-archivo-path');
  if (archivoPat && !archivoPat.value) archivoPat.value = file.name;
  const titleInp = $('modal-title-input');
  if (titleInp && !titleInp.value) titleInp.value = file.name.replace(/\.[^.]+$/, '');
  if (typeof window.wfLoadFile === 'function') window.wfLoadFile(file);
  wsCmd('FILE_SELECTED', { name: file.name, size: file.size });
};

const wsCmd = (cmd, data = {}) => {
  const msg = { module: 'biblioteca-musical', cmd, ts: Date.now(), data };
  if (window.ShellWS && typeof window.ShellWS.send === 'function') {
    window.ShellWS.send(JSON.stringify(msg));
    setStatus(`WS ▶ ${cmd}`, true);
  } else {
    console.info('[BM·WS]', msg);
    setStatus(`[SIM] ${cmd} · ${data.title || data.id || ''}`, false);
  }
};

const setStatus = (msg, live = true) => {
  const el = getStatusMsg();
  if (el) el.textContent = msg;
  if (live) setTimeout(() => { const el2 = getStatusMsg(); if (el2) el2.textContent = 'Sistema listo · WebSocket conectado'; }, 3000);
};

const showToast = (msg, type = 'info') => {
  const icons = { success: '✓', error: '✕', info: '•' };
  const colorMap = { success: 'accent-green', error: 'accent-red', info: 'accent-cyan' };
  const t = document.createElement('div');
  t.className = `bm-toast bm-toast--${type}`;
  t.innerHTML = `<span style="color:var(--${colorMap[type] || 'accent-cyan'})">${icons[type] || '•'}</span>${msg}`;
  const container = $('bm-toasts');
  if (container) container.appendChild(t);
  setTimeout(() => {
    t.style.cssText += 'opacity:0;transform:translateX(20px);transition:0.3s';
    setTimeout(() => t.remove(), 350);
  }, 2800);
};

window.openModal = openModal;
window.closeModal = closeModal;
window.saveModal = saveModal;

console.log('>>> [SISTEMA] Ónix FM: Funciones vinculadas al objeto window correctamente.');
initApp();
