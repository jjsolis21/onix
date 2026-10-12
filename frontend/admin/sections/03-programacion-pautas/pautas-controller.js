/**
 * ============================================================
 * PAUTAS-CONTROLLER.JS  — v3.0  (Redesign Pro)
 * Módulo autónomo para la Sección 03 — Programación de Pautas
 *
 * NUEVAS FUNCIONALIDADES v3:
 *   · Editor de Cue Points vía onix-waveform.js (wfInit/wfLoadFile/wfLoadUrl)
 *   · Campos extra: rubro, contrato, urgente, menciones/día
 *   · Persistencia de campos extra dentro de matriz.__meta (sin cambios de schema)
 *   · Semáforo de vencimientos (rojo / amarillo / urgente) en las filas
 *   · Centro de Notificaciones (campana) con conteo de alertas
 *   · Filtro avanzado con dropdown (por estado, rubro)
 *   · Advertencia dinámica de menciones vs. franjas de la matriz
 *   · Ruta de subida anualizada: MEDIA/COMERCIALES/{YYYY}/
 *
 * Expone: window.PautasController
 * Dependencias: onix-waveform.js (global wfInit / wfLoadFile / wfLoadUrl /
 *               wfGetMarkers / wfDestroy), WaveSurfer.js + RegionsPlugin
 *
 * Endpoints:
 *   GET    /api/v1/audios        → catálogo de audios
 *   GET    /api/v1/pautas        → lista de pautas
 *   POST   /api/v1/pautas        → crear pauta
 *   PUT    /api/v1/pautas/:id    → actualizar
 *   DELETE /api/v1/pautas/:id    → eliminar
 *   POST   /api/v1/upload        → subir archivo de audio
 * ============================================================
 */
;(function (global) {
  'use strict';

  // ──────────────────────────────────────────────────────────
  // 1. CONFIGURACIÓN
  // ──────────────────────────────────────────────────────────

  const API_BASE = '';

  // Categorías que interesan al filtrar audios del catálogo
  const AUDIO_CATEGORIAS = ['COMERCIAL', 'PUBLICIDAD'];

  // Días de aviso previo al vencimiento para el semáforo amarillo
  const DAYS_WARN = 3;

  /**
   * Las horas "Prime Time" coinciden con las definidas en el CSS y en
   * la lógica de onix-waveform.js: lun–vie 07-08 y 17-19.
   * day: 0=Dom, 1=Lun…6=Sáb
   */
  const PRIME_SLOTS = new Set(
    [1, 2, 3, 4, 5].flatMap(d => [7, 8, 17, 18, 19, 20].map(h => `${d}-${h}`))
  );

  const DIAS  = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
  const HORAS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0') + ':00');

  // ──────────────────────────────────────────────────────────
  // 2. ESTADO DEL MÓDULO
  // ──────────────────────────────────────────────────────────

  let _pautas      = [];        // espejo local del servidor
  let _audios      = [];        // catálogo de audios
  let _editingId   = null;      // id en edición (null = nueva)
  let _deletingId  = null;      // id pendiente de borrado
  let _isSaving    = false;     // guard anti-doble-submit
  let _searchTerm  = '';
  let _filterEstado= 'todas';   // 'todas'|'activa'|'vencida'|'por_vencer'|'urgente'
  let _filterRubro = '';

  // Estado de arrastre en la matriz
  const _drag = { active: false, startState: null };

  // WaveSurfer ya inicializado
  let _wfReady = false;

  // Guarda para evitar doble inicialización de eventos
  let _initialized = false;

  // ──────────────────────────────────────────────────────────
  // 3. HELPERS DOM
  // ──────────────────────────────────────────────────────────

  const $ = id => document.getElementById(id);

  function _showEl(el) { el && el.classList.remove('hidden'); }
  function _hideEl(el) { el && el.classList.add('hidden'); }

  // ──────────────────────────────────────────────────────────
  // 4. HELPERS DE DATOS PUROS
  // ──────────────────────────────────────────────────────────

  /** Hoy como "YYYY-MM-DD" */
  function _today() {
    return new Date().toISOString().slice(0, 10);
  }

  /** Hoy + N días como "YYYY-MM-DD" */
  function _offsetDate(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  /**
   * Calcula el estado semafórico de una pauta.
   * Retorna: 'vencida' | 'por-vencer' | 'urgente' | 'activa'
   *
   * "urgente" toma precedencia sobre todos los demás estados
   * para que la fila siempre llame la atención.
   */
  function _semaphore(pauta) {
    const meta = pauta.matriz?.__meta ?? {};
    const urgente = pauta.urgente ?? meta.urgente ?? false;
    const hoy = _today();
    const fin = pauta.fecha_fin;

    if (!fin || fin < hoy)  return 'vencida';
    if (fin <= _offsetDate(DAYS_WARN)) return 'por-vencer';
    if (urgente) return 'urgente';
    return 'activa';
  }

  function _isActiva(pauta) {
    const hoy = _today();
    return pauta.fecha_inicio <= hoy && pauta.fecha_fin >= hoy;
  }

  /**
   * Cuenta los slots activos de la matriz ignorando la clave
   * especial "__meta" que usamos para persistir campos extra.
   */
  function _countSpots(matriz) {
    if (!matriz || typeof matriz !== 'object') return 0;
    return Object.entries(matriz)
      .filter(([k, v]) => k !== '__meta' && v === true)
      .length;
  }

  /** Extrae los metadatos del campo especial de la matriz */
  function _getMeta(pauta) {
    return pauta.matriz?.__meta ?? {};
  }

  /** Fecha ISO → "DD/MM/AAAA" */
  function _fmt(isoDate) {
    if (!isoDate) return '—';
    const [y, m, d] = isoDate.split('-');
    return `${d}/${m}/${y}`;
  }

  function _fmtDur(s) {
    if (!s && s !== 0) return '0:00';
    const m = Math.floor(s / 60), sec = s % 60;
    return `${m}:${String(Math.floor(sec)).padStart(2, '0')}`;
  }

  function _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function _truncate(str, max) {
    return String(str ?? '').length > max
      ? String(str).slice(0, max - 1) + '…'
      : String(str ?? '');
  }

  function _fmtFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  }

  // ──────────────────────────────────────────────────────────
  // 5. FÁBRICA DEL MODELO
  // ──────────────────────────────────────────────────────────

  function createPauta(data = {}) {
    // BUG #3 FIX: Asegurar que se usen exactamente los nombres de campos del API (prioridad, contrato_id, menciones_diarias)
    // El server (PautaOut) envía estos campos directamente.
    return {
      id:           data.id           ?? null,
      cliente:      data.cliente      ?? '',
      audio_id:     data.audio_id     ?? null,
      audio_nombre: data.audio_nombre ?? '',
      matriz:       data.matriz       ?? {},
      fecha_inicio: data.fecha_inicio ?? '',
      fecha_fin:    data.fecha_fin    ?? '',
      notas:        data.notas        ?? '',
      rubro:        data.rubro             ?? data.matriz?.__meta?.rubro       ?? '',
      contrato:     data.contrato_id       ?? data.matriz?.__meta?.contrato    ?? '',
      urgente:      data.prioridad         ?? data.matriz?.__meta?.urgente     ?? false,
      menciones:    data.menciones_diarias ?? data.matriz?.__meta?.menciones ?? 4,
      cue_intro:    data.cue_intro         ?? data.matriz?.__meta?.cue_intro   ?? 0,
      cue_outro:    data.cue_outro         ?? data.matriz?.__meta?.cue_outro   ?? 0,
    };
  }

  // ──────────────────────────────────────────────────────────
  // 6. CAPA DE API
  // ──────────────────────────────────────────────────────────

  async function _apiFetch(url, options = {}) {
    const defaults = {
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
    };
    const res = await fetch(API_BASE + url, {
      ...defaults, ...options,
      headers: { ...defaults.headers, ...(options.headers ?? {}) },
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        msg = body?.detail ?? body?.message ?? msg;
      } catch (_) { /* body no es JSON */ }
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async function fetchAudios() {
    try {
      const params = new URLSearchParams();
      AUDIO_CATEGORIAS.forEach(cat => params.append('categoria_1', cat));
      let raw = await _apiFetch(`/api/v1/audios?${params.toString()}`);
      const normalized = _normalizeAudios(
        Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? [])
      );
      // Si el filtro devuelve 0, cargar todos como fallback
      if (!normalized.length) {
        raw = await _apiFetch('/api/v1/audios');
        return _normalizeAudios(Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? []));
      }
      return normalized;
    } catch (err) {
      console.warn('[PautasController] No se pudo cargar catálogo de audios:', err.message);
      return [];
    }
  }

  function _normalizeAudios(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.map(a => ({
      id:       a.id ?? a.audio_id ?? String(Math.random()).slice(2, 10),
      nombre:   a.nombre ?? a.archivo_path?.split('/').pop() ?? `Audio #${a.id}`,
      titulo:   a.titulo  ?? a.title  ?? '',
      artista:  a.artista ?? a.artist ?? '',
      duracion: a.duracion ?? _fmtDur(a.duration ?? a.duracion_seg ?? 0),
    }));
  }

  async function fetchPautas() {
    try {
      const raw = await _apiFetch('/api/v1/pautas');
      const arr = Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? []);
      _pautas = arr.map(createPauta);
    } catch (err) {
      console.error('[PautasController] Error al cargar pautas:', err);
      _pautas = [];
    }
  }

  async function _apiCreatePauta(payload) {
    return createPauta(await _apiFetch('/api/v1/pautas', {
      method: 'POST',
      body: JSON.stringify(payload),
    }));
  }

  async function _apiUpdatePauta(id, payload) {
    return createPauta(await _apiFetch(`/api/v1/pautas/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }));
  }

  async function _apiDeletePauta(id) {
    await _apiFetch(`/api/v1/pautas/${id}`, { method: 'DELETE' });
  }

  // ──────────────────────────────────────────────────────────
  // 7. RENDERIZADO: STATS
  // ──────────────────────────────────────────────────────────

  function _renderStats() {
    const hoy       = _today();
    const limitDate = _offsetDate(DAYS_WARN);
    const activas   = _pautas.filter(_isActiva).length;
    const vencidas  = _pautas.filter(p => p.fecha_fin < hoy).length;
    const porVencer = _pautas.filter(p =>
      p.fecha_fin >= hoy && p.fecha_fin <= limitDate
    ).length;
    const spotsTotal = _pautas.reduce((a, p) => a + _countSpots(p.matriz), 0);
    const clientes   = new Set(_pautas.map(p => p.cliente)).size;

    const _num = (id, val) => {
      const el = $(id); if (el) el.querySelector('.stat-card__num').textContent = val;
    };

    _num('statActivas',   activas);
    _num('statPorVencer', porVencer);
    _num('statVencidas',  vencidas);
    _num('statSpots',     spotsTotal);
    _num('statClientes',  clientes);
  }

  // ──────────────────────────────────────────────────────────
  // 8. RENDERIZADO: CENTRO DE NOTIFICACIONES
  // ──────────────────────────────────────────────────────────

  function _renderNotifications() {
    const hoy       = _today();
    const limitDate = _offsetDate(DAYS_WARN);

    const vencidas  = _pautas.filter(p => p.fecha_fin < hoy);
    const porVencer = _pautas.filter(p =>
      p.fecha_fin >= hoy && p.fecha_fin <= limitDate
    );

    const total = vencidas.length + porVencer.length;
    const badge = $('notifBadge');
    if (badge) {
      badge.textContent = total;
      total > 0 ? _showEl(badge) : _hideEl(badge);
    }

    // Poblar el panel de notificaciones
    const list = $('notifList');
    if (!list) return;

    if (total === 0) {
      list.innerHTML =
        '<div class="notif-empty">✅ Sin alertas pendientes. Todas las pautas están al día.</div>';
      return;
    }

    let html = '';

    if (vencidas.length) {
      html += `<div class="notif-group__title notif-group__title--vencida">
        ● Vencidas (${vencidas.length})</div>`;
      vencidas.slice(0, 8).forEach(p => {
        html += `<div class="notif-item" data-notif-id="${_esc(String(p.id))}">
          <span class="notif-item__dot notif-item__dot--vencida"></span>
          <span class="notif-item__name">${_esc(_truncate(p.cliente, 26))}</span>
          <span class="notif-item__date">${_fmt(p.fecha_fin)}</span>
        </div>`;
      });
    }

    if (porVencer.length) {
      html += `<div class="notif-group__title notif-group__title--porvencer">
        ◑ Por Vencer — ${DAYS_WARN} días (${porVencer.length})</div>`;
      porVencer.slice(0, 8).forEach(p => {
        html += `<div class="notif-item" data-notif-id="${_esc(String(p.id))}">
          <span class="notif-item__dot notif-item__dot--porvencer"></span>
          <span class="notif-item__name">${_esc(_truncate(p.cliente, 26))}</span>
          <span class="notif-item__date">${_fmt(p.fecha_fin)}</span>
        </div>`;
      });
    }

    list.innerHTML = html;

    // Clic en un ítem → abre el modal de esa pauta
    list.querySelectorAll('.notif-item[data-notif-id]').forEach(item => {
      item.addEventListener('click', () => {
        _closeNotifications();
        _openModal(item.dataset.notifId);
      });
    });
  }

  function _toggleNotifications() {
    const dropdown  = $('notifDropdown');
    const bell      = $('notifBell');
    const isVisible = !dropdown?.classList.contains('hidden');
    if (isVisible) {
      _closeNotifications();
    } else {
      _showEl(dropdown);
      bell?.setAttribute('aria-expanded', 'true');
    }
  }

  function _closeNotifications() {
    _hideEl($('notifDropdown'));
    $('notifBell')?.setAttribute('aria-expanded', 'false');
  }

  // ──────────────────────────────────────────────────────────
  // 9. RENDERIZADO: TABLA CON SEMÁFORO
  // ──────────────────────────────────────────────────────────

  function _renderTable() {
    const term  = _searchTerm.toLowerCase();
    const tbody = $('pautasTableBody');
    if (!tbody) return;

    const visible = _pautas.filter(p => {
      // Búsqueda en tiempo real (cliente, rubro, contrato, audio)
      const meta = _getMeta(p);
      const rubro    = (p.rubro    || meta.rubro    || '').toLowerCase();
      const contrato = (p.contrato || meta.contrato || '').toLowerCase();
      const matchSearch = !term ||
        p.cliente.toLowerCase().includes(term)     ||
        p.audio_nombre.toLowerCase().includes(term) ||
        rubro.includes(term)                         ||
        contrato.includes(term);

      // Filtro de estado
      const semaf = _semaphore(p);
      let matchFilter = true;
      if      (_filterEstado === 'activa')     matchFilter = _isActiva(p);
      else if (_filterEstado === 'vencida')    matchFilter = semaf === 'vencida';
      else if (_filterEstado === 'por_vencer') matchFilter = semaf === 'por-vencer';
      else if (_filterEstado === 'urgente')    matchFilter = semaf === 'urgente';

      // Filtro de rubro
      const matchRubro = !_filterRubro ||
        (p.rubro || meta.rubro || '').toLowerCase() === _filterRubro.toLowerCase();

      return matchSearch && matchFilter && matchRubro;
    });

    tbody.innerHTML = '';

    if (!visible.length) {
      _showEl($('pautasEmpty'));
      return;
    }
    _hideEl($('pautasEmpty'));

    visible.forEach(p => {
      const semaf     = _semaphore(p);
      const meta      = _getMeta(p);
      const rubro     = p.rubro     || meta.rubro     || '—';
      const contrato  = p.contrato  || meta.contrato  || '';
      const urgente   = p.urgente   || meta.urgente   || false;
      const menciones = p.menciones ?? meta.menciones ?? 4;

      // Clase de semáforo para el fondo de la fila
      const rowClass = {
        'vencida':    'row--vencida',
        'por-vencer': 'row--por-vencer',
        'urgente':    'row--urgente',
        'activa':     '',
      }[semaf] ?? '';

      // Badge de estado
      let estadoBadge = '';
      if (semaf === 'vencida') {
        estadoBadge = `<span class="badge badge--dot badge--vencida">Vencida</span>`;
      } else if (semaf === 'por-vencer') {
        estadoBadge = `<span class="badge badge--dot badge--por-vencer">Por Vencer</span>`;
      } else if (semaf === 'urgente') {
        estadoBadge = `<span class="badge badge--dot badge--activa">Activa</span>`;
      } else {
        estadoBadge = `<span class="badge badge--dot badge--activa">Activa</span>`;
      }

      // Badge URGENTE adicional en el nombre del cliente
      const urgenteBadge = urgente
        ? `<span class="badge-urgente-row">URGENTE</span>`
        : '';

      const tr = document.createElement('tr');
      tr.dataset.id = p.id;
      if (rowClass) tr.classList.add(rowClass);

      tr.innerHTML = `
        <td>
          <strong>${_esc(p.cliente)}</strong>
          ${urgenteBadge}
          ${contrato
            ? `<br><span style="font-size:.72rem;color:var(--p-text-muted)">${_esc(contrato)}</span>`
            : ''}
        </td>
        <td>${_esc(_truncate(rubro, 22))}</td>
        <td>
          <span title="${_esc(p.audio_nombre)}">
            🎵 ${_esc(_truncate(p.audio_nombre, 26))}
          </span>
        </td>
        <td style="white-space:nowrap">
          ${_fmt(p.fecha_inicio)} → ${_fmt(p.fecha_fin)}
        </td>
        <td style="text-align:center">
          <span style="color:var(--p-accent);font-weight:700">${menciones}</span>
        </td>
        <td>${estadoBadge}</td>
        <td>
          <div class="row-actions">
            <button class="btn-row btn-row--edit"
              title="Editar" data-action="edit" data-id="${_esc(String(p.id))}">✏️</button>
            <button class="btn-row btn-row--delete"
              title="Eliminar" data-action="delete" data-id="${_esc(String(p.id))}">🗑</button>
          </div>
        </td>`;

      tbody.appendChild(tr);
    });
  }

  function _render() {
    _renderStats();
    _renderNotifications();
    _renderTable();
    _populateRubroFilter();
  }

  // ──────────────────────────────────────────────────────────
  // 10. FILTRO AVANZADO
  // ──────────────────────────────────────────────────────────

  /** Llena el <select> de rubros con los valores únicos del listado */
  function _populateRubroFilter() {
    const sel = $('filtRubro');
    if (!sel) return;
    const rubros = [...new Set(
      _pautas.map(p => p.rubro || _getMeta(p).rubro || '')
        .filter(Boolean)
    )].sort();

    const current = sel.value;
    sel.innerHTML = '<option value="">Todos los rubros</option>';
    rubros.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r;
      opt.textContent = r;
      if (r === current) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function _applyFilter() {
    const dot = $('filterDotActive');
    const hasFilter = _filterEstado !== 'todas' || _filterRubro !== '';
    hasFilter ? _showEl(dot) : _hideEl(dot);
    _renderTable();
  }

  function _clearFilters() {
    _filterEstado = 'todas';
    _filterRubro  = '';
    // Resetear los radios
    document.querySelectorAll('[name="filtEstado"]').forEach(r => {
      r.checked = r.value === 'todas';
    });
    const sel = $('filtRubro');
    if (sel) sel.value = '';
    _hideEl($('filterDotActive'));
    _renderTable();
    _hideFilterPanel();
  }

  function _toggleFilterPanel() {
    const panel = $('filterPanel');
    const btn   = $('btnFilterToggle');
    if (!panel) return;
    const open = !panel.classList.contains('hidden');
    open ? _hideFilterPanel() : _showFilterPanel();
  }

  function _showFilterPanel() {
    _showEl($('filterPanel'));
    $('btnFilterToggle')?.setAttribute('aria-expanded', 'true');
  }

  function _hideFilterPanel() {
    _hideEl($('filterPanel'));
    $('btnFilterToggle')?.setAttribute('aria-expanded', 'false');
  }

  // ──────────────────────────────────────────────────────────
  // 11. CONSTRUCCIÓN DE LA MATRIZ 24×7
  // ──────────────────────────────────────────────────────────

  function _buildMatrix(matrizData = {}) {
    const grid = $('matrixGrid');
    if (!grid) return;
    grid.innerHTML = '';

    // Fila de cabecera (esquina + días)
    grid.appendChild(_matrixCell('', 'matrix-cell--day-header'));
    DIAS.forEach(d => grid.appendChild(_matrixCell(d, 'matrix-cell--day-header')));

    // Filas de horas
    for (let h = 0; h < 24; h++) {
      grid.appendChild(_matrixCell(HORAS[h], 'matrix-cell--hour-label'));
      for (let d = 0; d < 7; d++) {
        const key    = `${d}-${h}`;
        const isPrime= PRIME_SLOTS.has(key);
        const isOn   = !!matrizData[key];

        const cell = document.createElement('div');
        cell.className   = 'matrix-cell';
        cell.dataset.key = key;
        cell.dataset.state = isOn ? 'on' : 'off';
        cell.dataset.prime = isPrime ? 'true' : 'false';
        cell.title = `${DIAS[d]} ${HORAS[h]}${isPrime ? ' ★ Prime Time' : ''}`;

        cell.addEventListener('mousedown',  _onCellMouseDown);
        cell.addEventListener('mouseenter', _onCellMouseEnter);
        cell.addEventListener('mouseup',    _onCellMouseUp);

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

  // Drag y toggle
  function _onCellMouseDown(e) {
    e.preventDefault();
    const cell = e.currentTarget;
    _drag.active     = true;
    _drag.startState = cell.dataset.state === 'on' ? 'off' : 'on';
    _toggleCell(cell, _drag.startState);
    _checkMentionsWarning();
  }

  function _onCellMouseEnter(e) {
    if (!_drag.active) return;
    _toggleCell(e.currentTarget, _drag.startState);
  }

  function _onCellMouseUp() {
    _drag.active = false;
    _updateMatrixSummary(_readMatrixFromDOM());
    _checkMentionsWarning();
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
    const el    = $('matrixSummaryText');
    if (!el) return;
    const count = _countSpots(matriz);
    if (count === 0) {
      el.textContent = 'Sin franjas horarias asignadas';
      return;
    }
    const days = new Set(
      Object.keys(matriz).filter(k => k !== '__meta').map(k => k.split('-')[0])
    ).size;
    const perDay = (count / 7).toFixed(1);
    el.textContent =
      `${count} slot${count !== 1 ? 's' : ''} en ${days} día${days !== 1 ? 's' : ''} · ~${perDay}/día`;
  }

  // ──────────────────────────────────────────────────────────
  // 12. ADVERTENCIA DE MENCIONES vs. MATRIZ
  // ──────────────────────────────────────────────────────────

  /**
   * Verifica si algún día de la semana tiene más franjas seleccionadas
   * en la matriz que el número de menciones configurado.
   *
   * Ejemplo: si el usuario pidió 3 menciones/día pero marcó 8 franjas
   * el lunes, eso implica que el spot podría emitirse hasta 8 veces ese día,
   * superando el contrato. En ese caso mostramos una advertencia amarilla.
   */
  function _checkMentionsWarning() {
    const warnEl  = $('mentionsWarning');
    const warnTxt = $('mentionsWarningText');
    if (!warnEl) return;

    const menciones = parseInt($('fMenciones')?.value ?? 4, 10);
    if (!menciones || menciones < 1) { _hideEl(warnEl); return; }

    const matriz = _readMatrixFromDOM();

    // Contamos cuántos slots tiene cada día
    const perDay = Array(7).fill(0);
    Object.keys(matriz).filter(k => k !== '__meta').forEach(k => {
      const d = parseInt(k.split('-')[0], 10);
      perDay[d]++;
    });

    const maxDay  = Math.max(...perDay);
    const offenders = perDay
      .map((n, d) => n > menciones ? DIAS[d] : null)
      .filter(Boolean);

    if (maxDay > menciones && offenders.length) {
      _showEl(warnEl);
      if (warnTxt) {
        warnTxt.textContent =
          `Advertencia: ${offenders.join(', ')} tienen más de ${menciones} franja${menciones !== 1 ? 's' : ''} ` +
          `seleccionada${menciones !== 1 ? 's' : ''}. ` +
          `Con ${menciones} menciones/día el sistema solo emitirá las primeras ${menciones}.`;
      }
    } else {
      _hideEl(warnEl);
    }
  }

  // ──────────────────────────────────────────────────────────
  // 13. RELLENO RÁPIDO DE MATRIZ
  // ──────────────────────────────────────────────────────────

  function _matrixLV() {
    _applyQuickFill(c => {
      const d = parseInt(c.dataset.key.split('-')[0]);
      return d >= 1 && d <= 5;
    });
  }

  function _matrixFinde() {
    _applyQuickFill(c => {
      const d = parseInt(c.dataset.key.split('-')[0]);
      return d === 0 || d === 6;
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
    _checkMentionsWarning();
  }

  function _applyQuickFill(predicate) {
    document.querySelectorAll('.matrix-cell[data-key]').forEach((c, i) => {
      const match = predicate(c);
      c.dataset.state = match ? 'on' : 'off';
      if (match) {
        c.classList.remove('matrix-cell--flash');
        // Animación de flash escalonada por columna de día
        setTimeout(() => c.classList.add('matrix-cell--flash'), (i % 7) * 6);
      }
    });
    _updateMatrixSummary(_readMatrixFromDOM());
    _checkMentionsWarning();
  }

  // ──────────────────────────────────────────────────────────
  // 14. INTEGRACIÓN DE WAVESURFER (onix-waveform.js)
  // ──────────────────────────────────────────────────────────

  /**
   * Inicializa WaveSurfer de forma diferida.
   * onix-waveform.js expone wfInit() como global.
   * Se llama una sola vez al abrir el modal por primera vez.
   */
  function _initWaveSurfer() {
    if (_wfReady) return Promise.resolve();

    if (typeof global.wfInit !== 'function') {
      console.warn('[PautasController] wfInit no encontrado. ¿Está cargado onix-waveform.js?');
      return Promise.resolve();  // el modal abre sin waveform; no es crítico
    }

    // BUG #4 FIX: la implementación anterior hacía await de wfInit() sin límite de
    // tiempo. wfInit() internamente reintenta hasta 50 veces × 100 ms = 5 segundos
    // esperando que WaveSurfer esté disponible. Durante esa espera, _openModal()
    // quedaba suspendido en el await y el modal nunca aparecía para el usuario.
    //
    // La solución es Promise.race() contra un timeout de 3 segundos. Si WaveSurfer
    // no está listo en ese tiempo, el modal abre de todas formas y el panel de la
    // onda simplemente permanece oculto. Nunca rechazamos — siempre resolvemos —
    // para garantizar que _openModal() siempre avanza.
    var wfPromise = global.wfInit().then(function() { _wfReady = true; });
    var timeout   = new Promise(function(resolve) { setTimeout(resolve, 3000); });

    return Promise.race([wfPromise, timeout]).catch(function(err) {
      console.error('[PautasController] WaveSurfer no pudo inicializar:', err);
      // El catch convierte el rechazo en resolución para no bloquear al usuario.
    });
  }

  // ──────────────────────────────────────────────────────────
  // 15. MODAL PRINCIPAL
  // ──────────────────────────────────────────────────────────

  async function _openModal(pautaId = null) {
    _editingId = pautaId;
    _clearModalError();
    _setSaving(false);

    // Inicializar WaveSurfer si aún no está listo
    await _initWaveSurfer();

    if (pautaId) {
      // ── MODO EDICIÓN ──
      const p = _pautas.find(x => String(x.id) === String(pautaId));
      if (!p) return;

      const meta = _getMeta(p);

      $('modalTitle').textContent = `Editar — ${p.cliente}`;
      $('fCliente').value   = p.cliente;
      $('fAudio').value     = p.audio_nombre;
      $('fAudio')._selectedId     = p.audio_id;
      $('fAudio')._selectedNombre = p.audio_nombre;
      $('fAudio')._selectedPath   = '';
      $('audioHint').textContent  = p.audio_nombre || 'Ningún archivo seleccionado';
      $('fFechaInicio').value = p.fecha_inicio;
      $('fFechaFin').value    = p.fecha_fin;
      $('fNotas').value       = p.notas;
      $('fRubro').value       = p.rubro    || meta.rubro    || '';
      $('fContrato').value    = p.contrato || meta.contrato || '';
      $('fMenciones').value   = p.menciones ?? meta.menciones ?? 4;
      $('fUrgente').checked   = !!(p.urgente ?? meta.urgente ?? false);
      $('btnSaveText').textContent = 'Actualizar Pauta';

      _buildMatrix(p.matriz);

      // Actualizar badge urgente en la cabecera del modal
      _syncUrgenteHeader();

      // Cargar la onda del audio existente (si tiene ruta)
      const audioPath = p.audio_nombre || '';
      if (audioPath && typeof global.wfLoadUrl === 'function') {
        // El audio de la pauta se sirve por id: la API resuelve la ruta real
        const url = audioPath.startsWith('http')
          ? audioPath
          : (p.audio_id ? `/api/v1/audios/${p.audio_id}/stream`
                        : `/stream/${audioPath.replace(/\\/g, '/')}`);
        const existingCues = {
          intro:       meta.cue_intro       ?? 0,
          outro:       meta.cue_outro       ?? 0,
          mezcla:      meta.cue_mezcla      ?? 0,
          inicio_coro: meta.cue_inicio_coro ?? 0,
          final_coro:  meta.cue_final_coro  ?? 0,
        };
        global.wfLoadUrl(url, existingCues);
      }

    } else {
      // ── MODO CREACIÓN ──
      $('modalTitle').textContent  = 'Nueva Pauta';
      $('fCliente').value          = '';
      $('fAudio').value            = '';
      $('fAudio')._selectedId      = null;
      $('fAudio')._selectedNombre  = '';
      $('fAudio')._selectedPath    = '';
      $('audioHint').textContent   = 'Ningún archivo seleccionado';
      $('fFechaInicio').value      = _today();
      $('fFechaFin').value         = _offsetDate(30);
      $('fNotas').value            = '';
      $('fRubro').value            = '';
      $('fContrato').value         = '';
      $('fMenciones').value        = '4';
      $('fUrgente').checked        = false;
      $('btnSaveText').textContent = 'Guardar Pauta';

      _buildMatrix({});
      _hideEl($('urgenteBadgeHeader'));
      _hideEl($('mentionsWarning'));
    }

    // Bloquear fechas pasadas en Fecha Inicio
    $('fFechaInicio').min = _today();

    _showEl($('pautaModal'));
    $('fCliente').focus();
  }

  function _closeModal() {
    _hideEl($('pautaModal'));
    _editingId = null;
    _isSaving  = false;
    _drag.active = false;
    _hideEl($('mentionsWarning'));
  }

  /** Actualiza el badge URGENTE del header del modal según el checkbox */
  function _syncUrgenteHeader() {
    const cb    = $('fUrgente');
    const badge = $('urgenteBadgeHeader');
    if (!cb || !badge) return;
    cb.checked ? _showEl(badge) : _hideEl(badge);
  }

  // ──────────────────────────────────────────────────────────
  // 16. VALIDACIÓN Y GUARDADO
  // ──────────────────────────────────────────────────────────

  function _setSaving(flag) {
    _isSaving = flag;
    const btn  = $('btnModalSave');
    const txt  = $('btnSaveText');
    if (btn) btn.disabled = flag;
    if (txt) txt.textContent = flag
      ? (_editingId ? 'Actualizando…' : 'Guardando…')
      : (_editingId ? 'Actualizar Pauta' : 'Guardar Pauta');
  }

  function _showModalError(msg) {
    const el = $('modalError');
    if (!el) return;
    el.textContent = '⚠ ' + msg;
    _showEl(el);
  }

  function _clearModalError() {
    const el = $('modalError');
    if (!el) return;
    el.textContent = '';
    _hideEl(el);
  }

  async function _savePauta() {
    if (_isSaving) return;

    // Leer campos del formulario
    const cliente  = ($('fCliente')?.value  ?? '').trim().toUpperCase();
    const audioVal = ($('fAudio')?.value    ?? '').trim();
    const inicio   = $('fFechaInicio')?.value ?? '';
    const fin      = $('fFechaFin')?.value    ?? '';
    const notas    = ($('fNotas')?.value     ?? '').trim().toUpperCase();
    const rubro    = ($('fRubro')?.value     ?? '').trim();
    const contrato = ($('fContrato')?.value  ?? '').trim();
    const urgente  = $('fUrgente')?.checked  ?? false;
    const menciones= parseInt($('fMenciones')?.value ?? '4', 10);

    // Validaciones
    if (!cliente)  { _showModalError('El cliente es obligatorio.'); return; }
    if (!audioVal) { _showModalError('Seleccioná un audio para la pauta.'); return; }
    if (!inicio)   { _showModalError('La fecha de inicio es obligatoria.'); return; }
    if (!fin)      { _showModalError('La fecha de fin es obligatoria.'); return; }
    if (inicio > fin) { _showModalError('La fecha de inicio no puede superar al fin.'); return; }
    if (isNaN(menciones) || menciones < 1 || menciones > 24) {
      _showModalError('Las menciones deben estar entre 1 y 24.');
      return;
    }

    const rawMatrix = _readMatrixFromDOM();
    if (!Object.keys(rawMatrix).length) {
      _showModalError('Seleccioná al menos un slot en la Matriz de Horarios.');
      return;
    }

    // Obtener los marcadores de WaveSurfer si están disponibles
    const cues = (typeof global.wfGetMarkers === 'function')
      ? global.wfGetMarkers()
      : {};

    // Empaquetamos los campos extra en __meta para persistirlos
    // sin requerir cambios de schema en el backend.
    const matrizConMeta = {
      ...rawMatrix,
      __meta: {
        rubro,
        contrato,
        urgente,
        menciones,
        cue_intro:       cues.intro       ?? 0,
        cue_outro:       cues.outro       ?? 0,
        cue_mezcla:      cues.mezcla      ?? 0,
        cue_inicio_coro: cues.inicio_coro ?? 0,
        cue_final_coro:  cues.final_coro  ?? 0,
      }
    };

    // Resolver el audio seleccionado
    const fAudioEl      = $('fAudio');
    const audioId       = fAudioEl?._selectedId    ?? null;
    const audioNombre   = fAudioEl?._selectedNombre || audioVal;

    const payload = {
      cliente,
      audio_id:          audioId,
      audio_nombre:      audioNombre,
      fecha_inicio:      inicio,
      fecha_fin:         fin,
      notas,
      rubro,
      // BUG #3 FIX: these three fields were sent with the wrong names.
      // JS used:  contrato    → PautaIn expects: contrato_id
      // JS used:  urgente     → PautaIn expects: prioridad
      // JS used:  menciones   → PautaIn expects: menciones_diarias
      // Pydantic validates the payload strictly; mismatched names silently
      // received their default values (empty string / False / None) instead
      // of the user's input, and any validation error caused a 422 Unprocessable.
      contrato_id:       contrato,
      prioridad:         urgente,
      menciones_diarias: menciones,
      // cue_intro / cue_outro are top-level fields in PautaIn (not just __meta).
      cue_intro:         cues.intro  ?? 0,
      cue_outro:         cues.outro  ?? 0,
      matriz:            matrizConMeta,
    };

    _clearModalError();
    _setSaving(true);

    try {
      if (_editingId) {
        const updated = await _apiUpdatePauta(_editingId, payload);
        const idx = _pautas.findIndex(p => String(p.id) === String(_editingId));
        if (idx !== -1) _pautas[idx] = updated;
        else _pautas.push(updated);
        console.info(`[PautasController] Pauta ${_editingId} actualizada.`);
      } else {
        const created = await _apiCreatePauta(payload);
        _pautas.push(created);
        console.info(`[PautasController] Pauta creada con ID: ${created.id}`);
      }
      _render();
      _closeModal();
    } catch (err) {
      console.error('[PautasController] Error al guardar:', err);
      _showModalError(err.message || 'Error de red. Intentá nuevamente.');
    } finally {
      _setSaving(false);
    }
  }

  // ──────────────────────────────────────────────────────────
  // 17. SELECTOR DE AUDIO + SUBIDA CON RUTA ANUALIZADA
  // ──────────────────────────────────────────────────────────

  function _openFilePicker() {
    $('audioFileInput')?.click();
  }

  /**
   * Sube el archivo a /api/v1/upload incluyendo la subcarpeta
   * anualizada MEDIA/COMERCIALES/{AÑO}/ y luego activa el
   * waveform via wfLoadFile(file) para análisis inmediato.
   */
  async function _onAudioFileSelected(e) {
    const file = e.target.files?.[0];
    if (!file) return;

    const fAudio    = $('fAudio');
    const audioHint = $('audioHint');
    fAudio.value    = file.name;
    if (audioHint) audioHint.textContent =
      `📤 Preparando: ${file.name} (${_fmtFileSize(file.size)})`;

    // Mostrar progreso
    const progressWrap = $('uploadProgress');
    const progressBar  = $('uploadProgressBar');
    const progressLbl  = $('uploadProgressLabel');
    _showEl(progressWrap);
    progressBar.style.width = '0%';
    progressLbl.textContent = 'Subiendo…';

    const btnSelect = $('btnSelectAudio');
    if (btnSelect) btnSelect.disabled = true;

    // Cargar en WaveSurfer de inmediato (no esperar la subida)
    // para que el operador ya pueda editar los cues mientras sube.
    if (typeof global.wfLoadFile === 'function') {
      global.wfLoadFile(file);
    }

    try {
      const year    = new Date().getFullYear();
      const subdir  = `MEDIA/COMERCIALES/${year}`;

      const formData = new FormData();
      formData.append('file',   file);
      formData.append('subdir', subdir); // instrucción de ruta para el backend

      const result = await _uploadWithProgress(
        '/api/v1/upload',
        formData,
        pct => {
          progressBar.style.width = `${pct}%`;
          progressLbl.textContent = `Subiendo… ${pct}%`;
        }
      );

      const nombre = result.filename ?? file.name;
      const path   = result.archivo_path ?? `${subdir}/${nombre}`;

      fAudio.value             = nombre;
      fAudio._selectedNombre   = nombre;
      fAudio._selectedPath     = path;

      // ─── BUG FIX: audio_id NOT NULL constraint ──────────────────────
      // El endpoint de upload devuelve filename y archivo_path pero NO
      // devuelve un audio_id. La tabla pautas tiene audio_id NOT NULL,
      // por lo que guardar con _selectedId = null produce un error 500.
      //
      // Solución: después del upload, refrescar el catálogo de audios
      // y buscar el ID por coincidencia de nombre de archivo.
      // El backend registra el audio en la tabla audios durante el upload,
      // así que al refrescar debería aparecer.
      //
      // Primero: intentar leer el audio_id directo de la respuesta del
      // upload (si el backend lo incluye en una versión futura).
      if (result.audio_id) {
        fAudio._selectedId = result.audio_id;
        console.info(`[PautasController] audio_id resuelto desde respuesta upload: ${result.audio_id}`);
      } else {
        // El backend no retornó el ID → refrescar catálogo y buscar por nombre
        progressLbl.textContent = 'Verificando registro…';
        _audios = await fetchAudios();

        // Normalizar el nombre para la comparación: quitar extensión, minúsculas
        const baseName = nombre.replace(/\.[^/.]+$/, '').toLowerCase();

        const match = _audios.find(a => {
          const aNombre = (a.nombre ?? '').replace(/\.[^/.]+$/, '').toLowerCase();
          return aNombre === baseName || a.nombre === nombre;
        });

        if (match) {
          fAudio._selectedId = match.id;
          console.info(`[PautasController] audio_id resuelto desde catálogo: ${match.id} → "${match.nombre}"`);
        } else {
          // Último recurso: el audio fue subido pero el catálogo aún no lo refleja.
          // Dejar _selectedId = null y mostrar un aviso claro al operador para que
          // seleccione el audio desde la biblioteca en vez de subir uno nuevo.
          fAudio._selectedId = null;
          console.warn(`[PautasController] Audio subido pero no encontrado en catálogo (nombre: "${nombre}"). El operador debe seleccionarlo desde la biblioteca para obtener el audio_id.`);
          if (audioHint) {
            audioHint.textContent = `⚠ Subido pero no vinculado. Seleccioná "${nombre}" desde la lista para poder guardar.`;
            audioHint.style.color = 'var(--p-warn, #e6a817)';
          }
        }
      }
      // ────────────────────────────────────────────────────────────────

      if (audioHint && fAudio._selectedId) {
        audioHint.textContent = `✅ Subido y vinculado: ${nombre}`;
        audioHint.style.color = '';
      }
      progressBar.style.width = '100%';
      progressLbl.textContent = '¡Subida completada!';
      setTimeout(() => _hideEl(progressWrap), 2200);

    } catch (err) {
      console.error('[PautasController] Error al subir audio:', err);
      fAudio.value           = '';
      fAudio._selectedId     = null;
      fAudio._selectedNombre = '';
      if (audioHint) audioHint.textContent = `⚠ Error: ${err.message}`;
      progressBar.style.width = '0%';
      progressLbl.textContent = 'Error en la subida';
      progressLbl.style.color = 'var(--p-red)';
      setTimeout(() => {
        _hideEl(progressWrap);
        progressLbl.style.color = '';
      }, 3500);
    } finally {
      if (btnSelect) btnSelect.disabled = false;
      e.target.value = ''; // permitir re-selección del mismo archivo
    }
  }

  function _uploadWithProgress(url, formData, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.upload.addEventListener('progress', e => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      });
      xhr.addEventListener('load', () => {
        let json = {};
        try { json = JSON.parse(xhr.responseText); } catch (_) { /* no-op */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(json);
        else reject(new Error(json?.detail ?? json?.message ?? `HTTP ${xhr.status}`));
      });
      xhr.addEventListener('error', () => reject(new Error('Error de red al subir.')));
      xhr.addEventListener('abort', () => reject(new Error('Subida cancelada.')));
      xhr.send(formData);
    });
  }

  // ──────────────────────────────────────────────────────────
  // 18. MODAL DE BORRADO
  // ──────────────────────────────────────────────────────────

  function _openDeleteModal(id) {
    _deletingId = id;
    const p = _pautas.find(x => String(x.id) === String(id));
    if (!p) return;
    const el = $('deleteClienteName');
    if (el) el.textContent = p.cliente;
    _showEl($('deleteModal'));
  }

  function _closeDeleteModal() {
    _hideEl($('deleteModal'));
    _deletingId = null;
  }

  async function _deletePauta(id) {
    try {
      await _apiDeletePauta(id);
      _pautas = _pautas.filter(p => String(p.id) !== String(id));
      _render();
      console.info(`[PautasController] Pauta ${id} eliminada.`);
    } catch (err) {
      console.error(`[PautasController] Error al eliminar pauta ${id}:`, err);
      alert(`No se pudo eliminar la pauta: ${err.message}`);
    }
  }

  // ──────────────────────────────────────────────────────────
  // 19. VINCULACIÓN DE EVENTOS
  // ──────────────────────────────────────────────────────────

  function _bindEvents() {
    // ── Botón Nueva Pauta ──
    $('btnNuevaPauta')?.addEventListener('click', () => _openModal());

    // ── Modal principal ──
    $('btnModalClose')?.addEventListener('click', _closeModal);
    $('btnModalCancel')?.addEventListener('click', _closeModal);
    $('pautaModal')?.addEventListener('click', e => {
      if (e.target === $('pautaModal')) _closeModal();
    });
    $('btnModalSave')?.addEventListener('click', _savePauta);

    // ── Urgente: sincronizar badge del header en tiempo real ──
    $('fUrgente')?.addEventListener('change', _syncUrgenteHeader);

    // ── Menciones: botones +/- ──
    $('btnMencionesDown')?.addEventListener('click', () => {
      const inp = $('fMenciones');
      if (!inp) return;
      const val = Math.max(1, parseInt(inp.value, 10) - 1);
      inp.value = val;
      _checkMentionsWarning();
    });
    $('btnMencionesUp')?.addEventListener('click', () => {
      const inp = $('fMenciones');
      if (!inp) return;
      const val = Math.min(24, parseInt(inp.value, 10) + 1);
      inp.value = val;
      _checkMentionsWarning();
    });
    $('fMenciones')?.addEventListener('input', _checkMentionsWarning);

    // ── Selector de audio ──
    $('btnSelectAudio')?.addEventListener('click', _openFilePicker);
    $('fAudio')?.addEventListener('click', _openFilePicker);
    $('audioFileInput')?.addEventListener('change', _onAudioFileSelected);

    // ── Relleno rápido de la Matriz ──
    $('btnQfLV')?.addEventListener('click',       _matrixLV);
    $('btnQfFinde')?.addEventListener('click',    _matrixFinde);
    $('btnQfPrime')?.addEventListener('click',    _matrixPrimeTime);
    $('btnMatrixClear')?.addEventListener('click', _matrixClear);

    // ── Arrastre global: liberar al soltar fuera de la matriz ──
    document.addEventListener('mouseup', () => {
      if (_drag.active) {
        _drag.active = false;
        _updateMatrixSummary(_readMatrixFromDOM());
        _checkMentionsWarning();
      }
    });

    // ── Acciones de fila (delegación) ──
    $('pautasTableBody')?.addEventListener('click', e => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;
      const { action, id } = btn.dataset;
      if (action === 'edit')   _openModal(id);
      if (action === 'delete') _openDeleteModal(id);
    });

    // ── Búsqueda en tiempo real ──
    $('pautaSearch')?.addEventListener('input', e => {
      _searchTerm = e.target.value;
      _renderTable();
    });

    // ── Campana de notificaciones ──
    $('notifBell')?.addEventListener('click', e => {
      e.stopPropagation();
      _toggleNotifications();
    });
    $('notifClose')?.addEventListener('click', _closeNotifications);

    // Cerrar notificaciones al clicar fuera
    document.addEventListener('click', e => {
      const wrap = $('notifWrap');
      if (wrap && !wrap.contains(e.target)) _closeNotifications();
    });

    // ── Filtro avanzado ──
    $('btnFilterToggle')?.addEventListener('click', e => {
      e.stopPropagation();
      _toggleFilterPanel();
    });
    $('btnFilterClear')?.addEventListener('click', _clearFilters);

    // Cerrar filtro al clicar fuera
    document.addEventListener('click', e => {
      const wrap = $('filterWrap');
      if (wrap && !wrap.contains(e.target)) _hideFilterPanel();
    });

    // Radios de estado
    document.querySelectorAll('[name="filtEstado"]').forEach(radio => {
      radio.addEventListener('change', e => {
        _filterEstado = e.target.value;
        _applyFilter();
      });
    });

    // Select de rubro
    $('filtRubro')?.addEventListener('change', e => {
      _filterRubro = e.target.value;
      _applyFilter();
    });

    // ── Modal delete ──
    $('btnDeleteClose')?.addEventListener('click', _closeDeleteModal);
    $('btnDeleteCancel')?.addEventListener('click', _closeDeleteModal);
    $('btnDeleteConfirm')?.addEventListener('click', () => {
      if (_deletingId) _deletePauta(_deletingId);
      _closeDeleteModal();
    });
    $('deleteModal')?.addEventListener('click', e => {
      if (e.target === $('deleteModal')) _closeDeleteModal();
    });
  }

  // ──────────────────────────────────────────────────────────
  // 20. INICIALIZACIÓN
  // ──────────────────────────────────────────────────────────

  async function init() {
    if (_initialized) {
      console.info('[PautasController] init() — ya inicializado, omitiendo duplicados.');
      return;
    }
    console.info('[PautasController] init() — iniciando módulo v3...');

    // Cargar datos en paralelo
    const [, audios] = await Promise.all([fetchPautas(), fetchAudios()]);
    _audios = audios;

    _bindEvents();
    _render();

    // Inicializar WaveSurfer en segundo plano (warm-up)
    _initWaveSurfer().then(() => {
      console.info('[PautasController] WaveSurfer warm-up completado.');
    });

    _initialized = true;
    console.info(
      `[PautasController] ✅ Módulo v3 listo. Pautas: ${_pautas.length} | Audios: ${_audios.length}`
    );
  }

  function destroy() {
    // Destruir la instancia de WaveSurfer al desmontar la sección
    if (typeof global.wfDestroy === 'function') {
      global.wfDestroy();
      _wfReady = false;
    }
  }

  // ──────────────────────────────────────────────────────────
  // 21. API PÚBLICA (window.PautasController)
  // ──────────────────────────────────────────────────────────

  global.PautasController = {
    /** Punto de entrada. Llamar desde admin-app.js tras inyectar el HTML. */
    init,

    /** Limpiar recursos al desmontar la sección. */
    destroy,

    /** Fuerza una recarga desde la API REST. */
    async reload() {
      await fetchPautas();
      _render();
    },

    // Métodos de conveniencia para integración externa
    save:             _savePauta,
    openModal:        _openModal,
    closeModal:       _closeModal,
    deletePauta:      _deletePauta,
    openDeleteModal:  _openDeleteModal,
    closeDeleteModal: _closeDeleteModal,
  };

  // ─────────────────────────────────────────────────────────────────────
  // 22. AUTO-REGISTRO VÍA section:init (BUG #2 FIX)
  //
  // El shell dispara CustomEvent("section:init") sobre el panel después
  // de inyectar el HTML (ver loadSection() en index.html). Suscribirse
  // aquí hace al módulo resiliente: si por alguna razón el shell no llama
  // explícitamente a PautasController.init(), la sección igual arranca.
  //
  // El listener se auto-elimina tras la primera llamada para evitar que
  // navegar de vuelta a la sección dispare un init() duplicado.
  // ─────────────────────────────────────────────────────────────────────
  ;(function _autoRegister() {
    var panel = document.getElementById('section-panel-03-programacion-pautas');
    if (!panel) return;  // el panel ya existe en el DOM cuando este script carga

    function _onSectionInit() {
      panel.removeEventListener('section:init', _onSectionInit);
      init().catch(function(err) {
        console.error('[PautasController] Error en auto-init vía section:init:', err);
      });
    }

    panel.addEventListener('section:init', _onSectionInit);
  })();

})(window);
