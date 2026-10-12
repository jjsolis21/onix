/**
 * ============================================================
 * PAUTAS-CONTROLLER.JS  — v3.0  (Redesign Pro)
 * Módulo autónomo para la Sección 03 — Programación de Pautas
 *
 * NUEVAS FUNCIONALIDADES v3:
 *   · Editor de Cue Points vía onix-waveform.js (wfInit/wfLoadFile/wfLoadUrl)
 *   · Campos extra: rubro, agencia, contrato, prioridad, posición fija, menciones/día
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
  let _filterEstado= 'activa';  // 'todas'|'activa'|'hoy'|'vencida'|'por_vencer'
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

  /** Asigna si el campo existe (un HTML viejo en caché no rompe el modal) */
  function _val(id, v) { const el = $(id); if (el) el.value = v; }

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
   * Retorna: 'vencida' | 'por-vencer' | 'activa'
   */
  function _semaphore(pauta) {
    const hoy = _today();
    const fin = pauta.fecha_fin;

    if (!fin || fin < hoy)  return 'vencida';
    if (fin <= _offsetDate(DAYS_WARN)) return 'por-vencer';
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
      prioridad:    Number(data.prioridad ?? (data.matriz?.__meta?.urgente ? 1 : 0)) || 0,
      agencia:      data.agencia           ?? '',
      posicion:     Number(data.posicion   ?? 0) || 0,
      orden:        data.orden             ?? null,
      versiones:    data.versiones         ?? [],
      menciones:    data.menciones_diarias ?? data.matriz?.__meta?.menciones ?? 4,
      seccion:      data.seccion           ?? 3,
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
      // Solo cuñas (antes, si no encontraba la categoría COMERCIAL, mostraba
      // toda la biblioteca, canciones incluidas)
      const raw = await _apiFetch(`/api/v1/audios?tipo=${encodeURIComponent('Cuña')}&limit=50000`);
      return _normalizeAudios(Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? []));
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
    await _cargarRiesgo();
  }

  /** Cuñas de hoy que ya no alcanzan sus menciones (se refresca cada minuto) */
  let _riesgo = new Map();
  async function _cargarRiesgo() {
    try {
      const r = await _apiFetch('/api/v1/pautas-riesgo');
      _riesgo = new Map((r?.data || []).map(x => [x.pauta_id, x]));
    } catch (_) { _riesgo = new Map(); }
  }
  setInterval(async () => {
    if (!$('pautasRoot')?.offsetParent) return;
    await _cargarRiesgo();
    _renderTable();
  }, 60000);

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

  const PRIORIDAD = { '1': '▲ Alta', '0': '● Normal', '-1': '▼ Baja' };
  const POSICION  = { 1: 'Primero', 2: 'Último' };

  /** "2026-10-12" → "12/10/26" (cabe en pantallas chicas) */
  const _fechaCorta = f => (f ? `${f.slice(8, 10)}/${f.slice(5, 7)}/${f.slice(2, 4)}` : '—');

  /** ¿Sale hoy? Vigente y con alguna hora marcada para el día de hoy. */
  function _alAireHoy(p) {
    if (!_isActiva(p)) return false;
    const d = new Date().getDay();
    return Object.keys(p.matriz || {}).some(k => k.startsWith(d + '-') && p.matriz[k]);
  }

  function _renderTable() {
    const term  = _searchTerm.toLowerCase();
    const tbody = $('pautasTableBody');
    if (!tbody) return;

    const visible = _pautas.filter(p => {
      const meta = _getMeta(p);
      const rubro    = (p.rubro    || meta.rubro    || '');
      const contrato = (p.contrato || meta.contrato || '').toLowerCase();
      const matchSearch = !term ||
        p.cliente.toLowerCase().includes(term)     ||
        p.audio_nombre.toLowerCase().includes(term) ||
        rubro.toLowerCase().includes(term)           ||
        (p.agencia || '').toLowerCase().includes(term) ||
        contrato.includes(term);

      const semaf = _semaphore(p);
      let matchEstado = true;
      if      (_filterEstado === 'activa')     matchEstado = _isActiva(p);
      else if (_filterEstado === 'hoy')        matchEstado = _alAireHoy(p);
      else if (_filterEstado === 'vencida')    matchEstado = semaf === 'vencida';
      else if (_filterEstado === 'por_vencer') matchEstado = semaf === 'por-vencer';

      const f = _filtros;
      return matchSearch && matchEstado &&
        (!_filterRubro || rubro.toLowerCase() === _filterRubro.toLowerCase()) &&
        (!f.cliente  || p.cliente === f.cliente) &&
        (!f.agencia  || (p.agencia || '') === f.agencia) &&
        (f.prioridad === '' || String(p.prioridad) === f.prioridad) &&
        (f.posicion  === '' || String(p.posicion)  === f.posicion);
    });

    tbody.innerHTML = '';
    const cuenta = $('cmCuenta');
    if (cuenta) cuenta.textContent = `${visible.length} de ${_pautas.length} spots`;

    if (!visible.length) {
      _showEl($('pautasEmpty'));
      return;
    }
    _hideEl($('pautasEmpty'));

    // Fijados primero (como el alfiler de Jazler), luego por cliente
    visible.sort((a, b) => (+!a.posicion) - (+!b.posicion) || a.cliente.localeCompare(b.cliente));

    visible.forEach(p => {
      const semaf     = _semaphore(p);
      const meta      = _getMeta(p);
      const rubro     = p.rubro     || meta.rubro     || '—';
      const contrato  = p.contrato  || meta.contrato  || '';
      const menciones = p.menciones ?? meta.menciones ?? 4;

      const rowClass = { 'vencida': 'row--vencida', 'por-vencer': 'row--por-vencer' }[semaf] ?? '';
      const estado = semaf === 'vencida'
        ? `<span class="badge badge--dot badge--vencida">Vencido</span>`
        : semaf === 'por-vencer'
          ? `<span class="badge badge--dot badge--por-vencer">Por vencer</span>`
          : !_isActiva(p)
            ? `<span class="badge badge--dot">Aún no inicia</span>`
            : _alAireHoy(p)
              ? `<span class="badge badge--dot badge--activa">Al aire hoy</span>`
              : `<span class="badge badge--dot badge--activa">Activo</span>`;

      const rg = _riesgo.get(p.id);
      const aviso = rg ? `<span class="cm-riesgo ${rg.estado === 'incumplida' ? 'cm-riesgo--mal' : ''}"
        title="Hoy: salieron ${rg.emitidas} de ${rg.objetivo}; quedan ${rg.restantes} hora(s) marcada(s)">
        ⚠ ${rg.estado === 'incumplida' ? `${rg.perdidas} no salieron hoy` : `no alcanza: −${rg.perdidas} hoy`}</span>` : '';

      const tr = document.createElement('tr');
      tr.dataset.id = p.id;
      if (rowClass) tr.classList.add(rowClass);
      if (String(p.id) === String(_previa.id)) tr.classList.add('cm-sel');

      tr.innerHTML = `
        <td class="cm-exp"><button type="button" data-action="expandir" data-id="${_esc(String(p.id))}" title="Ver detalle">›</button></td>
        <td class="cm-pin" title="${p.posicion ? 'Siempre ' + POSICION[p.posicion].toLowerCase() + ' en la tanda' : ''}">${p.posicion ? '📌<small>' + (p.posicion === 1 ? '1º' : 'ÚLT') + '</small>' : ''}</td>
        <td><span class="cm-spot" title="${_esc(p.audio_nombre)}">${_esc(_truncate(p.audio_nombre, 34))}</span>
          ${(p.versiones || []).length > 1 ? `<small class="cm-sub cm-sub--ver">🔁 ${p.versiones.length} versiones</small>` : ''}
          ${contrato ? `<small class="cm-sub">${_esc(contrato)}</small>` : ''}</td>
        <td><strong>${_esc(p.cliente)}</strong></td>
        <td>${_esc(p.agencia || '—')}</td>
        <td>${_esc(_truncate(rubro, 22))}</td>
        <td class="cm-fecha">${_fechaCorta(p.fecha_inicio)}</td>
        <td class="cm-fecha">${p.fecha_fin ? _fechaCorta(p.fecha_fin) : 'Nunca'}</td>
        <td class="cm-num">${menciones}</td>
        <td class="cm-pri cm-pri--${p.prioridad > 0 ? 'alta' : p.prioridad < 0 ? 'baja' : 'normal'}">${PRIORIDAD[String(p.prioridad)] || PRIORIDAD['0']}</td>
        <td>${estado}${aviso}</td>
        <td>
          <div class="row-actions">
            <button class="btn-row"
              title="Reporte del cliente" data-action="reporte" data-id="${_esc(String(p.id))}">🖨</button>
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
    _populateFiltros();
    _renderTable();
  }

  // ──────────────────────────────────────────────────────────
  // 10. FILTROS (panel izquierdo, estilo Jazler)
  // ──────────────────────────────────────────────────────────

  const _filtros = { cliente: '', agencia: '', prioridad: '', posicion: '' };

  function _llenarSelect(id, valores, todos) {
    const sel = $(id);
    if (!sel) return;
    const actual = sel.value;
    sel.innerHTML = `<option value="">${todos}</option>` +
      valores.map(v => `<option value="${_esc(v)}">${_esc(v)}</option>`).join('');
    sel.value = valores.includes(actual) ? actual : '';
  }

  /** Llena rubros, clientes y agencias con los valores del listado */
  function _populateFiltros() {
    const unicos = f => [...new Set(_pautas.map(f).filter(Boolean))].sort();
    _llenarSelect('filtRubro', unicos(p => p.rubro || _getMeta(p).rubro || ''), 'Todos');
    _llenarSelect('filtCliente', unicos(p => p.cliente), 'Todos');
    const agencias = unicos(p => p.agencia);
    _llenarSelect('filtAgencia', agencias, 'Todas');
    const dl = $('cmAgencias');
    if (dl) dl.innerHTML = agencias.map(a => `<option value="${_esc(a)}">`).join('');
    _filterRubro     = $('filtRubro')?.value   ?? '';
    _filtros.cliente = $('filtCliente')?.value ?? '';
    _filtros.agencia = $('filtAgencia')?.value ?? '';
  }

  function _applyFilter() {
    _renderTable();
  }

  function _clearFilters() {
    _filterEstado = 'activa';
    _filterRubro  = '';
    Object.assign(_filtros, { cliente: '', agencia: '', prioridad: '', posicion: '' });
    if ($('filtEstadoSel')) $('filtEstadoSel').value = 'activa';
    ['filtRubro', 'filtCliente', 'filtAgencia', 'filtPrioridad', 'filtPosicion', 'pautaSearch']
      .forEach(id => { if ($(id)) $(id).value = ''; });
    _searchTerm = '';
    _renderTable();
  }

  // ──────────────────────────────────────────────────────────
  // 10b. VISTA PREVIA (clic = escuchar · doble clic = editar)
  // ──────────────────────────────────────────────────────────

  const _previa = { id: null, audio: null, onda: null };

  // ──────────────────────────────────────────────────────────
  // 10c. VERSIONES QUE ROTAN (spot con varios audios)
  // ──────────────────────────────────────────────────────────

  let _versiones = [];   // versiones extra (sin la principal): [{id, titulo, emitidas}]

  function _pintarVersiones() {
    const ol = $('cmVersiones');
    if (!ol) return;
    const f = $('fAudio');
    const principal = f?._selectedId
      ? `<li class="cm-ver cm-ver--principal"><b>A</b><span>${_esc(f._selectedNombre || f.value)}</span><small>principal</small></li>` : '';
    ol.innerHTML = principal + _versiones.map((v, i) => `
      <li class="cm-ver"><b>${String.fromCharCode(66 + i)}</b><span>${_esc(v.titulo)}</span>
        ${v.emitidas ? `<small>${v.emitidas} salidas</small>` : ''}
        <button type="button" data-quitar="${i}" title="Quitar esta versión">✕</button></li>`).join('');
  }

  async function _agregarVersion() {
    if (!$('fAudio')?._selectedId) { _showModalError('Primero elige la cuña principal.'); return; }
    if (!global.CunasBiblioteca) return;
    const c = await global.CunasBiblioteca.elegir(($('fCliente')?.value || '').trim());
    if (!c || c.id === $('fAudio')._selectedId || _versiones.some(v => v.id === c.id)) return;
    _versiones.push({ id: c.id, titulo: c.titulo });
    _pintarVersiones();
  }

  // ──────────────────────────────────────────────────────────
  // 10d. ORDEN MANUAL DEL BLOQUE
  // ──────────────────────────────────────────────────────────

  function _abrirOrden() {
    const comerciales = _pautas.filter(p => _isActiva(p) && !p.posicion && (p.seccion ?? 3) === 3);
    const fijas1 = _pautas.filter(p => _isActiva(p) && p.posicion === 1);
    const fijas2 = _pautas.filter(p => _isActiva(p) && p.posicion === 2);
    comerciales.sort((a, b) => (a.orden ?? 9999) - (b.orden ?? 9999) || b.prioridad - a.prioridad || a.id - b.id);
    const fila = (p, fija) => `<li class="cm-ord-item ${fija ? 'cm-ord-item--fija' : ''}" data-id="${p.id}" ${fija ? '' : 'draggable="true"'}>
      <span class="cm-ord-n"></span><span class="cm-ord-t"><b>${_esc(p.cliente)}</b> ${_esc(p.audio_nombre)}</span>
      ${fija ? `<small>📌 ${fija}</small>` : '<button type="button" data-mover="-1" title="Subir">▲</button><button type="button" data-mover="1" title="Bajar">▼</button>'}</li>`;
    $('ordLista').innerHTML = fijas1.map(p => fila(p, 'primero')).join('') +
      comerciales.map(p => fila(p)).join('') + fijas2.map(p => fila(p, 'último')).join('');
    $('ordMsg').textContent = comerciales.some(p => p.orden != null) ? '' : 'Ahora decide el sistema (prioridad y rubro). Si guardas, manda este orden.';
    _numerarOrden();
    _showEl($('ordenModal'));
  }

  function _numerarOrden() {
    $('ordLista').querySelectorAll('.cm-ord-item').forEach((li, i) => { li.querySelector('.cm-ord-n').textContent = i + 1; });
  }

  async function _guardarOrden(ids) {
    try {
      const r = await fetch('/api/v1/pautas/orden', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      _pautas.forEach(p => { const i = ids.indexOf(p.id); p.orden = i >= 0 ? i : null; });
      _hideEl($('ordenModal'));
    } catch (e) {
      $('ordMsg').textContent = '⚠ No se pudo guardar: ' + e.message;
    }
  }

  function _mmss(t) {
    t = Math.max(0, Math.floor(t || 0));
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
  }

  function _elegirPrevia(id) {
    const p = _pautas.find(x => String(x.id) === String(id));
    if (!p || !p.audio_id) return;
    _previa.id = p.id;
    document.querySelectorAll('#pautasTableBody tr').forEach(tr =>
      tr.classList.toggle('cm-sel', tr.dataset.id === String(p.id)));
    if (!_previa.audio) {
      const a = _previa.audio = new Audio();
      a.addEventListener('timeupdate', () => {
        $('cmPreviaT').textContent = `${_mmss(a.currentTime)} / ${_mmss(a.duration)}`;
      });
      const icono = () => { $('cmPlay').innerHTML = ICO(a.paused ? 'play2' : 'pause2'); };
      a.addEventListener('play', icono);
      a.addEventListener('pause', icono);
      a.addEventListener('ended', icono);
    }
    _previa.audio.src = `/api/v1/audios/${p.audio_id}/stream`;
    $('cmPreviaTit').textContent = `${p.cliente} — ${p.audio_nombre}`;
    ['cmPlay', 'cmStop', 'cmAtras', 'cmAdelante'].forEach(id => { $(id).disabled = false; });
    // Onda del spot (clic = ir a ese punto), sobre el mismo audio
    try { _previa.onda?.destroy(); } catch (_) { }
    _previa.onda = typeof WaveSurfer !== 'undefined' ? WaveSurfer.create({
      container: $('cmOnda'), media: _previa.audio, height: 46, normalize: true,
      waveColor: '#5d6675', progressColor: '#f5b301', cursorColor: '#fff', cursorWidth: 2,
      barWidth: 2, barGap: 1, dragToSeek: true,
    }) : null;
    if ($('cmAutoPlay')?.checked) _previa.audio.play().catch(() => {});
  }

  // ── Reporte por cliente ──
  function _abrirReporte(cliente) {
    const clientes = [...new Set(_pautas.map(p => p.cliente))].sort();
    if (!clientes.length) { alert('Todavía no hay pautas.'); return; }
    $('repCliente').innerHTML = clientes.map(c => `<option value="${_esc(c)}">${_esc(c)}</option>`).join('');
    $('repCliente').value = cliente || _filtros.cliente || clientes[0];
    _rangoReporte('mes');
    _showEl($('reporteModal'));
  }

  function _rangoReporte(rango) {
    const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const hoy = new Date();
    let desde = new Date(hoy.getFullYear(), hoy.getMonth(), 1), hasta = hoy;
    if (rango === 'pasado') { desde = new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1); hasta = new Date(hoy.getFullYear(), hoy.getMonth(), 0); }
    if (rango === '7') desde = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate() - 6);
    if (rango === 'vigencia') {
      const ps = _pautas.filter(p => p.cliente === $('repCliente').value);
      const ini = ps.map(p => p.fecha_inicio).filter(Boolean).sort()[0];
      const fin = ps.map(p => p.fecha_fin).filter(Boolean).sort().pop();
      $('repDesde').value = ini || iso(desde);
      $('repHasta').value = fin && fin < iso(hoy) ? fin : iso(hoy);
      return;
    }
    $('repDesde').value = iso(desde);
    $('repHasta').value = iso(hasta);
  }

  function _saltar(seg) {
    const a = _previa.audio;
    if (a && a.duration) a.currentTime = Math.min(a.duration, Math.max(0, a.currentTime + seg));
  }

  /** Detalle desplegable de la fila (›): horario, días, versiones y notas */
  function _detalle(p) {
    const m = p.matriz || {};
    const horasDe = d => Object.keys(m).filter(k => k.startsWith(d + '-') && m[k]).map(k => +k.split('-')[1]).sort((a, b) => a - b);
    const dias = [1, 2, 3, 4, 5, 6, 0].filter(d => horasDe(d).length);
    const hoy = horasDe(new Date().getDay());
    const vers = (p.versiones || []).map((v, i) => `${String.fromCharCode(65 + i)}: ${_esc(v.titulo)} (${v.emitidas} salidas)`).join(' · ');
    return `<div class="cm-det">
      <div><span>Días</span>${dias.map(d => DIAS[d]).join(', ') || '—'}</div>
      <div><span>Horas hoy</span>${hoy.map(h => HORAS[h].slice(0, 5)).join(' · ') || 'no sale hoy'}</div>
      <div><span>Contrato</span>${_esc(p.contrato || '—')}</div>
      ${vers ? `<div class="cm-det-ancho"><span>Versiones</span>${vers}</div>` : ''}
      ${p.notas ? `<div class="cm-det-ancho"><span>Notas</span>${_esc(p.notas)}</div>` : ''}
    </div>`;
  }

  function _pararPrevia() {
    if (!_previa.audio) return;
    _previa.audio.pause();
    _previa.audio.currentTime = 0;
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
    if (_menc.modo === 'auto') {
      // Tocar la matriz a mano = pasar a modo manual con lo que ya está marcado
      _setModoMenciones('manual');
    }
    _drag.active     = true;
    _drag.startState = cell.dataset.state === 'on' ? 'off' : 'on';
    _toggleCell(cell, _drag.startState);
    _checkMentionsWarning();
  }

  function _onCellMouseEnter(e) {
    if (!_drag.active) return;
    _toggleCell(e.currentTarget, _drag.startState);
  }

  // ── Menciones diarias: modo automático o manual ──────────────
  //  Automático: N horas por día, repartidas parejo entre "desde" y "hasta",
  //              en los días elegidos. Se recalcula al cambiar N, días o franja.
  //  Manual:     el usuario marca, pero nunca más de N horas en un día.
  const _menc = { modo: 'auto', dias: [1, 2, 3, 4, 5], desde: 6, hasta: 22 };

  function _menciones() {
    const n = parseInt($('fMenciones')?.value ?? '4', 10);
    return Number.isFinite(n) && n > 0 ? n : 1;
  }

  function _porDia(matriz) {
    const n = Array(7).fill(0);
    Object.keys(matriz).filter(k => k !== '__meta').forEach(k => n[+k.split('-')[0]]++);
    return n;
  }

  function _initMencionesUI() {
    const opts = HORAS.map((h, i) => `<option value="${i}">${h}</option>`).join('');
    $('mencDesde').innerHTML = opts;
    $('mencHasta').innerHTML = opts;
    $('mencDias').innerHTML = [1, 2, 3, 4, 5, 6, 0].map(d =>
      `<label class="menc-dia"><input type="checkbox" value="${d}"> ${DIAS[d]}</label>`).join('') +
      `<button type="button" class="menc-preset" data-dias="1,2,3,4,5">Lun–Vie</button>` +
      `<button type="button" class="menc-preset" data-dias="0,1,2,3,4,5,6">Todos</button>`;
    document.querySelectorAll('.menc-btn').forEach(b =>
      b.addEventListener('click', () => _setModoMenciones(b.dataset.modo)));
    $('mencDias').addEventListener('change', () => {
      _menc.dias = [...document.querySelectorAll('#mencDias input:checked')].map(i => +i.value);
      _distribuir();
    });
    $('mencDias').addEventListener('click', e => {
      const p = e.target.closest('.menc-preset');
      if (!p) return;
      _menc.dias = p.dataset.dias.split(',').map(Number);
      _pintarControlesMenc();
      _distribuir();
    });
    ['mencDesde', 'mencHasta'].forEach(id => $(id).addEventListener('change', () => {
      _menc.desde = +$('mencDesde').value; _menc.hasta = +$('mencHasta').value;
      _distribuir();
    }));
  }

  function _pintarControlesMenc() {
    document.querySelectorAll('.menc-btn').forEach(b => b.classList.toggle('activo', b.dataset.modo === _menc.modo));
    $('mencAuto').hidden = _menc.modo !== 'auto';
    document.querySelectorAll('#mencDias input').forEach(i => { i.checked = _menc.dias.includes(+i.value); });
    $('mencDesde').value = _menc.desde; $('mencHasta').value = _menc.hasta;
    // Los rellenos rápidos marcan días enteros: solo tienen sentido para "Limpiar"
    ['btnQfLV', 'btnQfFinde', 'btnQfPrime'].forEach(id => { const b = $(id); if (b) b.hidden = true; });
    const hint = $('matrixHint');
    if (hint) hint.textContent = _menc.modo === 'auto'
      ? 'Vista previa de las horas elegidas. Si tocas la matriz pasas a modo manual.'
      : `Clic o arrastre para marcar · máximo ${_menciones()} por día`;
  }

  function _setModoMenciones(modo) {
    _menc.modo = modo;
    _pintarControlesMenc();
    if (modo === 'auto') _distribuir();
    else _checkMentionsWarning();
  }

  /** Reparte N horas por día, parejo dentro de la franja, en los días elegidos. */
  function _distribuir() {
    if (_menc.modo !== 'auto') return;
    const n = _menciones();
    const desde = Math.min(_menc.desde, _menc.hasta), hasta = Math.max(_menc.desde, _menc.hasta);
    const horas = [];
    for (let h = desde; h <= hasta; h++) horas.push(h);
    const cuantas = Math.min(n, horas.length);
    const elegidas = new Set();
    for (let i = 0; i < cuantas; i++) elegidas.add(horas[Math.floor((i + 0.5) * horas.length / cuantas)]);
    document.querySelectorAll('.matrix-cell[data-key]').forEach(c => {
      const [d, h] = c.dataset.key.split('-').map(Number);
      c.dataset.state = _menc.dias.includes(d) && elegidas.has(h) ? 'on' : 'off';
    });
    _updateMatrixSummary(_readMatrixFromDOM());
    const info = $('mencInfo');
    if (info) {
      info.classList.toggle('menc-info--error', n > horas.length);
      info.textContent = !_menc.dias.length ? 'Elige al menos un día.'
        : n > horas.length
          ? `${n} menciones no entran entre las ${HORAS[desde]} y las ${HORAS[hasta]} (máximo ${horas.length}: una por hora). Amplía la franja.`
          : `${n} por día a las ${[...elegidas].sort((a, b) => a - b).map(h => HORAS[h].slice(0, 2) + 'h').join(', ')}`;
    }
    _hideEl($('mentionsWarning'));
  }

  function _onCellMouseUp() {
    _drag.active = false;
    _updateMatrixSummary(_readMatrixFromDOM());
    _checkMentionsWarning();
  }

  function _toggleCell(cell, newState) {
    if (newState === 'on' && cell.dataset.state !== 'on') {
      // Manual: nunca más de N horas en un día
      const d = +cell.dataset.key.split('-')[0];
      if (_porDia(_readMatrixFromDOM())[d] >= _menciones()) {
        cell.classList.remove('matrix-cell--lleno');
        void cell.offsetWidth;                      // reinicia la animación
        cell.classList.add('matrix-cell--lleno');
        const info = $('mencInfo');
        _menc.avisoHasta = Date.now() + 4000;       // que el aviso no lo borre el mouseup
        if (info) {
          info.classList.add('menc-info--error');
          info.textContent = `${DIAS[d]} ya tiene ${_menciones()} de ${_menciones()}: quita una hora para elegir otra, o sube las menciones.`;
        }
        return;
      }
    }
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
    const porDia = _porDia(matriz).filter(n => n > 0);
    const iguales = porDia.every(n => n === porDia[0]);
    el.textContent =
      `${count} mención${count !== 1 ? 'es' : ''} por semana en ${days} día${days !== 1 ? 's' : ''} · ` +
      (iguales ? `${porDia[0]} por día` : `entre ${Math.min(...porDia)} y ${Math.max(...porDia)} por día`);
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

    if (_menc.modo === 'auto') { _distribuir(); return; }
    _pintarControlesMenc();
    const matriz = _readMatrixFromDOM();
    const perDay = _porDia(matriz);
    const info = $('mencInfo');
    if (info && Date.now() > (_menc.avisoHasta || 0)) {
      const faltan = perDay.map((c, d) => c > 0 && c < menciones ? `${DIAS[d]} ${c}/${menciones}` : null).filter(Boolean);
      info.classList.remove('menc-info--error');
      info.textContent = faltan.length ? `Faltan horas: ${faltan.join(' · ')}` : '';
    }

    const maxDay  = Math.max(...perDay);
    const offenders = perDay
      .map((n, d) => n > menciones ? DIAS[d] : null)
      .filter(Boolean);

    if (maxDay > menciones && offenders.length) {
      _showEl(warnEl);
      if (warnTxt) {
        warnTxt.textContent =
          `${offenders.join(', ')} tienen más de ${menciones} hora${menciones !== 1 ? 's' : ''} marcada${menciones !== 1 ? 's' : ''}. ` +
          `Quita las que sobran: no se puede guardar así.`;
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
    // Siempre se pide en el contenedor de Pautas: el editor es compartido con la
    // Biblioteca y puede haber quedado dibujado allí.

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
    var wfPromise = global.wfInit('waveform').then(function() { _wfReady = true; });
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
      _val('fPrioridad', String(p.prioridad ?? 0));
      _val('fPosicion',  String(p.posicion ?? 0));
      _val('fAgencia',   p.agencia || '');
      _versiones = (p.versiones || []).filter(v => v.id !== p.audio_id);
      _pintarVersiones();
      _cargarSecciones(p.seccion ?? 3);
      $('btnSaveText').textContent = 'Actualizar Pauta';

      _buildMatrix(p.matriz);
      // Pautas viejas (sin modo guardado) se abren en manual con lo que tenían
      Object.assign(_menc, meta.modo === 'auto'
        ? { modo: 'auto', dias: meta.dias ?? [1, 2, 3, 4, 5], desde: meta.desde ?? 6, hasta: meta.hasta ?? 22 }
        : { modo: 'manual' });
      _pintarControlesMenc();
      if (_menc.modo === 'manual') _checkMentionsWarning();
      else _distribuir();

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
      _val('fPrioridad', '0');
      _val('fPosicion',  '0');
      _val('fAgencia',   '');
      _versiones = [];
      _pintarVersiones();
      _cargarSecciones(3);
      $('btnSaveText').textContent = 'Guardar Pauta';

      _buildMatrix({});
      Object.assign(_menc, { modo: 'auto', dias: [1, 2, 3, 4, 5], desde: 6, hasta: 22 });
      _pintarControlesMenc();
      _distribuir();
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
    const sel   = $('fPrioridad');
    const badge = $('urgenteBadgeHeader');
    if (!sel || !badge) return;
    badge.textContent = 'PRIORIDAD ALTA';
    sel.value === '1' ? _showEl(badge) : _hideEl(badge);
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
    const prioridad= parseInt($('fPrioridad')?.value ?? '0', 10) || 0;
    const posicion = parseInt($('fPosicion')?.value ?? '0', 10) || 0;
    const agencia  = ($('fAgencia')?.value   ?? '').trim().toUpperCase();
    const urgente  = prioridad > 0;
    const menciones= parseInt($('fMenciones')?.value ?? '4', 10);
    const seccion  = parseInt($('fSeccion')?.value ?? '3', 10) || 3;

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
    const porDia = _porDia(rawMatrix);
    const sobran = porDia.map((c, d) => c > menciones ? DIAS[d] : null).filter(Boolean);
    if (sobran.length) {
      _showModalError(`${sobran.join(', ')}: hay más horas marcadas que ${menciones} menciones por día.`);
      return;
    }
    const faltan = porDia.map((c, d) => c > 0 && c < menciones ? `${DIAS[d]} (${c})` : null).filter(Boolean);
    if (faltan.length && !confirm(`Estos días tienen menos de ${menciones} horas: ${faltan.join(', ')}.\n` +
                                  `Esos días saldrá menos veces. ¿Guardar igual?`)) return;

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
        modo:  _menc.modo,
        dias:  _menc.dias,
        desde: _menc.desde,
        hasta: _menc.hasta,
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
      prioridad,
      agencia,
      posicion,
      versiones: _versiones.map(v => v.id),
      menciones_diarias: menciones,
      seccion,
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

  /** El campo de audio abre la Biblioteca de cuñas (antes abría el explorador de archivos). */
  async function _elegirCuna() {
    if (!global.CunasBiblioteca) { _openFilePicker(); return; }
    const c = await global.CunasBiblioteca.elegir(($('fCliente')?.value || '').trim());
    if (c) _asignarCuna(c);
  }

  function _asignarCuna(c) {
    const f = $('fAudio');
    f.value = `${c.titulo} — ${c.artista}`;
    f._selectedId = c.id;
    f._selectedNombre = c.titulo;
    f._selectedPath = c.archivo_path || '';
    const hint = $('audioHint');
    if (hint) { hint.textContent = `✅ ${c.titulo} (${c.artista})`; hint.style.color = ''; }
    if (!$('fCliente').value.trim() && c.artista && c.artista !== 'SIN ANUNCIANTE') $('fCliente').value = c.artista;
    if (typeof global.wfLoadUrl === 'function') global.wfLoadUrl(`/api/v1/audios/${c.id}/stream`, {});
    _versiones = _versiones.filter(v => v.id !== c.id);
    _pintarVersiones();
  }

  /** "＋ Pauta" desde la Biblioteca de cuñas: pauta nueva con esa cuña ya elegida. */
  async function _nuevaConCuna(c) {
    document.querySelector('.cunas-tab[data-tab="pautas"]')?.click();
    await _openModal(null);
    _asignarCuna(c);
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
      // Se sube a la Biblioteca de cuñas (tipo Cuña, anunciante = cliente):
      // la respuesta trae el id, así que la pauta queda vinculada siempre.
      const creada = await global.CunasBiblioteca.subir(file, ($('fCliente')?.value || '').trim(), pct => {
        progressBar.style.width = `${pct}%`;
        progressLbl.textContent = `Subiendo… ${pct}%`;
      });
      const result = { audio_id: creada.id, filename: creada.titulo, archivo_path: creada.archivo_path };
      const subdir = '';

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
    $('fPrioridad')?.addEventListener('change', _syncUrgenteHeader);

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
    _initMencionesUI();

    // ── Selector de audio ──
    $('btnSelectAudio')?.addEventListener('click', _openFilePicker);
    $('fAudio')?.addEventListener('click', _elegirCuna);
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
      if (action === 'expandir') {
        const tr = btn.closest('tr');
        const abierto = tr.nextElementSibling?.classList.contains('cm-det-fila');
        if (abierto) tr.nextElementSibling.remove();
        else {
          const p = _pautas.find(x => String(x.id) === String(id));
          tr.insertAdjacentHTML('afterend', `<tr class="cm-det-fila"><td colspan="12">${_detalle(p)}</td></tr>`);
        }
        btn.classList.toggle('abierto', !abierto);
      }
      if (action === 'reporte') _abrirReporte(_pautas.find(p => String(p.id) === String(id))?.cliente);
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

    // ── Filtros del panel izquierdo ──
    $('btnFilterClear')?.addEventListener('click', _clearFilters);
    $('filtEstadoSel')?.addEventListener('change', e => { _filterEstado = e.target.value; _applyFilter(); });
    [['filtCliente', 'cliente'], ['filtAgencia', 'agencia'], ['filtPrioridad', 'prioridad'], ['filtPosicion', 'posicion']]
      .forEach(([id, k]) => $(id)?.addEventListener('change', e => { _filtros[k] = e.target.value; _applyFilter(); }));

    // ── Vista previa: clic = escuchar · doble clic = editar ──
    $('pautasTableBody')?.addEventListener('click', e => {
      if (e.target.closest('[data-action]')) return;
      const tr = e.target.closest('tr[data-id]');
      if (tr) _elegirPrevia(tr.dataset.id);
    });
    $('pautasTableBody')?.addEventListener('dblclick', e => {
      const tr = e.target.closest('tr[data-id]');
      if (tr && !e.target.closest('[data-action]')) { _pararPrevia(); _openModal(tr.dataset.id); }
    });
    $('cmPlay')?.addEventListener('click', () => {
      const a = _previa.audio;
      if (a) a.paused ? a.play().catch(() => {}) : a.pause();
    });
    $('cmStop')?.addEventListener('click', _pararPrevia);

    // ── Versiones que rotan ──
    $('btnAddVersion')?.addEventListener('click', _agregarVersion);
    $('cmVersiones')?.addEventListener('click', e => {
      const b = e.target.closest('[data-quitar]');
      if (b) { _versiones.splice(+b.dataset.quitar, 1); _pintarVersiones(); }
    });

    // ── Orden del bloque ──
    $('btnOrdenBloque')?.addEventListener('click', _abrirOrden);
    $('ordCerrar')?.addEventListener('click', () => _hideEl($('ordenModal')));
    $('ordAuto')?.addEventListener('click', () => _guardarOrden([]));
    $('ordGuardar')?.addEventListener('click', () => _guardarOrden(
      [...$('ordLista').querySelectorAll('.cm-ord-item:not(.cm-ord-item--fija)')].map(li => +li.dataset.id)));
    $('ordLista')?.addEventListener('click', e => {
      const b = e.target.closest('[data-mover]');
      if (!b) return;
      const li = b.closest('li');
      const otro = b.dataset.mover === '-1' ? li.previousElementSibling : li.nextElementSibling;
      if (!otro || otro.classList.contains('cm-ord-item--fija')) return;
      b.dataset.mover === '-1' ? otro.before(li) : otro.after(li);
      _numerarOrden();
    });
    let _arrastrado = null;
    $('ordLista')?.addEventListener('dragstart', e => { _arrastrado = e.target.closest('li'); _arrastrado?.classList.add('arrastrando'); });
    $('ordLista')?.addEventListener('dragend', () => { _arrastrado?.classList.remove('arrastrando'); _arrastrado = null; _numerarOrden(); });
    $('ordLista')?.addEventListener('dragover', e => {
      const li = e.target.closest('li');
      if (!_arrastrado || !li || li === _arrastrado || li.classList.contains('cm-ord-item--fija')) return;
      e.preventDefault();
      const r = li.getBoundingClientRect();
      e.clientY < r.top + r.height / 2 ? li.before(_arrastrado) : li.after(_arrastrado);
    });

    // ── Reporte por cliente ──
    $('btnReporte')?.addEventListener('click', () => _abrirReporte());
    $('repCerrar')?.addEventListener('click', () => _hideEl($('reporteModal')));
    $('reporteModal')?.addEventListener('click', e => { if (e.target.id === 'reporteModal') _hideEl($('reporteModal')); });
    $('repCliente')?.addEventListener('change', () => _rangoReporte('mes'));
    document.querySelectorAll('.cm-rep-rapido [data-rango]').forEach(b =>
      b.addEventListener('click', () => _rangoReporte(b.dataset.rango)));
    $('repAbrir')?.addEventListener('click', () => {
      const q = new URLSearchParams({ cliente: $('repCliente').value, desde: $('repDesde').value, hasta: $('repHasta').value });
      window.open(`/api/v1/reportes/certificado?${q}`, '_blank');
    });
    $('cmAtras')?.addEventListener('click', () => _saltar(-5));
    $('cmAdelante')?.addEventListener('click', () => _saltar(5));
    // Teclado en la lista (si no se está escribiendo): Espacio, ← →
    document.addEventListener('keydown', e => {
      if (!_previa.audio || !$('pautasRoot')?.offsetParent) return;
      if (e.target.closest('input, textarea, select, [contenteditable]')) return;
      if (!$('pautaModal')?.classList.contains('hidden')) return;
      if (e.code === 'Space') { e.preventDefault(); $('cmPlay').click(); }
      else if (e.key === 'ArrowLeft') _saltar(-5);
      else if (e.key === 'ArrowRight') _saltar(5);
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

  // Secciones de la tanda (Admin → 06 Motor de audio las define)
  async function _cargarSecciones(valor) {
    const sel = $('fSeccion');
    if (!sel) return;
    try {
      const secs = await fetch('/api/v1/tandas/secciones').then(r => r.json());
      sel.innerHTML = secs.map(s =>
        `<option value="${s.numero}">${s.numero} · ${_esc(s.nombre)}${s.comercial ? '' : (s.opcional ? ' (opcional)' : ' (fija)')}</option>`
      ).join('');
    } catch (e) {
      console.warn('[Pautas] Secciones no disponibles:', e);
    }
    sel.value = String(valor ?? 3);
    if (!sel.value) sel.value = '3';
  }

  async function init() {
    // La marca va ANTES del primer await: el shell y section:init llaman casi
    // a la vez y, si no, los dos registraban los eventos (clics dobles).
    if (_initialized) {
      console.info('[PautasController] init() — ya inicializado, omitiendo duplicados.');
      return;
    }
    _initialized = true;
    console.info('[PautasController] init() — iniciando módulo v3...');

    // Cargar datos en paralelo
    let audios;
    try {
      [, audios] = await Promise.all([fetchPautas(), fetchAudios()]);
    } catch (err) {
      _initialized = false;      // que el próximo intento vuelva a probar
      throw err;
    }
    _audios = audios;

    _bindEvents();
    _render();
    global.CunasBiblioteca?.montar();

    // Inicializar WaveSurfer en segundo plano (warm-up)
    _initWaveSurfer().then(() => {
      console.info('[PautasController] WaveSurfer warm-up completado.');
    });

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
    nuevaConCuna:     _nuevaConCuna,
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
