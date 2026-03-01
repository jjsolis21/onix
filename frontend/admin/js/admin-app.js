// ONIX/frontend/admin/js/admin-app.js

const Shell = {
  // Configuración base
  config: {
    basePath: 'sections/',
    containerId: 'module-view', // El ID del main en tu index.html
    activeClass: 'active'
  },

  init() {
    console.log("Ónix FM Shell — Sistema Iniciado");
    this.bindEvents();
    // Cargar por defecto la sección 01
    this.loadSection('01-estado-global');
  },

  bindEvents() {
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const module = btn.getAttribute('data-module');
        if (module) {
          // Actualizar UI del menú
          document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');

          // Mapeo de nombres a carpetas
          const routes = {
            'dashboard': '01-estado-global',
            'biblioteca': '02-biblioteca-musical',
            'pautas': '03-programacion-pautas',
            'cartuchera': '04-editor-cartuchera',
            'logs': '05-historial-emision',
            'engine': '06-motor-audio'
          };

          this.loadSection(routes[module]);
        }
      });
    });
  },

  async loadSection(sectionName) {
    const container = document.getElementById(this.config.containerId);
    if (!container) return;

    try {
      const url = `${this.config.basePath}${sectionName}/${sectionName.split('-').slice(1).join('-')}.html`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`No se pudo cargar la pieza: ${sectionName}`);

      const html = await response.text();
      container.innerHTML = html;

      // Actualizar título en el Topbar si existe
      const titleEl = document.getElementById('module-title');
      if (titleEl) titleEl.textContent = sectionName.replace(/-/g, ' ').toUpperCase().slice(3);

    } catch (error) {
      container.innerHTML = `<div class="error">Error al engranar pieza: ${error.message}</div>`;
    }
  }
};

document.addEventListener('DOMContentLoaded', () => Shell.init());

/* Lógica Global Biblioteca Musical */
'use strict';

window.openModal = openModal;
window.closeModal = closeModal;
window.saveModal = saveModal;
console.log('>>> [SISTEMA] Ónix FM: Funciones vinculadas al objeto window correctamente.');

/* ══════════════════════════════════════════════════════════════
     §1 · CONFIGURACIÓN DE API
     API_BASE ya está declarada en index.html
  ══════════════════════════════════════════════════════════════ */

// schema se llena en initApp() con GET /api/v1/config/biblioteca/schema
// Estructura: { categorias: [{ nombre_interno, etiqueta_visible, valores:[{valor}] }] }
let schema = { categorias: [] };

// Ayudantes para consultar el schema dinámico
const getCatValues = nombre => {
  const cat = schema.categorias.find(c => c.nombre_interno === nombre);
  return cat ? cat.valores.map(v => v.valor) : [];
};
const getCatLabel = nombre => {
  const cat = schema.categorias.find(c => c.nombre_interno === nombre);
  return cat ? cat.etiqueta_visible : nombre;
};

/* ══════════════════════════════════════════════════════════════
   §2 · ESTADO DE BIBLIOTECA
   Vacío hasta que initApp() completa la carga desde la API.
══════════════════════════════════════════════════════════════ */
let library = [];
let filtered = [];
let sortKey = null;
let sortDir = 1;
let editingId = null;
let playingId = null;
let audioEl = null;
let playerTimer = null;
let currentEnergy = null;

/* ══════════════════════════════════════════════════════════════
   §3 · HELPERS DE DOM
   REGLA DE ORO: Cada función busca su nodo EN EL MOMENTO
   en que se invoca. Nunca se guarda una referencia fija.
   Esto es esencial porque el Shell inyecta este HTML
   dinámicamente; los nodos pueden recrearse en cualquier momento.
══════════════════════════════════════════════════════════════ */
const $ = id => document.getElementById(id);
const getOverlay = () => document.getElementById('bm-overlay');
const getTbody = () => document.getElementById('bm-tbody');
const getCountEl = () => document.getElementById('bm-count');
const getStatusMsg = () => document.getElementById('bm-status-msg');
const getEmptyEl = () => document.getElementById('bm-empty');

/* ══════════════════════════════════════════════════════════════
   §4 · CONSTRUCCIÓN DE SELECTS
══════════════════════════════════════════════════════════════ */
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
  // Usa el schema cargado de la API; si aún está vacío los selects quedan solo con "Todos"
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

/* ══════════════════════════════════════════════════════════════
   §5 · SELECTOR DE ENERGÍA
══════════════════════════════════════════════════════════════ */
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
  document.querySelectorAll('.bm-energy-btn').forEach(b => {
    b.classList.toggle('sel', +b.dataset.val === val);
  });
  const labels = ['', 'Tranquilo', 'Suave', 'Ligero', 'Moderado', 'Animado', 'Dinámico', 'Intenso', 'Potente', '¡Máximo!'];
  const lbl = $('modal-energy-label');
  if (lbl) lbl.textContent = `${val} — ${labels[val] ?? ''}`;
};

/* ══════════════════════════════════════════════════════════════
   §6 · RENDERIZADO DE TABLA
══════════════════════════════════════════════════════════════ */
const fmtDuration = s => {
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
};

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
          <button class="bm-play-btn ${playingId === t.id ? 'playing' : ''}"
            data-id="${t.id}" title="Preescucha">
            ${playingId === t.id
      ? `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`
      : `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`}
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

/* ══════════════════════════════════════════════════════════════
   §7 · FILTRADO Y ORDENAMIENTO
══════════════════════════════════════════════════════════════ */
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

/* ══════════════════════════════════════════════════════════════
   §8 · PREESCUCHA DE AUDIO (Mini Player)
══════════════════════════════════════════════════════════════ */
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
  if (prevId) {
    getTbody()?.querySelector(`tr[data-id="${prevId}"]`)?.classList.remove('bm-row--playing');
  }
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
    if (t >= track.duration) {
      clearInterval(playerTimer);
      stopPlayer();
      setPlayerInfo(null);
      render();
      return;
    }
    updateTimeline(t, track.duration);
  }, 500);
  if ($('bm-player-icon')) {
    $('bm-player-icon').innerHTML =
      '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';
  }
};

const playTrack = track => {
  if (playingId === track.id) { stopPlayer(); setPlayerInfo(null); return; }
  stopPlayer();
  playingId = track.id;
  setPlayerInfo(track);

  audioEl = new Audio(`/api/v1/audios/${track.id}/stream`);
  audioEl.volume = 0.9;
  audioEl.play().catch(() => simulatePlayback(track));
  audioEl.addEventListener('timeupdate', () => updateTimeline(audioEl.currentTime, track.duration));
  audioEl.addEventListener('ended', () => { stopPlayer(); setPlayerInfo(null); render(); });
  audioEl.addEventListener('error', () => simulatePlayback(track));

  render();
  wsCmd('PREVIEW', { id: track.id });
  showToast(`▶ ${track.title}`, 'info');
};

/* ══════════════════════════════════════════════════════════════
   §9 · MODAL: ABRIR, CERRAR, GUARDAR
 
   openModal / closeModal siempre buscan #bm-overlay en tiempo
   de ejecución con getOverlay(). Nunca almacenan la referencia.
 
   FIX 4 — FUERZA BRUTA: además de añadir la clase .active,
   se asigna overlay.style.display = 'flex' como estilo inline.
   Un estilo inline tiene la mayor especificidad posible en CSS
   (supera cualquier regla de hoja de estilos, incluidas las del
   Shell). Esto garantiza la visibilidad del modal incluso si
   el Shell tiene reglas que anulan display en elementos fixed.
══════════════════════════════════════════════════════════════ */
function openModal(id = null) {
  console.log(">>> Abriendo Modal Ónix FM");
  const overlay = getOverlay();
  if (!overlay) {
    console.warn('[BM] openModal: #bm-overlay no encontrado en el DOM');
    return;
  }

  editingId = id;
  buildModalSelects();
  buildEnergySel();
  currentEnergy = null;

  if (id) {
    // Modo edición
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
    // En modo edición los campos de staging no son relevantes (el archivo ya existe en library)
    if ($('modal-staging-field')) $('modal-staging-field').style.display = 'none';
    if ($('modal-duracion-field')) $('modal-duracion-field').style.display = 'none';
    setEnergy(t.energy);
  } else {
    // Modo creación
    if ($('modal-badge')) $('modal-badge').textContent = 'NUEVO';
    if ($('modal-title')) $('modal-title').textContent = 'Cargar Audio';
    ['modal-title-input', 'modal-artist', 'modal-album', 'modal-year', 'modal-bpm', 'modal-notes']
      .forEach(fid => { const el = $(fid); if (el) el.value = ''; });
    if ($('modal-lang')) $('modal-lang').value = 'es';
    if ($('modal-cat1')) $('modal-cat1').value = '';
    if ($('modal-cat2')) $('modal-cat2').value = '';
    if ($('modal-cat3')) $('modal-cat3').value = '';
    if ($('modal-voz')) $('modal-voz').value = '';
    if ($('modal-cue-intro')) $('modal-cue-intro').value = '0';
    if ($('modal-cue-mix')) $('modal-cue-mix').value = '0';
    if ($('modal-dropzone-wrap')) $('modal-dropzone-wrap').style.display = 'block';
    if ($('modal-file-info')) $('modal-file-info').style.display = 'none';
    // En modo creación mostramos los campos de staging que el backend requiere
    if ($('modal-staging-field')) { $('modal-staging-field').style.display = 'block'; }
    if ($('modal-duracion-field')) { $('modal-duracion-field').style.display = 'block'; }
    if ($('modal-archivo-path')) $('modal-archivo-path').value = '';
    if ($('modal-duracion')) $('modal-duracion').value = '';
  }

  // Añadir clase semántica (útil para CSS y tests)
  overlay.classList.add('active');

  /*
   * FIX 4 — FUERZA BRUTA
   * El estilo inline prevalece sobre CUALQUIER regla CSS externa,
   * incluidas las del Shell que pudieran sobreescribir el display.
   * Es el seguro de último recurso cuando el cascading falla.
   */
  overlay.style.display = 'flex';
  overlay.style.zIndex = '9999';

  // Focus al primer campo editable tras la transición de entrada
  setTimeout(() => { const inp = $('modal-title-input'); if (inp) inp.focus(); }, 120);
};

function closeModal() {
  const overlay = getOverlay();
  if (!overlay) return;

  overlay.classList.remove('active');
  // Limpiar los estilos inline que pusimos en openModal
  overlay.style.display = '';
  overlay.style.zIndex = '';

  editingId = null;
  selectedFile = null;
};

async function saveModal() {
  const titleVal = $('modal-title-input')?.value.trim();
  const artistVal = $('modal-artist')?.value.trim();
  if (!titleVal || !artistVal) {
    showToast('Título y artista son obligatorios', 'error');
    return;
  }

  // Mapeo frontend → campos esperados por el backend (API v1)
  // El backend usa: titulo, artista, album, bpm, fecha_lanzamiento,
  // cat1, cat2, cat3, voz, archivo_path (requerido en POST)
  const bpmVal = +($('modal-bpm')?.value) || null;
  const yearVal = $('modal-year')?.value.trim() || null;

  if (!editingId) {
    if (!selectedFile) {
      showToast('Debes seleccionar un archivo de audio', 'error');
      return;
    }
  }

  const formData = new FormData();
  formData.append('titulo', titleVal);
  formData.append('artista', artistVal);

  const albumVal = $('modal-album')?.value.trim();
  if (albumVal) formData.append('album', albumVal);

  // El backend espera form data, valores opcionales no se envían si vacíos
  if (bpmVal && bpmVal >= 40 && bpmVal <= 300) formData.append('bpm', bpmVal);
  if (yearVal) formData.append('fecha_lanzamiento', yearVal);
  if ($('modal-cat1')?.value) formData.append('cat1', $('modal-cat1').value);
  if ($('modal-cat2')?.value) formData.append('cat2', $('modal-cat2').value);
  if ($('modal-cat3')?.value) formData.append('cat3', $('modal-cat3').value);
  if ($('modal-voz')?.value) formData.append('voz', $('modal-voz').value);

  // Adjuntar archivo solo si es creación
  if (!editingId && selectedFile) {
    formData.append('file', selectedFile);
    formData.append('duracion', +($('modal-duracion')?.value) || 0);
  }

  // Deshabilitar botón guardar mientras la petición está en vuelo
  const btnSave = $('bm-modal-save');
  if (btnSave) { btnSave.disabled = true; btnSave.textContent = 'Guardando…'; }

  try {
    let res, data;
    if (editingId) {
      // PUT para metadata sí usa JSON 
      const jsonPayload = {};
      formData.forEach((value, key) => jsonPayload[key] = value);
      res = await fetch(`${API_BASE}/api/v1/audios/${editingId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(jsonPayload),
      });
    } else {
      res = await fetch(`${API_BASE}/api/v1/audios`, {
        method: 'POST',
        body: formData,
      });
    }

    data = await res.json();

    if (!res.ok) {
      // El backend devuelve detail con mensaje descriptivo
      const detail = data.detail;
      const msg = typeof detail === 'object'
        ? (detail.mensaje || JSON.stringify(detail))
        : (detail || `Error ${res.status}`);
      showToast(msg, 'error');
      return;
    }

    // Éxito: refrescar la tabla desde la API para mantener consistencia
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
};

/* ══════════════════════════════════════════════════════════════
   §9-A · CARGA DE AUDIOS DESDE API
   Obtiene la lista paginada de GET /api/v1/audios y mapea los
   nombres de columna del backend (titulo, artista, duracion…)
   a los nombres internos del frontend (title, artist, duration…).
   Los campos que no tienen equivalente en la API (energy, intro,
   mix, lang) se inicializan con valores neutros; si en el futuro
   el backend los soporta sólo hay que añadirlos al map.
══════════════════════════════════════════════════════════════ */
const loadAudios = async () => {
  try {
    // Pedimos hasta 500 pistas; si la biblioteca crece habrá que paginar
    const res = await fetch(`${API_BASE}/api/v1/audios?limit=500&offset=0`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // Mapeo backend → frontend
    // El backend expone subgenero→cat1, categoria→cat2, cat3→cat3, genero_vocal→voz
    // porque la API ya unifica los alias Jazler en el alias cat1/cat2/cat3/voz
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
      // Campos sin equivalente en API v1 — valores por defecto
      energy: 5,
      intro: 0,
      mix: 0,
      lang: 'es',
    }));

    filtered = [...library];
    applyFilters();
    const countEl = getCountEl();
    if (countEl) countEl.textContent = library.length;

  } catch (err) {
    console.error('[BM·loadAudios]', err);
    showToast('No se pudo cargar la biblioteca desde la API', 'error');
  }
};

/* ══════════════════════════════════════════════════════════════
   §9-B · INICIALIZACIÓN ASÍNCRONA
   Secuencia correcta:
     1. GET /schema  → llena `schema` con categorías dinámicas
     2. buildFilters / buildModalSelects  → usan el schema real
     3. GET /audios  → llena la tabla
   Si el backend no está disponible muestra un toast y deja la
   UI funcional pero vacía (no bloquea el render).
══════════════════════════════════════════════════════════════ */
const initApp = async () => {
  setStatus('Cargando schema de categorías…', false);

  try {
    const schRes = await fetch(`${API_BASE}/api/v1/config/biblioteca/schema`);
    if (schRes.ok) {
      schema = await schRes.json();
      console.info('[BM·init] Schema cargado:', schema.categorias?.length, 'categorías');
    } else {
      console.warn('[BM·init] Schema no disponible, usando selects vacíos');
    }
  } catch (err) {
    console.warn('[BM·init] Error cargando schema:', err.message);
  }

  // Con el schema (o sin él) construimos los selects
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

/* ══════════════════════════════════════════════════════════════
   §10 · DELEGACIÓN GLOBAL DE EVENTOS — CLICK
 
   FIX 2 — DELEGACIÓN REESCRITA
   Un único listener en `document` reemplaza todos los
   addEventListener directos sobre IDs individuales.
 
   ¿Por qué funciona aunque el elemento no exista aún?
   Porque el evento siempre burbujea desde el nodo origen
   hasta document, y es aquí donde interceptamos y preguntamos:
   "¿el elemento que originó el click coincide con este selector?"
 
   Esto es imprescindible porque el Shell inyecta este HTML
   vía fetch() y innerHTML, lo que destruye y recrea los nodos
   del DOM. Los listeners directos sobre esos nodos se pierden;
   la delegación en document NO se pierde nunca.
══════════════════════════════════════════════════════════════ */
document.addEventListener('click', e => {

  /* ── Abrir modal — botón "Cargar Audio" ───────────────────
     FIX 3 verificado: selector '#bm-btn-add' coincide con el ID
     del botón en el HTML de esta misma sección.               */
  if (e.target.closest('#bm-btn-add')) {
    openModal();
    return;
  }

  /* ── Cerrar modal — botón X ──────────────────────────────── */
  if (e.target.closest('#bm-modal-close')) {
    closeModal();
    return;
  }

  /* ── Cerrar modal — botón Cancelar ──────────────────────── */
  if (e.target.closest('#bm-modal-cancel')) {
    closeModal();
    return;
  }

  /* ── Guardar modal ───────────────────────────────────────── */
  if (e.target.closest('#bm-modal-save')) {
    saveModal();
    return;
  }

  /* ── Cerrar modal — click en el fondo oscuro ─────────────
     e.target === overlay distingue el fondo del contenido
     interior (.bm-modal), que también está dentro del overlay */
  const overlay = getOverlay();
  if (overlay && e.target === overlay) {
    closeModal();
    return;
  }

  /* ── Play/Pause por fila en la tabla ─────────────────────── */
  const playBtn = e.target.closest('.bm-play-btn');
  if (playBtn && playBtn.closest('#bm-tbody')) {
    const track = library.find(t => t.id === +playBtn.dataset.id);
    if (track) playTrack(track);
    return;
  }

  /* ── Editar audio ────────────────────────────────────────── */
  const editBtn = e.target.closest('.bm-action-edit');
  if (editBtn) {
    openModal(+editBtn.dataset.id);
    return;
  }

  /* ── Eliminar audio ──────────────────────────────────────── */
  const delBtn = e.target.closest('.bm-action-del');
  if (delBtn) {
    const id = +delBtn.dataset.id;
    const track = library.find(t => t.id === id);
    if (!track) return;
    if (!confirm(`¿Eliminar "${track.title}"?`)) return;

    if (playingId === id) stopPlayer();

    // Soft-delete en la API — marca activo=0, no destruye el registro
    fetch(`${API_BASE}/api/v1/audios/${id}`, { method: 'DELETE' })
      .then(async res => {
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          showToast(data.detail || `Error ${res.status} al eliminar`, 'error');
          return;
        }
        wsCmd('DELETE', { id });
        showToast(`Eliminado: ${track.title}`, 'error');
        // Recarga la tabla para reflejar el estado real del servidor
        loadAudios();
      })
      .catch(() => showToast('Error de conexión al eliminar', 'error'));

    return;
  }

  /* ── Ordenar por columna (click en th) ───────────────────── */
  const th = e.target.closest('.bm-table thead th[data-sort]');
  if (th) {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir *= -1;
    else { sortKey = key; sortDir = 1; }
    document.querySelectorAll('.bm-table thead th').forEach(h =>
      h.classList.remove('sort-asc', 'sort-desc')
    );
    th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
    applyFilters();
    return;
  }

  /* ── Mini Player: stop ───────────────────────────────────── */
  if (e.target.closest('#bm-player-toggle')) {
    if (playingId) stopPlayer();
    return;
  }

  /* ── Timeline del player: seek ───────────────────────────── */
  const tl = e.target.closest('#bm-timeline');
  if (tl && playingId) {
    const rect = tl.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    if (audioEl && audioEl.duration) audioEl.currentTime = pct * audioEl.duration;
    return;
  }

  /* ── Dropzone: click para abrir selector de archivo ─────── */
  if (e.target.closest('#bm-dropzone')) {
    // Evitar bucle infinito si el click fue sobre el propio input
    if (e.target.id === 'modal-file') return;
    const fileInput = $('modal-file');
    if (fileInput) fileInput.click();
    return;
  }

  /* ── Selector de energía ─────────────────────────────────── */
  const energyBtn = e.target.closest('.bm-energy-btn');
  if (energyBtn && energyBtn.dataset.val) {
    setEnergy(+energyBtn.dataset.val);
    return;
  }

  /* ── Botones de punto de cue ─────────────────────────────── */
  const cueBtn = e.target.closest('.bm-cue__mark-btn');
  if (cueBtn) {
    const cue = cueBtn.dataset.cue;
    const input = cue === 'intro' ? $('modal-cue-intro') : $('modal-cue-mix');
    if (input) {
      if (audioEl && !isNaN(audioEl.currentTime)) {
        input.value = audioEl.currentTime.toFixed(1);
        showToast(`Punto ${cue} marcado: ${input.value}s`, 'info');
      } else {
        // Sin audio real: valor demo para probar el formulario
        input.value = (Math.random() * 10 + 1).toFixed(1);
        showToast(`Demo: punto ${cue} = ${input.value}s`, 'info');
      }
    }
    return;
  }

}); // fin document.addEventListener('click')

/* ══════════════════════════════════════════════════════════════
   §11 · DELEGACIÓN GLOBAL — INPUT / CHANGE
══════════════════════════════════════════════════════════════ */
const FILTER_IDS = new Set(['bm-search', 'bm-filter-cat1', 'bm-filter-cat2', 'bm-filter-cat3', 'bm-filter-voz']);

document.addEventListener('input', e => { if (FILTER_IDS.has(e.target.id)) applyFilters(); });
document.addEventListener('change', e => {
  if (FILTER_IDS.has(e.target.id)) applyFilters();
  if (e.target.id === 'modal-file' && e.target.files?.[0]) handleFile(e.target.files[0]);
});

/* ══════════════════════════════════════════════════════════════
   §12 · DELEGACIÓN GLOBAL — DRAG & DROP
══════════════════════════════════════════════════════════════ */
document.addEventListener('dragover', e => {
  const dz = e.target.closest('#bm-dropzone');
  if (dz) { e.preventDefault(); dz.classList.add('drag-over'); }
});
document.addEventListener('dragleave', e => {
  const dz = e.target.closest('#bm-dropzone');
  if (dz) dz.classList.remove('drag-over');
});
document.addEventListener('drop', e => {
  const dz = e.target.closest('#bm-dropzone');
  if (dz) {
    e.preventDefault();
    dz.classList.remove('drag-over');
    handleFile(e.dataTransfer.files[0]);
  }
});

/* ══════════════════════════════════════════════════════════════
   §13 · ATAJOS DE TECLADO
══════════════════════════════════════════════════════════════ */
document.addEventListener('keydown', e => {
  const overlay = getOverlay(); // búsqueda en tiempo real
  const isOpen = overlay?.classList.contains('active');

  if (e.key === 'Escape' && isOpen) {
    closeModal();
    return;
  }
  if (e.key === 'n' && e.ctrlKey && !isOpen) {
    e.preventDefault();
    openModal();
    return;
  }
  if (e.key === ' ' && !isOpen && e.target === document.body) {
    e.preventDefault();
    if (playingId) stopPlayer();
  }
});

/* ══════════════════════════════════════════════════════════════
   §14 · MANEJO DE ARCHIVO
══════════════════════════════════════════════════════════════ */
let selectedFile = null;

const handleFile = file => {
  if (!file || !file.type.startsWith('audio/')) {
    showToast('El archivo seleccionado no es audio', 'error');
    return;
  }
  selectedFile = file;
  const info = $('modal-file-info');
  if (info) {
    info.textContent = `✓ ${file.name}  ·  ${(file.size / 1048576).toFixed(1)} MB`;
    info.style.display = 'block';
  }
  // Pre-rellenar el campo de ruta con el nombre del archivo.
  // El operador deberá confirmar que el archivo ya fue copiado
  // a la carpeta staging del servidor con este mismo nombre.
  const archivoPat = $('modal-archivo-path');
  if (archivoPat && !archivoPat.value) archivoPat.value = file.name;

  const titleInp = $('modal-title-input');
  if (titleInp && !titleInp.value) titleInp.value = file.name.replace(/\.[^.]+$/, '');
  wsCmd('FILE_SELECTED', { name: file.name, size: file.size });
};

/* ══════════════════════════════════════════════════════════════
   §15 · WEBSOCKET BRIDGE & TOAST
══════════════════════════════════════════════════════════════ */
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
  const el = getStatusMsg(); // búsqueda en tiempo real
  if (el) el.textContent = msg;
  if (live) setTimeout(() => {
    const el2 = getStatusMsg();
    if (el2) el2.textContent = 'Sistema listo · WebSocket conectado';
  }, 3000);
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

/* ══════════════════════════════════════════════════════════════
   §16 · INICIALIZACIÓN
   initApp() es async: primero carga el schema de categorías,
   luego construye los selects con datos reales, luego carga
   los audios. Todo lo que antes era síncrono y usaba mocks
   ahora espera confirmación del backend antes de renderizar.
══════════════════════════════════════════════════════════════ */

/* ── FIX 5 · EXPORTAR AL SCOPE GLOBAL ────────────────────────
 *
 * POR QUÉ EL IIFE ROMPE LA ACCESIBILIDAD:
 *
 *   Todo este módulo está envuelto en (function() { ... })(),
 *   un patrón IIFE que crea un scope léxico privado. Esto es
 *   correcto para evitar contaminar el namespace global, pero
 *   tiene una consecuencia cuando el Shell Antigravy inyecta
 *   el HTML: los atributos onclick del HTML llaman funciones
 *   desde window (el scope global), y openModal / closeModal /
 *   saveModal sólo existen dentro del IIFE — no en window.
 *
 * POR QUÉ ESTO FALLA DENTRO DEL SHELL:
 *
 *   Dos rutas posibles de fallo:
 *
 *   1) El Shell llama stopPropagation() en algún handler propio
 *      antes de que el click burbujee hasta el document listener
 *      registrado en §10. En ese caso la delegación nunca llega
 *      a ejecutar openModal(), aunque la función esté definida.
 *
 *   2) El Shell llama las funciones del módulo por nombre desde
 *      su propio código JS: Antigravy.openModal() o window.openModal().
 *      Sin la exportación, ambas llamadas fallan con
 *      "openModal is not defined".
 *
 * POR QUÉ AQUÍ Y NO ANTES:
 *
 *   Las asignaciones deben ocurrir DESPUÉS de que todas las
 *   funciones han sido declaradas con const. En JS, las
 *   declaraciones const no se hoistan con valor (a diferencia
 *   de function declarations), así que window.openModal = openModal
 *   antes de la const openModal = () => {...} lanzaría un
 *   ReferenceError. Al ponerlas aquí, justo antes de initApp(),
 *   todas las funciones ya están definidas en el scope del IIFE.
 *
 * POR QUÉ window.openModal() EN EL onclick Y NO SOLO openModal():
 *
 *   El atributo onclick="openModal()" busca openModal en el scope
 *   global (window). Escribir onclick="window.openModal()" es
 *   semánticamente idéntico pero más explícito: documenta la
 *   intención y evita confusión si alguien en el futuro intenta
 *   entender por qué el onclick no usa el nombre bare.
 * ──────────────────────────────────────────────────────────── */
initApp();

