/**
 * ============================================================
 * PAUTAS-CONTROLLER.JS  — v2.0  (API REST)
 * Módulo autónomo para la Sección 03 — Programación de Pautas
 *
 * Expone: window.PautasController
 * Dependencias externas: ninguna (vanilla JS puro)
 *
 * Endpoints consumidos:
 *   GET    /api/v1/audios            → catálogo de audios
 *   GET    /api/v1/pautas            → lista de pautas
 *   POST   /api/v1/pautas            → crear pauta
 *   PUT    /api/v1/pautas/:id        → actualizar pauta
 *   DELETE /api/v1/pautas/:id        → eliminar pauta
 * ============================================================
 */
; (function (global) {
  'use strict';

  // ──────────────────────────────────────────────────────────
  // 1. CONFIGURACIÓN DE LA API
  // ──────────────────────────────────────────────────────────

  /** Base URL de la API. Vacío = mismo origen (recomendado en producción). */
  const API_BASE = '';

  /**
   * Categorías que se solicitan al filtrar audios.
   * Si la API no soporta este filtro, se ignora y llegan todos.
   */
  const AUDIO_CATEGORIAS = ['COMERCIAL', 'PUBLICIDAD'];

  /**
   * Wrapper de fetch con manejo centralizado de errores.
   * Lanza un Error con el mensaje del servidor si el status >= 400.
   *
   * @param {string} url
   * @param {RequestInit} [options]
   * @returns {Promise<any>}   JSON parseado de la respuesta
   */
  async function _apiFetch(url, options = {}) {
    const defaults = {
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    };
    const res = await fetch(API_BASE + url, {
      ...defaults, ...options,
      headers: { ...defaults.headers, ...(options.headers ?? {}) },
    });

    if (!res.ok) {
      // Intenta extraer el mensaje de error del cuerpo JSON
      let msg = `HTTP ${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        msg = body?.detail ?? body?.message ?? msg;
      } catch (_) { /* body no es JSON, usar msg por defecto */ }
      throw new Error(msg);
    }

    // 204 No Content — no hay body que parsear
    if (res.status === 204) return null;
    return res.json();
  }

  // ──────────────────────────────────────────────────────────
  // 2. MODELO DE DATOS
  // ──────────────────────────────────────────────────────────

  /**
   * Fábrica del objeto OnixPauta.
   * El campo `id` viene siempre del servidor; nunca lo generamos localmente.
   *
   * @param {Partial<OnixPauta>} data
   * @returns {OnixPauta}
   */
  function createPauta(data = {}) {
    return {
      id: data.id ?? null,  // asignado por el servidor
      cliente: data.cliente ?? '',
      audio_id: data.audio_id ?? null,
      audio_nombre: data.audio_nombre ?? '',
      /**
       * matriz: objeto indexado por "dia-hora" → true
       * Ej: { "1-8": true, "3-14": true }
       * dia: 0=Dom … 6=Sáb  |  hora: 0–23
       */
      matriz: data.matriz ?? {},
      fecha_inicio: data.fecha_inicio ?? '',
      fecha_fin: data.fecha_fin ?? '',
      notas: data.notas ?? '',
      creado_en: data.creado_en ?? new Date().toISOString(),
    };
  }

  // Prime-time: lunes-viernes 07-09 y 17-20
  const PRIME_SLOTS = new Set(
    [1, 2, 3, 4, 5].flatMap(d => [7, 8, 17, 18, 19, 20].map(h => `${d}-${h}`))
  );

  const DIAS = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
  const HORAS = Array.from({ length: 24 }, (_, i) =>
    String(i).padStart(2, '0') + ':00'
  );

  // ──────────────────────────────────────────────────────────
  // 3. ESTADO LOCAL DEL MÓDULO
  // ──────────────────────────────────────────────────────────
  let _pautas = [];        // Array<OnixPauta>  — espejo local del servidor
  let _audios = [];        // Array<OnixAudio>  — catálogo cargado una vez
  let _editingId = null;      // ID de la pauta en edición (null = nueva)
  let _deletingId = null;      // ID pendiente de borrado
  let _filterActive = 'todas';   // 'todas' | 'activa' | 'vencida'
  let _searchTerm = '';
  let _isSaving = false;     // guard para evitar doble-submit

  // Estado de arrastre en la matriz
  const _drag = { active: false, startState: null };

  // REPRODUCTOR DE PREVIEW
  let _previewAudio = null;
  let _isPlaying = false;

  // ──────────────────────────────────────────────────────────
  // 4. REFERENCIAS DOM
  // ──────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  const DOM = {
    // Stats
    statActivas: () => $('statActivas'),
    statVencidas: () => $('statVencidas'),
    statSpots: () => $('statSpots'),
    statClientes: () => $('statClientes'),
    // Lista
    tableBody: () => $('pautasTableBody'),
    tableEmpty: () => $('pautasEmpty'),
    searchInput: () => $('pautaSearch'),
    // Botones de filtro
    filterBtns: () => document.querySelectorAll('.filter-btn'),
    // Modal principal
    modal: () => $('pautaModal'),
    modalTitle: () => $('modalTitle'),
    fCliente: () => $('fCliente'),
    fAudio: () => $('fAudio'),
    fFechaInicio: () => $('fFechaInicio'),
    fFechaFin: () => $('fFechaFin'),
    fNotas: () => $('fNotas'),
    audioDropdown: () => $('audioDropdown'),
    audioList: () => $('audioList'),
    audioHint: () => $('audioHint'),
    audioSearch: () => $('audioSearchInput'),
    matrixGrid: () => $('matrixGrid'),
    matrixSummary: () => $('matrixSummaryText'),
    modalError: () => $('modalError'),
    btnSave: () => $('btnModalSave'),
    btnSaveText: () => $('btnSaveText'),
    // Modal delete
    deleteModal: () => $('deleteModal'),
    deleteCliente: () => $('deleteClienteName'),
  };

  // ──────────────────────────────────────────────────────────
  // 5. HELPERS PUROS
  // ──────────────────────────────────────────────────────────
  function _today() {
    return new Date().toISOString().slice(0, 10);
  }

  function _offsetDate(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function _isActiva(pauta) {
    const hoy = _today();
    return pauta.fecha_inicio <= hoy && pauta.fecha_fin >= hoy;
  }

  function _countSpots(matriz) {
    return Object.values(matriz).filter(Boolean).length;
  }

  function _fmt(isoDate) {
    if (!isoDate) return '—';
    const [y, m, d] = isoDate.split('-');
    return `${d}/${m}/${y}`;
  }

  function _fmtDur(s) {
    if (!s && s !== 0) return '';
    const m = Math.floor(s / 60), sec = s % 60;
    return `${m}:${String(sec).padStart(2, '0')}`;
  }

  function _showEl(el) { el && el.classList.remove('hidden'); }
  function _hideEl(el) { el && el.classList.add('hidden'); }

  function _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function _truncate(str, max) {
    return str.length > max ? str.slice(0, max - 1) + '…' : str;
  }

  /** Muestra u oculta el spinner/bloqueo del botón de guardado */
  function _setSaving(flag) {
    _isSaving = flag;
    const btn = DOM.btnSave();
    const btnText = DOM.btnSaveText();
    if (!btn || !btnText) return;
    btn.disabled = flag;
    btnText.textContent = flag
      ? (_editingId ? 'Actualizando…' : 'Guardando…')
      : (_editingId ? 'Actualizar Pauta' : 'Guardar Pauta');
  }

  /** Muestra un mensaje en #modalError */
  function _showModalError(msg) {
    const el = DOM.modalError();
    if (!el) return;
    el.textContent = '⚠ ' + msg;
    _showEl(el);
  }

  function _clearModalError() {
    const el = DOM.modalError();
    if (!el) return;
    el.textContent = '';
    _hideEl(el);
  }

  // ──────────────────────────────────────────────────────────
  // 6. CAPA DE API — AUDIOS
  // ──────────────────────────────────────────────────────────

  /**
   * Obtiene el catálogo de audios desde la API REST.
   *
   * Intenta primero con el filtro de categorías para traer solo
   * spots comerciales. Si la API devuelve 400/422 (no soporta el
   * parámetro), reintenta sin filtro y registra un aviso.
   *
   * @returns {Promise<Array>}  Array normalizado de audios
   */
  async function fetchAudios() {
    // Construir query string con los filtros de categoría
    const params = new URLSearchParams();
    AUDIO_CATEGORIAS.forEach(cat => params.append('categoria_1', cat));

    let raw;
    try {
      raw = await _apiFetch(`/api/v1/audios?${params.toString()}`);
    } catch (err) {
      // Si el endpoint no soporta el filtro, reintenta sin él
      if (/400|422|unsupported/i.test(err.message)) {
        console.warn('[PautasController] El endpoint /api/v1/audios no soporta ' +
          'filtro por categoría. Cargando todos los audios.', err.message);
        try {
          raw = await _apiFetch('/api/v1/audios');
        } catch (err2) {
          console.error('[PautasController] No se pudo cargar el catálogo de audios.', err2);
          return [];
        }
      } else {
        console.error('[PautasController] Error al cargar audios:', err);
        return [];
      }
    }

    // Normalizar la respuesta filtrada
    const normalizedFiltered = _normalizeAudios(
      Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? [])
    );

    // ── Fallback: si el filtro COMERCIAL/PUBLICIDAD devolvió 0 resultados,
    //    significa que los audios de ESTA instalación usan otros valores en
    //    su campo subgénero. Cargamos todos para que el selector no quede vacío.
    if (normalizedFiltered.length === 0) {
      console.info(
        '[PautasController] El filtro por categoría devolvió 0 audios. ' +
        'Cargando catálogo completo como fallback.'
      );
      try {
        const rawAll = await _apiFetch('/api/v1/audios');
        return _normalizeAudios(
          Array.isArray(rawAll) ? rawAll : (rawAll?.items ?? rawAll?.data ?? [])
        );
      } catch (err3) {
        console.error('[PautasController] Error al cargar catálogo completo:', err3);
        return [];
      }
    }

    return normalizedFiltered;
  }

  /**
   * Normaliza la respuesta del servidor al shape interno:
   * { id, nombre, titulo, artista, duracion }
   */
  function _normalizeAudios(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.map(a => ({
      id: a.id ?? a.audio_id ?? String(a.codigo ?? Math.random()).slice(0, 8),
      nombre: a.nombre ?? a.archivo_path?.split('/').pop() ?? a.title ?? a.titulo ?? `Audio #${a.id}`,
      titulo: a.titulo ?? a.title ?? '',
      artista: a.artista ?? a.artist ?? '',
      duracion: a.duracion ?? _fmtDur(a.duration ?? a.duracion_seg ?? 0),
    }));
  }

  // ──────────────────────────────────────────────────────────
  // 7. CAPA DE API — PAUTAS (CRUD)
  // ──────────────────────────────────────────────────────────

  /**
   * GET /api/v1/pautas
   * Carga la lista completa y reemplaza el estado local.
   */
  async function fetchPautas() {
    try {
      const raw = await _apiFetch('/api/v1/pautas');
      const arr = Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? []);
      _pautas = arr.map(createPauta);
      console.info(`[PautasController] ${_pautas.length} pauta(s) cargada(s) desde la API.`);
    } catch (err) {
      console.error('[PautasController] Error al cargar pautas:', err);
      _pautas = [];
    }
  }

  /**
   * POST /api/v1/pautas
   * Crea una nueva pauta en el servidor.
   *
   * @param {Object} payload  Datos validados de la pauta
   * @returns {Promise<OnixPauta>}  La pauta creada (con ID asignado por el servidor)
   */
  async function _apiCreatePauta(payload) {
    const created = await _apiFetch('/api/v1/pautas', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return createPauta(created);
  }

  /**
   * PUT /api/v1/pautas/:id
   * Actualiza una pauta existente en el servidor.
   *
   * @param {string|number} id
   * @param {Object}        payload
   * @returns {Promise<OnixPauta>}
   */
  async function _apiUpdatePauta(id, payload) {
    const updated = await _apiFetch(`/api/v1/pautas/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    return createPauta(updated);
  }

  /**
   * DELETE /api/v1/pautas/:id
   * Elimina la pauta del servidor.
   *
   * @param {string|number} id
   * @returns {Promise<void>}
   */
  async function _apiDeletePauta(id) {
    await _apiFetch(`/api/v1/pautas/${id}`, { method: 'DELETE' });
  }

  // ──────────────────────────────────────────────────────────
  // 8. RENDERIZADO DE LA LISTA
  // ──────────────────────────────────────────────────────────
  function _renderStats() {
    const activas = _pautas.filter(_isActiva);
    const vencidas = _pautas.filter(p => !_isActiva(p));
    const spotsTotal = _pautas.reduce((a, p) => a + _countSpots(p.matriz), 0);
    const clientes = new Set(_pautas.map(p => p.cliente)).size;

    DOM.statActivas().querySelector('.stat-card__num').textContent = activas.length;
    DOM.statVencidas().querySelector('.stat-card__num').textContent = vencidas.length;
    DOM.statSpots().querySelector('.stat-card__num').textContent = spotsTotal;
    DOM.statClientes().querySelector('.stat-card__num').textContent = clientes;
  }

  function _renderTable() {
    const term = _searchTerm.toLowerCase();

    const visible = _pautas.filter(p => {
      const matchSearch = !term ||
        p.cliente.toLowerCase().includes(term) ||
        p.audio_nombre.toLowerCase().includes(term);

      const estado = _isActiva(p) ? 'activa' : 'vencida';
      const matchFilter = _filterActive === 'todas' || _filterActive === estado;

      return matchSearch && matchFilter;
    });

    const tbody = DOM.tableBody();
    tbody.innerHTML = '';

    if (!visible.length) {
      _showEl(DOM.tableEmpty());
      return;
    }
    _hideEl(DOM.tableEmpty());

    visible.forEach(p => {
      const activa = _isActiva(p);
      const spots = _countSpots(p.matriz);
      const tr = document.createElement('tr');
      tr.dataset.id = p.id;
      tr.innerHTML = `
        <td><strong>${_esc(p.cliente)}</strong></td>
        <td>
          <span title="${_esc(p.audio_nombre)}">
            🎵 ${_esc(_truncate(p.audio_nombre, 28))}
          </span>
        </td>
        <td>${_fmt(p.fecha_inicio)}</td>
        <td>${_fmt(p.fecha_fin)}</td>
        <td>
          <span style="color:var(--p-accent);font-weight:700">${spots}</span>
          <span style="color:var(--p-text-muted);font-size:.75rem"> slots</span>
        </td>
        <td>
          <span class="badge badge--dot ${activa ? 'badge--activa' : 'badge--vencida'}">
            ${activa ? 'Activa' : 'Vencida'}
          </span>
        </td>
        <td>
          <div class="row-actions">
            <button class="btn-row btn-row--edit"   title="Editar"     data-action="edit"   data-id="${p.id}">✏️</button>
            <button class="btn-row btn-row--matrix" title="Ver matriz" data-action="matrix" data-id="${p.id}">📊</button>
            <button class="btn-row btn-row--delete" title="Eliminar"   data-action="delete" data-id="${p.id}">🗑</button>
          </div>
        </td>`;
      tbody.appendChild(tr);
    });
  }

  function _render() {
    _renderStats();
    _renderTable();
  }

  // ──────────────────────────────────────────────────────────
  // 9. CONSTRUCCIÓN DE LA MATRIZ 24×7
  // ──────────────────────────────────────────────────────────
  function _buildMatrix(matrizData = {}) {
    const grid = DOM.matrixGrid();
    grid.innerHTML = '';

    // Fila 0: cabecera de días
    const cornerEl = _matrixCell('', 'matrix-cell--day-header');
    grid.appendChild(cornerEl);
    DIAS.forEach(d => grid.appendChild(_matrixCell(d, 'matrix-cell--day-header')));

    // Filas 1-24: horas × días
    for (let h = 0; h < 24; h++) {
      grid.appendChild(_matrixCell(HORAS[h], 'matrix-cell--hour-label'));

      for (let d = 0; d < 7; d++) {
        const key = `${d}-${h}`;
        const isPrime = PRIME_SLOTS.has(key);
        const isOn = !!matrizData[key];

        const cell = document.createElement('div');
        cell.className = 'matrix-cell';
        cell.dataset.key = key;
        cell.dataset.state = isOn ? 'on' : 'off';
        cell.dataset.prime = isPrime ? 'true' : 'false';
        cell.title = `${DIAS[d]} ${HORAS[h]}${isPrime ? ' ★ Prime Time' : ''}`;

        cell.addEventListener('mousedown', _onCellMouseDown);
        cell.addEventListener('mouseenter', _onCellMouseEnter);
        cell.addEventListener('mouseup', _onCellMouseUp);

        grid.appendChild(cell);
      }
    }

    _updateMatrixSummary(matrizData);
  }

  function _matrixCell(text, cls) {
    const el = document.createElement('div');
    el.className = cls;
    el.textContent = text;
    return el;
  }

  // Drag & toggle
  function _onCellMouseDown(e) {
    e.preventDefault();
    const cell = e.currentTarget;
    _drag.active = true;
    _drag.startState = cell.dataset.state === 'on' ? 'off' : 'on';
    _toggleCell(cell, _drag.startState);
  }

  function _onCellMouseEnter(e) {
    if (!_drag.active) return;
    _toggleCell(e.currentTarget, _drag.startState);
  }

  function _onCellMouseUp() {
    _drag.active = false;
    _updateMatrixSummary(_readMatrixFromDOM());
  }

  function _toggleCell(cell, newState) {
    cell.dataset.state = newState;
  }

  function _readMatrixFromDOM() {
    const result = {};
    document.querySelectorAll('.matrix-cell[data-key]').forEach(cell => {
      if (cell.dataset.state === 'on') result[cell.dataset.key] = true;
    });
    return result;
  }

  function _updateMatrixSummary(matriz) {
    const count = _countSpots(matriz);
    const el = DOM.matrixSummary();
    if (!el) return;
    if (count === 0) {
      el.textContent = 'Sin franjas horarias asignadas';
    } else {
      const days = new Set(Object.keys(matriz).map(k => k.split('-')[0])).size;
      el.textContent =
        `${count} slot${count !== 1 ? 's' : ''} seleccionados en ${days} día${days !== 1 ? 's' : ''}`;
    }
  }

  // ──────────────────────────────────────────────────────────
  // 10. GESTIÓN DEL MODAL PRINCIPAL
  // ──────────────────────────────────────────────────────────
  function _openModal(pautaId = null) {
    _editingId = pautaId;
    _clearModalError();
    _setSaving(false);

    if (pautaId) {
      const p = _pautas.find(x => x.id === pautaId || String(x.id) === String(pautaId));
      if (!p) return;
      DOM.modalTitle().textContent = `Editar Pauta — ${p.cliente}`;
      DOM.fCliente().value = p.cliente;
      DOM.fAudio().value = p.audio_nombre;
      DOM.audioHint().textContent = p.audio_nombre || 'Ningún archivo seleccionado';
      DOM.fFechaInicio().value = p.fecha_inicio;
      DOM.fFechaFin().value = p.fecha_fin;
      DOM.fNotas().value = p.notas;
      DOM.btnSaveText().textContent = 'Actualizar Pauta';
      // Restaurar el id del audio seleccionado para el submit
      DOM.fAudio()._selectedId = p.audio_id;
      DOM.fAudio()._selectedNombre = p.audio_nombre;
      _buildMatrix(p.matriz);
    } else {
      DOM.modalTitle().textContent = 'Nueva Pauta';
      DOM.fCliente().value = '';
      DOM.fAudio().value = '';
      DOM.fAudio()._selectedId = null;
      DOM.fAudio()._selectedNombre = '';
      DOM.audioHint().textContent = 'Ningún archivo seleccionado';
      DOM.fFechaInicio().value = _today();
      DOM.fFechaFin().value = _offsetDate(30);
      DOM.fNotas().value = '';
      DOM.btnSaveText().textContent = 'Guardar Pauta';
      _buildMatrix({});
    }

    _hideEl(DOM.audioDropdown());
    _showEl(DOM.modal());
    DOM.fCliente().focus();
  }

  function _closeModal() {
    _stopAudio();
    _hideEl(DOM.modal());
    _editingId = null;
    _isSaving = false;
    _drag.active = false;
  }

  function _globalMouseUp() {
    _drag.active = false;
    _updateMatrixSummary(_readMatrixFromDOM());
  }

  // ──────────────────────────────────────────────────────────
  // 11. CRUD ASÍNCRONO
  // ──────────────────────────────────────────────────────────

  /**
   * Valida el formulario del modal y llama a la API para
   * crear o actualizar la pauta según corresponda.
   */
  async function _savePauta() {
    if (_isSaving) return;  // evitar doble-submit

    // ── Leer valores del formulario ──
    const clienteRaw = DOM.fCliente().value.trim();
    const audioVal = DOM.fAudio().value.trim();
    const inicio = DOM.fFechaInicio().value;
    const fin = DOM.fFechaFin().value;
    const notas = DOM.fNotas().value.trim().toUpperCase();

    // ── Normalizar a MAYÚSCULAS (identidad de marca) ──
    const cliente = clienteRaw.toUpperCase();

    // ── Validaciones ──
    if (!cliente) { _showModalError('El cliente es obligatorio.'); return; }
    if (!audioVal) { _showModalError('Selecciona un audio para la pauta.'); return; }
    if (!inicio) { _showModalError('La fecha de inicio es obligatoria.'); return; }
    if (!fin) { _showModalError('La fecha de fin es obligatoria.'); return; }
    if (inicio > fin) { _showModalError('La fecha de inicio no puede superar al fin.'); return; }

    const matriz = _readMatrixFromDOM();
    if (!Object.keys(matriz).length) {
      _showModalError('Selecciona al menos un slot en la Matriz de Horarios.');
      return;
    }

    // ── Resolver el audio seleccionado ──
    const fAudio = DOM.fAudio();
    const audioId = fAudio._selectedId ?? null;
    const audioNombre = fAudio._selectedNombre || audioVal;

    /** Payload que se envía a la API */
    const payload = {
      cliente,
      audio_id: audioId,
      audio_nombre: audioNombre,
      fecha_inicio: inicio,
      fecha_fin: fin,
      notas,
      matriz,
    };

    _clearModalError();
    _setSaving(true);

    try {
      if (_editingId) {
        // ── PUT /api/v1/pautas/:id ──
        const updated = await _apiUpdatePauta(_editingId, payload);
        const idx = _pautas.findIndex(
          p => String(p.id) === String(_editingId)
        );
        if (idx !== -1) _pautas[idx] = updated;
        else _pautas.push(updated);  // fallback por si el índice falla

        console.info(`[PautasController] Pauta ${_editingId} actualizada.`);
      } else {
        // ── POST /api/v1/pautas ──
        const created = await _apiCreatePauta(payload);
        _pautas.push(created);
        console.info(`[PautasController] Pauta creada con ID: ${created.id}`);
      }

      _render();
      _closeModal();

    } catch (err) {
      console.error('[PautasController] Error al guardar pauta:', err);
      _showModalError(err.message || 'Error al conectar con el servidor. Intenta nuevamente.');
    } finally {
      _setSaving(false);
    }
  }

  /**
   * DELETE /api/v1/pautas/:id
   * Elimina la pauta del servidor y actualiza la lista local.
   *
   * @param {string|number} id
   */
  async function _deletePauta(id) {
    try {
      await _apiDeletePauta(id);
      _pautas = _pautas.filter(p => String(p.id) !== String(id));
      _render();
      console.info(`[PautasController] Pauta ${id} eliminada.`);
    } catch (err) {
      console.error(`[PautasController] Error al eliminar pauta ${id}:`, err);
      // Notificación no invasiva — el modal de confirmación ya se cerró,
      // mostramos el error en consola. Si quieres un toast, conéctalo aquí.
      alert(`No se pudo eliminar la pauta: ${err.message}`);
    }
  }

  // ──────────────────────────────────────────────────────────
  // 12. SELECTOR DE AUDIO — File Picker local
  //     El usuario elige un archivo de su PC.
  //     Se sube al servidor via POST /api/v1/upload.
  // ──────────────────────────────────────────────────────────

  /** Activa el file picker del sistema operativo */
  function _openFilePicker() {
    const input = $('audioFileInput');
    if (input) input.click();
  }

  /**
   * Se dispara cuando el usuario selecciona un archivo.
   * Muestra progreso, sube el archivo y actualiza el campo.
   */
  async function _onAudioFileSelected(e) {
    const file = e.target.files?.[0];
    if (!file) return;

    // --- Mostrar nombre provisional mientras sube ---
    const fAudio = DOM.fAudio();
    fAudio.value = file.name;
    DOM.audioHint().textContent = `📤 Preparando subida: ${file.name} (${_fmtFileSize(file.size)})`;

    // --- Mostrar barra de progreso ---
    const progressWrap = $('uploadProgress');
    const progressBar = $('uploadProgressBar');
    const progressLbl = $('uploadProgressLabel');
    _showEl(progressWrap);
    progressBar.style.width = '0%';
    progressLbl.textContent = 'Subiendo…';

    // --- Deshabilitar el botón y campo durante la subida ---
    const btnSelect = $('btnSelectAudio');
    if (btnSelect) btnSelect.disabled = true;

    try {
      const formData = new FormData();
      formData.append('file', file);

      // Subida con XMLHttpRequest para tener progreso real
      const result = await _uploadWithProgress(
        '/api/v1/upload',
        formData,
        (pct) => {
          progressBar.style.width = `${pct}%`;
          progressLbl.textContent = `Subiendo… ${pct}%`;
        }
      );

      // --- Éxito ---
      const nombre = result.filename ?? file.name;
      const path = result.archivo_path ?? '';

      fAudio.value = nombre;
      fAudio._selectedId = null;       // no tiene ID en biblioteca
      fAudio._selectedNombre = nombre;
      fAudio._selectedPath = path;       // ruta en disco para la API de pautas

      DOM.audioHint().textContent = `✅ Archivo subido: ${nombre}`;
      progressBar.style.width = '100%';
      progressLbl.textContent = '¡Subida completada!';

      // --- Inicializar reproductor ---
      _initPreviewPlayer(path);

      setTimeout(() => _hideEl(progressWrap), 2000);

    } catch (err) {
      console.error('[PautasController] Error al subir audio:', err);
      fAudio.value = '';
      fAudio._selectedId = null;
      fAudio._selectedNombre = '';
      fAudio._selectedPath = '';
      DOM.audioHint().textContent = `⚠ Error al subir: ${err.message}`;
      progressBar.style.width = '0%';
      progressLbl.textContent = 'Error en la subida';
      progressLbl.style.color = 'var(--p-red)';
      setTimeout(() => {
        _hideEl(progressWrap);
        progressLbl.style.color = '';
      }, 3000);

    } finally {
      if (btnSelect) btnSelect.disabled = false;
      // Limpiar el input file para permitir re-seleccionar el mismo archivo
      e.target.value = '';
    }
  }

  /**
   * Sube un FormData mostrando progreso real via XHR.
   * Retorna una Promise que resuelve con el JSON de respuesta.
   */
  function _uploadWithProgress(url, formData, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });

      xhr.addEventListener('load', () => {
        let json;
        try { json = JSON.parse(xhr.responseText); } catch (_) { json = {}; }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(json);
        } else {
          reject(new Error(json?.detail ?? json?.message ?? `HTTP ${xhr.status}`));
        }
      });

      xhr.addEventListener('error', () => reject(new Error('Error de red al subir el archivo.')));
      xhr.addEventListener('abort', () => reject(new Error('Subida cancelada.')));

      xhr.send(formData);
    });
  }

  /** Formatea bytes a texto legible (KB / MB) */
  function _fmtFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  // ── REPRODUCTOR DE PREVIEW — LÓGICA ────────────────────────
  function _initPreviewPlayer(path) {
    if (!path) return;

    // Detener previo si existe
    _stopAudio();

    const url = path.startsWith('http') ? path : `/stream/${path.replace(/\\/g, '/')}`;
    _previewAudio = new Audio(url);

    _previewAudio.addEventListener('timeupdate', _onAudioTimeUpdate);
    _previewAudio.addEventListener('loadedmetadata', _onAudioLoaded);
    _previewAudio.addEventListener('ended', _onAudioEnded);

    _showEl($('audioPlayer'));
    _updateTimeLabel(0, 0);
  }

  function _playToggle() {
    if (!_previewAudio) return;

    if (_isPlaying) {
      _previewAudio.pause();
      _isPlaying = false;
    } else {
      _previewAudio.play().catch(err => {
        console.error('[Pautas] Error al reproducir preview:', err);
      });
      _isPlaying = true;
    }
    _updatePlayBtn();
  }

  function _stopAudio() {
    if (!_previewAudio) return;
    _previewAudio.pause();
    _previewAudio.currentTime = 0;
    _isPlaying = false;
    _updatePlayBtn();
    _onAudioTimeUpdate();
  }

  function _onAudioTimeUpdate() {
    if (!_previewAudio) return;
    const cur = _previewAudio.currentTime;
    const dur = _previewAudio.duration || 0;

    const pct = dur > 0 ? (cur / dur) * 100 : 0;
    const progressEl = $('audioPlayProgress');
    if (progressEl) progressEl.style.width = `${pct}%`;

    _updateTimeLabel(cur, dur);
  }

  function _onAudioLoaded() {
    _updateTimeLabel(0, _previewAudio.duration);
  }

  function _onAudioEnded() {
    _isPlaying = false;
    _updatePlayBtn();
    _previewAudio.currentTime = 0;
    _onAudioTimeUpdate();
  }

  function _onAudioSeek(e) {
    if (!_previewAudio || !_previewAudio.duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const pct = x / rect.width;
    _previewAudio.currentTime = pct * _previewAudio.duration;
  }

  function _updatePlayBtn() {
    const btn = $('btnAudioPlay');
    if (btn) btn.textContent = _isPlaying ? '⏸' : '▶';
  }

  function _updateTimeLabel(cur, dur) {
    const lbl = $('audioTimeLabel');
    if (lbl) lbl.textContent = `${_fmtDur(cur)} / ${_fmtDur(dur)}`;
  }

  // ── Relleno rápido de la Matriz ──────────────────────────
  function _matrixLV() {
    _applyQuickFill(c => {
      const d = parseInt(c.dataset.key.split('-')[0]);
      return d >= 1 && d <= 5;  // Lun–Vie
    });
  }

  function _matrixFinde() {
    _applyQuickFill(c => {
      const d = parseInt(c.dataset.key.split('-')[0]);
      return d === 0 || d === 6;  // Dom y Sáb
    });
  }

  function _matrixPrimeTime() {
    _applyQuickFill(c => PRIME_SLOTS.has(c.dataset.key));
  }

  function _matrixClear() {
    document.querySelectorAll('.matrix-cell[data-key]').forEach(c => {
      c.dataset.state = 'off';
      c.classList.remove('matrix-cell--flash');
    });
    _updateMatrixSummary({});
  }

  /**
   * Activa las celdas que pasen el predicado con animación de flash neón.
   * Las que no pasen se apagan.
   */
  function _applyQuickFill(predicate) {
    document.querySelectorAll('.matrix-cell[data-key]').forEach((c, i) => {
      const match = predicate(c);
      c.dataset.state = match ? 'on' : 'off';
      if (match) {
        c.classList.remove('matrix-cell--flash');
        setTimeout(() => c.classList.add('matrix-cell--flash'), i % 7 * 6);
      }
    });
    _updateMatrixSummary(_readMatrixFromDOM());
  }

  // ──────────────────────────────────────────────────────────
  // 13. VINCULACIÓN DE EVENTOS
  // ──────────────────────────────────────────────────────────
  function _bindEvents() {
    const btnNueva = $('btnNuevaPauta');
    if (btnNueva) {
      console.log('[PautasController] Botón Nueva Pauta detectado ✅');
      btnNueva.addEventListener('click', () => _openModal());
    } else {
      console.error('[PautasController] ❌ btnNuevaPauta no encontrado en el DOM.');
    }

    // Cierre del modal principal
    $('btnModalClose')?.addEventListener('click', _closeModal);
    $('btnModalCancel')?.addEventListener('click', _closeModal);
    $('pautaModal')?.addEventListener('click', e => {
      if (e.target === $('pautaModal')) _closeModal();
    });

    // Guardar (ahora async)
    $('btnModalSave')?.addEventListener('click', _savePauta);

    // Selector de audio: file picker local
    $('btnSelectAudio')?.addEventListener('click', _openFilePicker);
    $('fAudio')?.addEventListener('click', _openFilePicker);
    $('audioFileInput')?.addEventListener('change', _onAudioFileSelected);

    // Reproductor Preview
    $('btnAudioPlay')?.addEventListener('click', _playToggle);
    $('btnAudioStop')?.addEventListener('click', _stopAudio);
    $('audioTrackBar')?.addEventListener('click', _onAudioSeek);

    // Botonera de relleno rápido
    $('btnQfLV')?.addEventListener('click', _matrixLV);
    $('btnQfFinde')?.addEventListener('click', _matrixFinde);
    $('btnQfPrime')?.addEventListener('click', _matrixPrimeTime);
    $('btnMatrixClear')?.addEventListener('click', _matrixClear);

    // Fin del arrastre global
    document.addEventListener('mouseup', _globalMouseUp);

    // Acciones de fila (delegación de eventos)
    $('pautasTableBody')?.addEventListener('click', e => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      const { action, id } = btn.dataset;
      if (action === 'edit') _openModal(id);
      if (action === 'matrix') _openModal(id);
      if (action === 'delete') _openDeleteModal(id);
    });

    // Búsqueda en tiempo real
    $('pautaSearch')?.addEventListener('input', e => {
      _searchTerm = e.target.value;
      _renderTable();
    });

    // Filtros de estado
    DOM.filterBtns().forEach(btn => {
      btn.addEventListener('click', () => {
        DOM.filterBtns().forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _filterActive = btn.dataset.filter;
        _renderTable();
      });
    });

    // Modal delete
    $('btnDeleteClose')?.addEventListener('click', _closeDeleteModal);
    $('btnDeleteCancel')?.addEventListener('click', _closeDeleteModal);
    $('btnDeleteConfirm')?.addEventListener('click', () => {
      if (_deletingId) _deletePauta(_deletingId);
      _closeDeleteModal();
    });
    $('deleteModal')?.addEventListener('click', e => {
      if (e.target === $('deleteModal')) _closeDeleteModal();
    });

    // Fin del arrastre global
    document.addEventListener('mouseup', _globalMouseUp);
  }

  // ──────────────────────────────────────────────────────────
  // 14. MODAL DELETE
  // ──────────────────────────────────────────────────────────
  function _openDeleteModal(id) {
    _deletingId = id;
    const p = _pautas.find(x => String(x.id) === String(id));
    if (!p) return;
    DOM.deleteCliente().textContent = p.cliente;
    _showEl($('deleteModal'));
  }

  function _closeDeleteModal() {
    _hideEl($('deleteModal'));
    _deletingId = null;
  }

  // ──────────────────────────────────────────────────────────
  // 15. INICIALIZACIÓN
  // ──────────────────────────────────────────────────────────

  /**
   * Punto de entrada del módulo.
   * Carga pautas y audios en paralelo antes de renderizar.
   */
  async function init() {
    console.log('[PautasController] init() llamado — DOM listo para binding.');
    // Cargar pautas y audios en paralelo para reducir el tiempo de arranque
    const [, audios] = await Promise.all([
      fetchPautas(),
      fetchAudios(),
    ]);
    _audios = audios;

    _bindEvents();
    _render();

    console.info(
      '[PautasController] ✅ Módulo inicializado.',
      `Pautas: ${_pautas.length} | Audios: ${_audios.length}`
    );
  }

  // ──────────────────────────────────────────────────────────
  // 16. API PÚBLICA
  // ──────────────────────────────────────────────────────────
  window.PautasController = {
    init,    // Inicia el setup, fetch de pautas y audios, bind de eventos
    save: _savePauta,
    openModal: _openModal,
    closeModal: _closeModal,
    deletePauta: _deletePauta,
    openDeleteModal: _openDeleteModal,   // era _confirmDelete (no existía)
    closeDeleteModal: _closeDeleteModal,

    /**
     * Carga forzada desde la API REST.
     * Útil si otro módulo necesita refrescar las pautas
     * (ej.: después de una importación masiva).
     */
    async reload() {
      await fetchPautas();
      _render();
    },

    /** Expone las pautas actuales (solo lectura) */
    getPautas() { return [..._pautas]; },

    /** Expone el catálogo de audios en memoria */
    getAudios() { return [..._audios]; },

    /** Desmonta el módulo liberando listeners globales */
    destroy() {
      document.removeEventListener('mouseup', _globalMouseUp);
      console.info('[PautasController] Módulo destruido.');
    },
  };

  // ──────────────────────────────────────────────────────────
  // 17. INICIALIZACIÓN MÓDULO (SHELL V3)
  // ──────────────────────────────────────────────────────────
  const panel = document.getElementById('section-panel-03-programacion-pautas');
  if (panel) {
    panel.addEventListener('section:init', () => {
      if (!window.PautasController._initialized) {
        window.PautasController._initialized = true;
        window.PautasController.init().catch(e => {
          console.error('[Pautas] Error en init:', e);
          window.PautasController._initialized = false;
        });
      }
    });

    // Fallback: si el panel ya está activo cuando este script carga
    if (panel.dataset.loaded === 'true' && !window.PautasController._initialized) {
      window.PautasController._initialized = true;
      window.PautasController.init().catch(console.error);
    }
  }

  console.log('[PautasController] CARGADO: Script parseado exitosamente.');

})(window);
