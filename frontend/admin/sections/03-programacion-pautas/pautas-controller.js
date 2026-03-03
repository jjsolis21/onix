/**
 * ============================================================
 * PAUTAS-CONTROLLER.JS
 * Módulo autónomo para la Sección 03 — Programación de Pautas
 *
 * Expone: window.PautasController
 * Dependencias externas: ninguna (vanilla JS puro)
 * ============================================================
 */
;(function (global) {
  'use strict';

  // ──────────────────────────────────────────────────────────
  // 1. MODELO DE DATOS
  // ──────────────────────────────────────────────────────────

  /**
   * Fábrica del objeto OnixPauta
   * @param {Partial<OnixPauta>} data
   * @returns {OnixPauta}
   */
  function createPauta(data = {}) {
    return {
      id:           data.id           || _uid(),
      cliente:      data.cliente      || '',
      audio_id:     data.audio_id     || null,
      audio_nombre: data.audio_nombre || '',
      /**
       * matriz: objeto indexado por "dia-hora" → true/false
       * Ej: { "1-8": true, "3-14": true }
       * dia: 0=Dom … 6=Sáb  |  hora: 0–23
       */
      matriz:       data.matriz       || {},
      fecha_inicio: data.fecha_inicio || '',
      fecha_fin:    data.fecha_fin    || '',
      notas:        data.notas        || '',
      creado_en:    data.creado_en    || new Date().toISOString(),
    };
  }

  // Prime-time: lunes-viernes 07-09 y 17-20
  const PRIME_SLOTS = new Set(
    [1,2,3,4,5].flatMap(d => [7,8,17,18,19,20].map(h => `${d}-${h}`))
  );

  const DIAS  = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];
  const HORAS = Array.from({ length: 24 }, (_, i) =>
    String(i).padStart(2,'0') + ':00'
  );

  // ──────────────────────────────────────────────────────────
  // 2. ESTADO LOCAL DEL MÓDULO
  // ──────────────────────────────────────────────────────────
  let _pautas        = [];        // Array<OnixPauta>
  let _audios        = [];        // Catálogo de audios disponibles
  let _editingId     = null;      // ID de la pauta en edición (null = nueva)
  let _deletingId    = null;      // ID pendiente de borrado
  let _filterActive  = 'todas';   // 'todas' | 'activa' | 'vencida'
  let _searchTerm    = '';

  // Estado de arrastre en la matriz
  const _drag = { active: false, startState: null };

  // ──────────────────────────────────────────────────────────
  // 3. REFERENCIAS DOM
  // ──────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  const DOM = {
    // Stats
    statActivas:   () => $('statActivas'),
    statVencidas:  () => $('statVencidas'),
    statSpots:     () => $('statSpots'),
    statClientes:  () => $('statClientes'),
    // Lista
    tableBody:     () => $('pautasTableBody'),
    tableEmpty:    () => $('pautasEmpty'),
    searchInput:   () => $('pautaSearch'),
    // Botones de filtro
    filterBtns:    () => document.querySelectorAll('.filter-btn'),
    // Modal principal
    modal:         () => $('pautaModal'),
    modalTitle:    () => $('modalTitle'),
    fCliente:      () => $('fCliente'),
    fAudio:        () => $('fAudio'),
    fFechaInicio:  () => $('fFechaInicio'),
    fFechaFin:     () => $('fFechaFin'),
    fNotas:        () => $('fNotas'),
    audioDropdown: () => $('audioDropdown'),
    audioList:     () => $('audioList'),
    audioHint:     () => $('audioHint'),
    audioSearch:   () => $('audioSearchInput'),
    matrixGrid:    () => $('matrixGrid'),
    matrixSummary: () => $('matrixSummaryText'),
    modalError:    () => $('modalError'),
    btnSaveText:   () => $('btnSaveText'),
    // Modal delete
    deleteModal:   () => $('deleteModal'),
    deleteCliente: () => $('deleteClienteName'),
  };

  // ──────────────────────────────────────────────────────────
  // 4. HELPERS
  // ──────────────────────────────────────────────────────────
  function _uid() {
    return 'p_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  function _today() {
    return new Date().toISOString().slice(0, 10);
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
    const [y,m,d] = isoDate.split('-');
    return `${d}/${m}/${y}`;
  }

  function _showEl(el)  { el && el.classList.remove('hidden'); }
  function _hideEl(el)  { el && el.classList.add('hidden');    }

  // Persistencia ligera en localStorage
  const STORAGE_KEY = 'onix_pautas_v1';
  function _save()  { localStorage.setItem(STORAGE_KEY, JSON.stringify(_pautas)); }
  function _load()  {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      _pautas = raw ? JSON.parse(raw) : _seedData();
    } catch { _pautas = _seedData(); }
  }

  // Datos de muestra para demo
  function _seedData() {
    return [
      createPauta({
        cliente: 'Coca-Cola Argentina',
        audio_id: 'aud_001', audio_nombre: 'spot_cocacola_verano.mp3',
        fecha_inicio: _today(),
        fecha_fin: _offsetDate(30),
        matriz: { '1-8':true,'2-8':true,'3-8':true,'4-8':true,'5-8':true,
                  '1-12':true,'3-12':true,'5-12':true,
                  '1-18':true,'2-18':true,'3-18':true,'4-18':true,'5-18':true },
        notas: 'Preferencia en prime time.',
      }),
      createPauta({
        cliente: 'Ford Motors',
        audio_id: 'aud_002', audio_nombre: 'ford_maverick_spot.mp3',
        fecha_inicio: _offsetDate(-40),
        fecha_fin:    _offsetDate(-5),
        matriz: { '1-9':true,'3-9':true,'5-9':true,'1-20':true,'5-20':true },
        notas: '',
      }),
    ];
  }

  function _offsetDate(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  // Catálogo de audios demo
  function _seedAudios() {
    return [
      { id:'aud_001', nombre:'spot_cocacola_verano.mp3',   duracion:'0:30' },
      { id:'aud_002', nombre:'ford_maverick_spot.mp3',     duracion:'0:45' },
      { id:'aud_003', nombre:'banco_nacion_promo.mp3',     duracion:'0:20' },
      { id:'aud_004', nombre:'perfumeria_florencia.mp3',   duracion:'0:15' },
      { id:'aud_005', nombre:'supermercado_dia_oferta.mp3',duracion:'1:00' },
      { id:'aud_006', nombre:'telefonica_fibra.mp3',       duracion:'0:30' },
    ];
  }

  // ──────────────────────────────────────────────────────────
  // 5. RENDERIZADO DE LA LISTA
  // ──────────────────────────────────────────────────────────
  function _renderStats() {
    const activas  = _pautas.filter(_isActiva);
    const vencidas = _pautas.filter(p => !_isActiva(p));
    const spotsTotal = _pautas.reduce((a,p) => a + _countSpots(p.matriz), 0);
    const clientes = new Set(_pautas.map(p => p.cliente)).size;

    DOM.statActivas().querySelector('.stat-card__num').textContent  = activas.length;
    DOM.statVencidas().querySelector('.stat-card__num').textContent = vencidas.length;
    DOM.statSpots().querySelector('.stat-card__num').textContent    = spotsTotal;
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
      const spots  = _countSpots(p.matriz);
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
            <button class="btn-row btn-row--edit"   title="Editar"    data-action="edit"   data-id="${p.id}">✏️</button>
            <button class="btn-row btn-row--matrix" title="Ver matriz" data-action="matrix" data-id="${p.id}">📊</button>
            <button class="btn-row btn-row--delete" title="Eliminar"  data-action="delete" data-id="${p.id}">🗑</button>
          </div>
        </td>`;
      tbody.appendChild(tr);
    });
  }

  function _esc(str) {
    return String(str)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function _truncate(str, max) {
    return str.length > max ? str.slice(0, max - 1) + '…' : str;
  }

  function _render() {
    _renderStats();
    _renderTable();
  }

  // ──────────────────────────────────────────────────────────
  // 6. CONSTRUCCIÓN DE LA MATRIZ 24×7
  // ──────────────────────────────────────────────────────────
  function _buildMatrix(matrizData = {}) {
    const grid = DOM.matrixGrid();
    grid.innerHTML = '';

    // ── Fila 0: cabecera de días ──
    const cornerEl = _matrixCell('', 'matrix-cell--day-header');
    grid.appendChild(cornerEl);
    DIAS.forEach(d => {
      const el = _matrixCell(d, 'matrix-cell--day-header');
      grid.appendChild(el);
    });

    // ── Filas 1-24: horas × días ──
    for (let h = 0; h < 24; h++) {
      // Etiqueta de hora
      const lblEl = _matrixCell(HORAS[h], 'matrix-cell--hour-label');
      grid.appendChild(lblEl);

      // 7 celdas (día 0-6)
      for (let d = 0; d < 7; d++) {
        const key   = `${d}-${h}`;
        const isPrime = PRIME_SLOTS.has(key);
        const isOn    = !!matrizData[key];

        const cell = document.createElement('div');
        cell.className   = 'matrix-cell';
        cell.dataset.key   = key;
        cell.dataset.state = isOn ? 'on' : 'off';
        cell.dataset.prime = isPrime ? 'true' : 'false';
        cell.title = `${DIAS[d]} ${HORAS[h]}${isPrime ? ' ★ Prime Time' : ''}`;

        // Eventos de arrastre
        cell.addEventListener('mousedown', _onCellMouseDown);
        cell.addEventListener('mouseenter', _onCellMouseEnter);
        cell.addEventListener('mouseup',   _onCellMouseUp);

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

  // ── Drag & toggle ──
  function _onCellMouseDown(e) {
    e.preventDefault();
    const cell = e.currentTarget;
    _drag.active     = true;
    _drag.startState = cell.dataset.state === 'on' ? 'off' : 'on'; // toggle inverso
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
    const el    = DOM.matrixSummary();
    if (!el) return;
    if (count === 0) {
      el.textContent = 'Sin franjas horarias asignadas';
    } else {
      const days = new Set(Object.keys(matriz).map(k => k.split('-')[0])).size;
      el.textContent = `${count} slot${count!==1?'s':''} seleccionados en ${days} día${days!==1?'s':''}`;
    }
  }

  // ──────────────────────────────────────────────────────────
  // 7. GESTIÓN DEL MODAL PRINCIPAL
  // ──────────────────────────────────────────────────────────
  function _openModal(pautaId = null) {
    _editingId = pautaId;
    const modal = DOM.modal();

    // Limpiar error previo
    const errEl = DOM.modalError();
    errEl.textContent = '';
    _hideEl(errEl);

    if (pautaId) {
      const p = _pautas.find(x => x.id === pautaId);
      if (!p) return;
      DOM.modalTitle().textContent  = `Editar Pauta — ${p.cliente}`;
      DOM.fCliente().value          = p.cliente;
      DOM.fAudio().value            = p.audio_nombre;
      DOM.audioHint().textContent   = p.audio_nombre || 'Ningún archivo seleccionado';
      DOM.fFechaInicio().value      = p.fecha_inicio;
      DOM.fFechaFin().value         = p.fecha_fin;
      DOM.fNotas().value            = p.notas;
      DOM.btnSaveText().textContent = 'Actualizar Pauta';
      _buildMatrix(p.matriz);
    } else {
      DOM.modalTitle().textContent  = 'Nueva Pauta';
      DOM.fCliente().value          = '';
      DOM.fAudio().value            = '';
      DOM.audioHint().textContent   = 'Ningún archivo seleccionado';
      DOM.fFechaInicio().value      = _today();
      DOM.fFechaFin().value         = _offsetDate(30);
      DOM.fNotas().value            = '';
      DOM.btnSaveText().textContent = 'Guardar Pauta';
      _buildMatrix({});
    }

    _hideEl(DOM.audioDropdown());
    _showEl(modal);
    DOM.fCliente().focus();
  }

  function _closeModal() {
    _hideEl(DOM.modal());
    _editingId = null;
    _drag.active = false;
    document.removeEventListener('mouseup', _globalMouseUp);
  }

  function _globalMouseUp() {
    _drag.active = false;
    _updateMatrixSummary(_readMatrixFromDOM());
  }

  // ──────────────────────────────────────────────────────────
  // 8. CRUD
  // ──────────────────────────────────────────────────────────
  function _savePauta() {
    const cliente  = DOM.fCliente().value.trim();
    const audioVal = DOM.fAudio().value.trim();
    const inicio   = DOM.fFechaInicio().value;
    const fin      = DOM.fFechaFin().value;
    const notas    = DOM.fNotas().value.trim();

    // ── Validaciones ──
    const errEl = DOM.modalError();
    const showErr = msg => {
      errEl.textContent = '⚠ ' + msg;
      _showEl(errEl);
    };

    if (!cliente)    { showErr('El cliente es obligatorio.');                 return; }
    if (!audioVal)   { showErr('Selecciona un audio para la pauta.');         return; }
    if (!inicio)     { showErr('La fecha de inicio es obligatoria.');         return; }
    if (!fin)        { showErr('La fecha de fin es obligatoria.');            return; }
    if (inicio > fin){ showErr('La fecha de inicio no puede superar al fin.'); return; }

    const matriz = _readMatrixFromDOM();
    if (!Object.keys(matriz).length) {
      showErr('Selecciona al menos un slot en la Matriz de Horarios.'); return;
    }

    // Buscar audio en catálogo
    const audioObj = _audios.find(a => a.nombre === audioVal || a.id === audioVal);

    if (_editingId) {
      // UPDATE
      const idx = _pautas.findIndex(p => p.id === _editingId);
      if (idx === -1) return;
      _pautas[idx] = {
        ..._pautas[idx],
        cliente, notas, inicio,
        audio_id:     audioObj?.id     || _pautas[idx].audio_id,
        audio_nombre: audioObj?.nombre || audioVal,
        fecha_inicio: inicio,
        fecha_fin:    fin,
        matriz,
      };
    } else {
      // CREATE
      _pautas.push(createPauta({
        cliente, notas,
        audio_id:     audioObj?.id     || null,
        audio_nombre: audioObj?.nombre || audioVal,
        fecha_inicio: inicio,
        fecha_fin:    fin,
        matriz,
      }));
    }

    _save();
    _render();
    _closeModal();
  }

  function _deletePauta(id) {
    _pautas = _pautas.filter(p => p.id !== id);
    _save();
    _render();
  }

  // ──────────────────────────────────────────────────────────
  // 9. SELECTOR DE AUDIO
  // ──────────────────────────────────────────────────────────
  function _renderAudioList(term = '') {
    const ul = DOM.audioList();
    ul.innerHTML = '';
    const filtered = !term
      ? _audios
      : _audios.filter(a => a.nombre.toLowerCase().includes(term.toLowerCase()));

    if (!filtered.length) {
      ul.innerHTML = '<li style="color:var(--p-text-muted);pointer-events:none">Sin resultados</li>';
      return;
    }

    filtered.forEach(a => {
      const li = document.createElement('li');
      li.dataset.id = a.id;
      li.innerHTML  = `🎵 <span>${_esc(a.nombre)}</span>
                       <small style="margin-left:auto;color:var(--p-text-muted)">${a.duracion}</small>`;
      li.addEventListener('click', () => _selectAudio(a));
      ul.appendChild(li);
    });
  }

  function _selectAudio(audio) {
    DOM.fAudio().value          = audio.nombre;
    DOM.audioHint().textContent = `✔ ${audio.nombre} (${audio.duracion})`;
    _hideEl(DOM.audioDropdown());
  }

  function _toggleAudioDropdown() {
    const dd = DOM.audioDropdown();
    if (dd.classList.contains('hidden')) {
      _renderAudioList();
      _showEl(dd);
      DOM.audioSearch().focus();
    } else {
      _hideEl(dd);
    }
  }

  // ──────────────────────────────────────────────────────────
  // 10. ACCIONES RÁPIDAS DE LA MATRIZ
  // ──────────────────────────────────────────────────────────
  function _matrixClear() {
    document.querySelectorAll('.matrix-cell[data-key]').forEach(c => {
      c.dataset.state = 'off';
    });
    _updateMatrixSummary({});
  }

  function _matrixPrimeTime() {
    document.querySelectorAll('.matrix-cell[data-key]').forEach(c => {
      if (c.dataset.prime === 'true') c.dataset.state = 'on';
    });
    _updateMatrixSummary(_readMatrixFromDOM());
  }

  // ──────────────────────────────────────────────────────────
  // 11. VINCULACIÓN DE EVENTOS
  // ──────────────────────────────────────────────────────────
  function _bindEvents() {
    // Botón nueva pauta
    $('btnNuevaPauta').addEventListener('click', () => _openModal());

    // Cierre de modal
    $('btnModalClose').addEventListener('click',  _closeModal);
    $('btnModalCancel').addEventListener('click', _closeModal);
    DOM.modal().addEventListener('click', e => {
      if (e.target === DOM.modal()) _closeModal();
    });

    // Guardar pauta
    $('btnModalSave').addEventListener('click', _savePauta);

    // Selector de audio
    $('btnSelectAudio').addEventListener('click', _toggleAudioDropdown);
    $('fAudio').addEventListener('click', _toggleAudioDropdown);
    $('audioSearchInput').addEventListener('input', e =>
      _renderAudioList(e.target.value)
    );

    // Acciones de la matriz
    $('btnMatrixClear').addEventListener('click',     _matrixClear);
    $('btnMatrixPrimetime').addEventListener('click', _matrixPrimeTime);

    // Fin del arrastre global
    document.addEventListener('mouseup', _globalMouseUp);

    // Acciones de fila (delegación)
    $('pautasTableBody').addEventListener('click', e => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      const { action, id } = btn.dataset;
      if (action === 'edit')   _openModal(id);
      if (action === 'matrix') _openModal(id);   // mismo modal, modo lectura opcional
      if (action === 'delete') _openDeleteModal(id);
    });

    // Búsqueda en tiempo real
    $('pautaSearch').addEventListener('input', e => {
      _searchTerm = e.target.value;
      _renderTable();
    });

    // Filtros
    DOM.filterBtns().forEach(btn => {
      btn.addEventListener('click', () => {
        DOM.filterBtns().forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _filterActive = btn.dataset.filter;
        _renderTable();
      });
    });

    // Modal delete
    $('btnDeleteClose').addEventListener('click',  _closeDeleteModal);
    $('btnDeleteCancel').addEventListener('click', _closeDeleteModal);
    $('btnDeleteConfirm').addEventListener('click', () => {
      if (_deletingId) _deletePauta(_deletingId);
      _closeDeleteModal();
    });
    $('deleteModal').addEventListener('click', e => {
      if (e.target === $('deleteModal')) _closeDeleteModal();
    });

    // Cerrar dropdown de audio al hacer click fuera
    document.addEventListener('click', e => {
      if (!e.target.closest('#audioSelector') &&
          !e.target.closest('#audioDropdown')) {
        _hideEl(DOM.audioDropdown());
      }
    });
  }

  // ──────────────────────────────────────────────────────────
  // 12. MODAL DELETE
  // ──────────────────────────────────────────────────────────
  function _openDeleteModal(id) {
    _deletingId = id;
    const p = _pautas.find(x => x.id === id);
    if (!p) return;
    DOM.deleteCliente().textContent = p.cliente;
    _showEl($('deleteModal'));
  }

  function _closeDeleteModal() {
    _hideEl($('deleteModal'));
    _deletingId = null;
  }

  // ──────────────────────────────────────────────────────────
  // 13. INICIALIZACIÓN
  // ──────────────────────────────────────────────────────────
  function init() {
    _load();
    _audios = _seedAudios();   // ← reemplazar por fetch real a la API de audios
    _bindEvents();
    _render();
    console.info('[PautasController] ✅ Módulo inicializado. Pautas:', _pautas.length);
  }

  // ──────────────────────────────────────────────────────────
  // 14. API PÚBLICA
  // ──────────────────────────────────────────────────────────
  global.PautasController = {
    init,
    /** Carga datos de pautas desde una fuente externa (API REST) */
    loadFromAPI(pautasArray) {
      _pautas = pautasArray.map(createPauta);
      _save();
      _render();
    },
    /** Expone las pautas actuales (solo lectura) */
    getPautas() { return [..._pautas]; },
    /** Recarga el módulo limpiando el estado */
    destroy() {
      document.removeEventListener('mouseup', _globalMouseUp);
      console.info('[PautasController] Módulo destruido.');
    },
  };

})(window);
