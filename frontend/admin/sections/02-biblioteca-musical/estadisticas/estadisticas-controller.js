/**
 * ============================================================
 * ÓNIX FM · Módulo de Estadísticas y Auditoría
 * Archivo: sections/02-biblioteca-musical/estadisticas/estadisticas-controller.js
 *
 * Arquitectura: IIFE + patrón Module para encapsulamiento total.
 * No depende de frameworks externos; compatible con admin-app.js
 * mediante el objeto window.EstadisticasModule que se expone al final.
 *
 * Endpoints consumidos:
 *   GET /api/v1/stats/top40?period={day|week|month}
 *   GET /api/v1/stats/top40/forgotten
 *   GET /api/v1/stats/detalle/{id}
 * ============================================================
 */

; (function (global) {
  'use strict';

  // ──────────────────────────────────────────────────────────
  //  CONFIGURACIÓN CENTRALIZADA
  // ──────────────────────────────────────────────────────────

  const CONFIG = {
    API_BASE: '/api/v1/stats',
    DOUBLE_CLICK_MS: 350,       // ventana para detectar doble clic
    SEEK_SKIP_SEC: 10,        // segundos para rewind / forward
    PAGE_SIZE: 40,        // ítems visibles inicialmente en el Top
    DEBOUNCE_SEARCH: 220,       // ms para debounce de búsqueda
    WAVEFORM_BARS: 80,        // barras del waveform simulado
    CATEGORIES: [               // Marca Blanca: etiquetas sin género real
      'Categoría 1', 'Categoría 2', 'Categoría 3',
      'Categoría 4', 'Categoría 5', 'Categoría 6',
    ],
  };

  // ──────────────────────────────────────────────────────────
  //  ESTADO INTERNO DEL MÓDULO
  // ──────────────────────────────────────────────────────────

  const STATE = {
    period: 'day',      // Período activo: day | week | month
    top40All: [],         // Todos los ítems del Top 40 (cache)
    top40Filtered: [],         // Después de aplicar búsqueda
    top40Page: 1,          // Paginación del Top 40
    forgottenAll: [],         // Cache del reporte de olvido
    currentItemId: null,       // ID del ítem abierto en el modal
    currentItemData: null,       // Datos del ítem abierto en el modal
    logAll: [],         // Historial completo de emisiones
    logFiltered: [],         // Historial filtrado por fecha
    playerPlaying: false,      // Estado play/pause del reproductor
    playerMuted: false,      // Estado mute del volumen
    clickTimer: null,       // Timer para distinguir 1 clic de 2 clics
    waveformData: [],         // Datos generados para el waveform
    abortController: null,       // Para cancelar fetches en vuelo
  };

  // ──────────────────────────────────────────────────────────
  //  REFERENCIAS A ELEMENTOS DEL DOM
  //  (se resuelven lazy en init() para soportar carga dinámica)
  // ──────────────────────────────────────────────────────────

  let DOM = {};

  function resolveDOM() {
    DOM = {
      // Filtros de período
      periodBtns: document.querySelectorAll('.stats-period-btn'),
      periodSlider: document.querySelector('.stats-period-slider'),

      // Búsqueda
      searchInput: document.getElementById('stats-search-input'),
      searchCount: document.getElementById('stats-search-count'),

      // Top 40
      top40List: document.getElementById('top40-list'),
      top40PeriodLabel: document.getElementById('top40-period-label'),
      top40CountLabel: document.getElementById('top40-count-label'),
      btnLoadMore: document.getElementById('btn-load-more'),

      // Reporte de olvido
      forgottenList: document.getElementById('forgotten-list'),
      forgottenCount: document.getElementById('forgotten-count'),
      btnExportForgotten: document.getElementById('btn-export-forgotten'),

      // Modal de auditoría
      auditModal: document.getElementById('audit-modal'),
      auditBackdrop: document.getElementById('audit-backdrop'),
      auditClose: document.getElementById('audit-modal-close'),
      auditTitle: document.getElementById('audit-modal-title'),
      auditArtist: document.getElementById('audit-modal-artist'),
      auditTypeBadge: document.getElementById('audit-type-badge'),
      auditCompliance: document.getElementById('audit-compliance-banner'),
      auditComplianceTxt: document.getElementById('audit-compliance-text'),
      complianceFill: document.getElementById('compliance-fill'),
      compliancePctLbl: document.getElementById('compliance-pct-label'),
      btnExportPdf: document.getElementById('btn-export-pdf'),

      // Reproductor
      audioEl: document.getElementById('audit-audio'),
      seekbar: document.getElementById('audio-seekbar'),
      seekbarProgress: document.getElementById('seekbar-progress'),
      currentTimeEl: document.getElementById('audio-current-time'),
      durationEl: document.getElementById('audio-duration'),
      btnPlay: document.getElementById('btn-play-pause'),
      btnRewind: document.getElementById('btn-rewind'),
      btnForward: document.getElementById('btn-forward'),
      btnMute: document.getElementById('btn-mute'),
      volSlider: document.getElementById('audio-volume'),
      iconPlay: document.getElementById('icon-play'),
      iconPause: document.getElementById('icon-pause'),
      iconVolOn: document.getElementById('icon-vol-on'),
      iconVolOff: document.getElementById('icon-vol-off'),
      waveformCanvas: document.getElementById('waveform-canvas'),
      waveformCursor: document.getElementById('waveform-cursor'),

      // Estadísticas rápidas del modal
      qsTotalPlays: document.getElementById('qs-total-plays'),
      qsLastPlay: document.getElementById('qs-last-play'),
      qsAvgDay: document.getElementById('qs-avg-day'),
      qsDuration: document.getElementById('qs-duration'),

      // Log de emisiones
      auditLogList: document.getElementById('audit-log-list'),
      auditLogTotal: document.getElementById('audit-log-total'),
      logDateFilter: document.getElementById('audit-log-date-filter'),
      btnClearDate: document.getElementById('btn-clear-date'),
    };
  }

  // ──────────────────────────────────────────────────────────
  //  UTILIDADES GENÉRICAS
  // ──────────────────────────────────────────────────────────

  /** Debounce sencillo: ejecuta fn sólo tras `wait` ms de silencio. */
  function debounce(fn, wait) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  /** Formatea segundos → "m:ss" */
  function formatTime(seconds) {
    if (!isFinite(seconds) || seconds < 0) return '0:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  /** Formatea una fecha ISO en texto legible (dd/mm/aaaa). */
  function formatDate(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    if (isNaN(d)) return isoString;
    return d.toLocaleDateString('es-AR', {
      day: '2-digit', month: '2-digit', year: 'numeric'
    });
  }

  /** Formatea hora desde una fecha ISO → "HH:MM:SS" */
  function formatTimeOfDay(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    if (isNaN(d)) return isoString;
    return d.toLocaleTimeString('es-AR', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    });
  }

  /** Escapa HTML para inserción segura como texto. */
  function escapeHTML(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /** Muestra/oculta un elemento usando el atributo `hidden`. */
  function toggleHidden(el, hidden) {
    if (!el) return;
    el.hidden = hidden;
  }

  /**
   * Fetch con gestión de AbortController.
   * Cancela cualquier solicitud previa antes de lanzar la nueva.
   */
  async function apiFetch(endpoint) {
    if (STATE.abortController) STATE.abortController.abort();
    STATE.abortController = new AbortController();
    const url = `${CONFIG.API_BASE}${endpoint}`;
    const res = await fetch(url, {
      signal: STATE.abortController.signal,
      headers: { 'Accept': 'application/json' },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} al acceder a ${url}`);
    return res.json();
  }

  // ──────────────────────────────────────────────────────────
  //  DATOS MOCK (sólo para desarrollo sin backend real)
  //  Eliminar cuando el backend FastAPI esté disponible.
  // ──────────────────────────────────────────────────────────

  function generateMockTop40(period) {
    const tipos = ['TEMA', 'TEMA', 'TEMA', 'PAUTA'];
    const items = [];
    for (let i = 1; i <= 40; i++) {
      const tipo = tipos[i % tipos.length];
      const baseToques = Math.max(1, Math.floor(100 / i) + Math.floor(Math.random() * 8));
      items.push({
        id: `item-${period}-${i}`,
        tipo,
        titulo: tipo === 'PAUTA'
          ? `Pauta Cliente ${String.fromCharCode(64 + (i % 10) + 1)} — Spot ${i}`
          : `Título del Tema ${i}`,
        artista: tipo === 'PAUTA' ? 'Pautante Comercial' : `Artista ${i}`,
        categoria: CONFIG.CATEGORIES[(i + 1) % CONFIG.CATEGORIES.length],
        toques: baseToques,
        duracion: tipo === 'PAUTA' ? 30 : 180 + Math.floor(Math.random() * 120),
        audio_url: `/media/audio/${period}-${i}.mp3`,
      });
    }
    return items;
  }

  function generateMockForgotten() {
    const tipos = ['TEMA', 'PAUTA', 'TEMA'];
    return Array.from({ length: 12 }, (_, i) => {
      const tipo = tipos[i % tipos.length];
      const dias = 30 + Math.floor(Math.random() * 90);
      const last = new Date(Date.now() - dias * 86400000).toISOString();
      return {
        id: `olvidado-${i + 1}`,
        tipo,
        titulo: tipo === 'PAUTA' ? `Pauta Inactiva ${i + 1}` : `Tema Olvidado ${i + 1}`,
        artista: tipo === 'PAUTA' ? 'Cliente Inactivo' : `Artista ${i + 1}`,
        dias_sin_sonar: dias,
        ultima_emision: last,
        audio_url: `/media/audio/forgotten-${i + 1}.mp3`,
      };
    });
  }

  function generateMockDetail(id) {
    const tipo = id.startsWith('olvidado') || Math.random() > 0.75 ? 'PAUTA' : 'TEMA';
    const totalEmisiones = tipo === 'PAUTA' ? Math.floor(Math.random() * 200 + 50) : Math.floor(Math.random() * 500 + 100);
    const emisiones = Array.from({ length: Math.min(totalEmisiones, 60) }, (_, i) => {
      const fecha = new Date(Date.now() - i * 8 * 3600000 - Math.random() * 3600000);
      return {
        id: `log-${i + 1}`,
        fecha_hora: fecha.toISOString(),
        canal: ['Principal', 'Secundario', 'Stream'][i % 3],
      };
    });
    const cumplimiento = tipo === 'PAUTA' ? Math.floor(Math.random() * 40 + 60) : null;

    return {
      id,
      tipo,
      titulo: tipo === 'PAUTA' ? `Pauta Cliente A — Spot ${id.split('-').pop()}` : `Tema ${id.split('-').pop()}`,
      artista: tipo === 'PAUTA' ? 'Pautante Comercial' : 'Artista del Tema',
      categoria: CONFIG.CATEGORIES[Math.floor(Math.random() * CONFIG.CATEGORIES.length)],
      duracion: tipo === 'PAUTA' ? 30 : 213,
      audio_url: `/media/audio/${id}.mp3`,
      total_toques: totalEmisiones,
      ultima_emision: emisiones[0]?.fecha_hora ?? null,
      promedio_dia: (totalEmisiones / 30).toFixed(1),
      cumplimiento_pct: cumplimiento,
      emisiones,
    };
  }

  // ──────────────────────────────────────────────────────────
  //  CARGA DE DATOS (con fallback a mock)
  // ──────────────────────────────────────────────────────────

  /** Carga el Top 40 para el período activo. */
  async function fetchTop40(period) {
    try {
      return await apiFetch(`/top40?period=${period}`);
    } catch (err) {
      if (err.name === 'AbortError') return null; // solicitud cancelada, ignorar
      console.warn('[EstadisticasModule] Backend no disponible — usando datos mock:', err.message);
      return generateMockTop40(period);
    }
  }

  /** Carga el reporte de olvido (no depende del período). */
  async function fetchForgotten() {
    try {
      return await apiFetch('/top40/forgotten');
    } catch (err) {
      if (err.name === 'AbortError') return null;
      return generateMockForgotten();
    }
  }

  /** Carga el detalle completo de un ítem para el modal de auditoría. */
  async function fetchDetail(id) {
    try {
      return await apiFetch(`/detalle/${encodeURIComponent(id)}`);
    } catch (err) {
      if (err.name === 'AbortError') return null;
      return generateMockDetail(id);
    }
  }

  // ──────────────────────────────────────────────────────────
  //  RENDERIZADO: TOP 40
  // ──────────────────────────────────────────────────────────

  /**
   * Renderiza las filas del Top 40 en el DOM.
   * Usa DocumentFragment para minimizar reflows.
   */
  function renderTop40(items) {
    const container = DOM.top40List;
    if (!container) return;

    if (!items || items.length === 0) {
      container.innerHTML = buildEmptyState('Sin datos para este período');
      container.setAttribute('aria-busy', 'false');
      return;
    }

    const maxToques = items[0]?.toques ?? 1; // para normalizar la barra de tendencia
    const fragment = document.createDocumentFragment();
    const visible = items.slice(0, STATE.top40Page * CONFIG.PAGE_SIZE);

    visible.forEach((item, idx) => {
      const row = buildTop40Row(item, idx + 1, maxToques);
      fragment.appendChild(row);
    });

    container.innerHTML = '';
    container.appendChild(fragment);
    container.setAttribute('aria-busy', 'false');

    // Actualizar contador y botón "cargar más"
    if (DOM.top40CountLabel) {
      DOM.top40CountLabel.textContent = `${visible.length} de ${items.length}`;
    }
    toggleHidden(DOM.btnLoadMore, visible.length >= items.length);
  }

  /** Construye un elemento <div> que representa una fila del Top 40. */
  function buildTop40Row(item, rank, maxToques) {
    const row = document.createElement('div');
    row.className = 'stats-row';
    row.setAttribute('role', 'row');
    row.setAttribute('tabindex', '0');
    row.setAttribute('aria-label', `${rank}. ${escapeHTML(item.titulo)} — ${item.toques} toques`);
    row.dataset.id = item.id;
    row.dataset.tipo = item.tipo;

    // Delay escalonado para animación de entrada (máx 600ms)
    row.style.setProperty('--row-delay', `${Math.min(rank * 18, 600)}ms`);

    const tipoBadge = buildTypeBadge(item.tipo);
    const trendPct = Math.round((item.toques / maxToques) * 100);
    const rankClass = rank <= 3 ? 'stats-row__rank--top3' : '';
    const rankDisplay = rank <= 3 ? ['🥇', '🥈', '🥉'][rank - 1] : `${rank}`;

    row.innerHTML = `
      <span class="stats-row__rank ${rankClass}" role="cell">${rankDisplay}</span>
      <span class="stats-row__type" role="cell">${tipoBadge}</span>
      <span class="stats-row__info" role="cell">
        <div class="stats-row__title">${escapeHTML(item.titulo)}</div>
        <div class="stats-row__artist">${escapeHTML(item.artista)}</div>
      </span>
      <span class="stats-row__category" role="cell">${escapeHTML(item.categoria)}</span>
      <span class="stats-row__plays" role="cell">${item.toques.toLocaleString('es-AR')}</span>
      <span class="stats-row__trend" role="cell">
        <span class="trend-bar" aria-label="${trendPct}% de tendencia">
          <span class="trend-bar__fill" style="width:${trendPct}%"></span>
        </span>
      </span>`;

    // Doble clic → abrir modal de auditoría
    attachDoubleClickHandler(row, item.id);
    // Enter / Space desde teclado
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') openAuditModal(item.id);
    });

    return row;
  }

  // ──────────────────────────────────────────────────────────
  //  RENDERIZADO: REPORTE DE OLVIDO
  // ──────────────────────────────────────────────────────────

  function renderForgotten(items) {
    const container = DOM.forgottenList;
    if (!container) return;

    if (!items || items.length === 0) {
      container.innerHTML = buildEmptyState('Sin ítems olvidados — ¡Todo al día!');
      container.setAttribute('aria-busy', 'false');
      if (DOM.forgottenCount) DOM.forgottenCount.textContent = '0';
      return;
    }

    if (DOM.forgottenCount) DOM.forgottenCount.textContent = items.length;

    const fragment = document.createDocumentFragment();
    items.forEach((item, idx) => {
      const row = buildForgottenRow(item, idx);
      fragment.appendChild(row);
    });

    container.innerHTML = '';
    container.appendChild(fragment);
    container.setAttribute('aria-busy', 'false');
  }

  function buildForgottenRow(item, idx) {
    const row = document.createElement('div');
    row.className = 'stats-row stats-row--forgotten';
    row.setAttribute('role', 'row');
    row.setAttribute('tabindex', '0');
    row.setAttribute('aria-label', `${escapeHTML(item.titulo)} — ${item.dias_sin_sonar} días sin sonar`);
    row.dataset.id = item.id;
    row.dataset.tipo = item.tipo;
    row.style.setProperty('--row-delay', `${idx * 40}ms`);

    const diasClass = item.dias_sin_sonar > 60 ? 'stats-row__days--danger' : 'stats-row__days--warn';

    row.innerHTML = `
      <span class="stats-row__type" role="cell">${buildTypeBadge(item.tipo)}</span>
      <span class="stats-row__info" role="cell">
        <div class="stats-row__title">${escapeHTML(item.titulo)}</div>
        <div class="stats-row__artist">${escapeHTML(item.artista)}</div>
      </span>
      <span class="stats-row__days ${diasClass}" role="cell">${item.dias_sin_sonar}d</span>
      <span class="stats-row__last" role="cell">${formatDate(item.ultima_emision)}</span>`;

    attachDoubleClickHandler(row, item.id);
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') openAuditModal(item.id);
    });

    return row;
  }

  // ──────────────────────────────────────────────────────────
  //  MODAL DE AUDITORÍA
  // ──────────────────────────────────────────────────────────

  /** Abre el modal cargando los datos del ítem con `id`. */
  async function openAuditModal(id) {
    if (!DOM.auditModal) return;

    STATE.currentItemId = id;
    resetPlayer();           // Detiene cualquier audio previo
    showModalLoading();      // Muestra skeletons mientras carga

    // Hacer visible el modal antes de la llamada para UX inmediata
    DOM.auditModal.setAttribute('aria-hidden', 'false');
    DOM.auditModal.removeAttribute('hidden');
    trapFocus(DOM.auditModal);

    // Cargar datos del detalle
    const data = await fetchDetail(id);
    if (!data) return; // solicitud cancelada

    STATE.currentItemData = data;
    STATE.logAll = data.emisiones ?? [];
    STATE.logFiltered = [...STATE.logAll];

    populateModalHeader(data);
    populateQuickStats(data);
    populateAuditLog(STATE.logFiltered);
    setupAudioSource(data.audio_url);
    renderWaveform(DOM.waveformCanvas);
    handlePautaCompliance(data);
  }

  /** Cierra el modal y limpia el estado relacionado. */
  function closeAuditModal() {
    if (!DOM.auditModal) return;
    DOM.auditModal.setAttribute('aria-hidden', 'true');
    resetPlayer();
    STATE.currentItemId = null;
    STATE.currentItemData = null;
    STATE.logAll = [];
    STATE.logFiltered = [];
    if (DOM.logDateFilter) DOM.logDateFilter.value = '';
    // Devolver el foco al elemento que originó la apertura
    const focusTarget = document.querySelector('.stats-row:focus') ??
      document.getElementById('stats-search-input');
    focusTarget?.focus();
  }

  function showModalLoading() {
    if (DOM.auditTitle) DOM.auditTitle.textContent = 'Cargando…';
    if (DOM.auditArtist) DOM.auditArtist.textContent = '';
    if (DOM.auditLogList) DOM.auditLogList.innerHTML = buildSkeletonRows(5, true);
    toggleHidden(DOM.auditCompliance, true);
  }

  function populateModalHeader(data) {
    if (DOM.auditTitle) DOM.auditTitle.textContent = data.titulo ?? '—';
    if (DOM.auditArtist) DOM.auditArtist.textContent = data.artista ?? '—';
    if (DOM.auditTypeBadge) {
      DOM.auditTypeBadge.textContent = data.tipo ?? 'TEMA';
      DOM.auditTypeBadge.dataset.type = data.tipo ?? 'TEMA';
      DOM.auditTypeBadge.setAttribute('aria-label', `Tipo: ${data.tipo ?? 'TEMA'}`);
    }
  }

  function populateQuickStats(data) {
    if (DOM.qsTotalPlays) DOM.qsTotalPlays.textContent = (data.total_toques ?? 0).toLocaleString('es-AR');
    if (DOM.qsLastPlay) DOM.qsLastPlay.textContent = formatDate(data.ultima_emision);
    if (DOM.qsAvgDay) DOM.qsAvgDay.textContent = `${data.promedio_dia ?? '0'}×`;
    if (DOM.qsDuration) DOM.qsDuration.textContent = formatTime(data.duracion ?? 0);
  }

  /**
   * Manejo especial para pautas.
   * Muestra el banner de cumplimiento y lo anima.
   */
  function handlePautaCompliance(data) {
    const esPauta = data.tipo === 'PAUTA';
    toggleHidden(DOM.auditCompliance, !esPauta);

    if (!esPauta) return;

    const pct = data.cumplimiento_pct ?? 0;
    if (DOM.auditComplianceTxt) {
      if (pct >= 90) {
        DOM.auditComplianceTxt.textContent = `Cumplimiento excelente: ${pct}% de las emisiones pautadas fueron emitidas.`;
      } else if (pct >= 70) {
        DOM.auditComplianceTxt.textContent = `Cumplimiento aceptable: ${pct}% — revisar franjas horarias faltantes.`;
      } else {
        DOM.auditComplianceTxt.textContent = `⚠ Cumplimiento bajo: ${pct}% — se requiere informe de incumplimiento al cliente.`;
      }
    }

    // Animar la barra con un pequeño delay para que la transición CSS sea visible
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (DOM.complianceFill) DOM.complianceFill.style.setProperty('--pct', `${pct}%`);
        if (DOM.compliancePctLbl) DOM.compliancePctLbl.textContent = `${pct}%`;
      });
    });
  }

  // ──────────────────────────────────────────────────────────
  //  LOG DE EMISIONES
  // ──────────────────────────────────────────────────────────

  function populateAuditLog(items) {
    const container = DOM.auditLogList;
    if (!container) return;

    if (!items || items.length === 0) {
      container.innerHTML = buildEmptyState('Sin emisiones registradas para este filtro');
      if (DOM.auditLogTotal) DOM.auditLogTotal.textContent = '0 registros';
      container.setAttribute('aria-busy', 'false');
      return;
    }

    if (DOM.auditLogTotal) DOM.auditLogTotal.textContent = `${items.length} registros`;

    const now = Date.now();
    const fragment = document.createDocumentFragment();

    items.forEach((entry, idx) => {
      const li = document.createElement('div');
      li.className = 'audit-log-item';
      li.setAttribute('role', 'listitem');

      // Marcar como "reciente" si fue hace menos de 24h
      const fechaMs = new Date(entry.fecha_hora).getTime();
      if (now - fechaMs < 86400000) li.classList.add('audit-log-item--recent');

      li.style.setProperty('--item-delay', `${Math.min(idx * 15, 400)}ms`);

      li.innerHTML = `
        <span class="audit-log-item__index">${String(idx + 1).padStart(3, '0')}</span>
        <span class="audit-log-item__dot" aria-hidden="true"></span>
        <span class="audit-log-item__datetime">
          <div class="audit-log-item__time">${formatTimeOfDay(entry.fecha_hora)}</div>
          <div class="audit-log-item__date">${formatDate(entry.fecha_hora)}</div>
        </span>
        <span class="audit-log-item__channel">${escapeHTML(entry.canal ?? '—')}</span>`;

      fragment.appendChild(li);
    });

    container.innerHTML = '';
    container.appendChild(fragment);
    container.setAttribute('aria-busy', 'false');
  }

  /** Filtra el historial por fecha seleccionada. */
  function filterLogByDate(dateStr) {
    if (!dateStr) {
      STATE.logFiltered = [...STATE.logAll];
    } else {
      STATE.logFiltered = STATE.logAll.filter((entry) => {
        const d = new Date(entry.fecha_hora);
        return d.toISOString().startsWith(dateStr);
      });
    }
    populateAuditLog(STATE.logFiltered);
  }

  // ──────────────────────────────────────────────────────────
  //  REPRODUCTOR DE AUDIO
  // ──────────────────────────────────────────────────────────

  function setupAudioSource(url) {
    if (!DOM.audioEl || !url) return;
    DOM.audioEl.src = url;
    DOM.audioEl.load();
  }

  function resetPlayer() {
    if (!DOM.audioEl) return;
    DOM.audioEl.pause();
    DOM.audioEl.src = '';
    STATE.playerPlaying = false;
    updatePlayPauseUI();
    if (DOM.seekbar) DOM.seekbar.value = 0;
    if (DOM.seekbarProgress) DOM.seekbarProgress.style.setProperty('--pct', '0%');
    if (DOM.currentTimeEl) DOM.currentTimeEl.textContent = '0:00';
    if (DOM.durationEl) DOM.durationEl.textContent = '0:00';
    if (DOM.waveformCursor) DOM.waveformCursor.style.setProperty('--pos', '0px');
    if (DOM.btnPlay) DOM.btnPlay.classList.remove('audit-player__play--playing');
  }

  function togglePlayPause() {
    if (!DOM.audioEl) return;
    if (STATE.playerPlaying) {
      DOM.audioEl.pause();
      STATE.playerPlaying = false;
      DOM.btnPlay?.classList.remove('audit-player__play--playing');
    } else {
      DOM.audioEl.play().catch((err) => {
        // El archivo puede no existir en entorno de desarrollo; silenciar el error
        console.warn('[Player] No se pudo reproducir el audio:', err.message);
      });
      STATE.playerPlaying = true;
      DOM.btnPlay?.classList.add('audit-player__play--playing');
    }
    updatePlayPauseUI();
  }

  function updatePlayPauseUI() {
    if (!DOM.iconPlay || !DOM.iconPause || !DOM.btnPlay) return;
    const playing = STATE.playerPlaying;
    DOM.iconPlay.style.display = playing ? 'none' : '';
    DOM.iconPause.style.display = playing ? '' : 'none';
    DOM.btnPlay.setAttribute('aria-label', playing ? 'Pausar' : 'Reproducir');
  }

  /** Actualiza la seekbar y el cursor del waveform en tiempo real. */
  function onAudioTimeUpdate() {
    if (!DOM.audioEl) return;
    const { currentTime, duration } = DOM.audioEl;
    if (!isFinite(duration) || duration === 0) return;

    const pct = (currentTime / duration) * 100;
    if (DOM.seekbar) DOM.seekbar.value = pct;
    if (DOM.seekbarProgress) DOM.seekbarProgress.style.setProperty('--pct', `${pct}%`);
    if (DOM.currentTimeEl) DOM.currentTimeEl.textContent = formatTime(currentTime);

    // Mover el cursor del waveform proporcionalmente
    if (DOM.waveformCanvas && DOM.waveformCursor) {
      const ww = DOM.waveformCanvas.offsetWidth;
      DOM.waveformCursor.style.setProperty('--pos', `${(pct / 100) * ww}px`);
    }
  }

  function onAudioLoaded() {
    if (!DOM.audioEl || !DOM.durationEl) return;
    DOM.durationEl.textContent = formatTime(DOM.audioEl.duration);
    renderWaveform(DOM.waveformCanvas); // Re-renderiza con la duración real
  }

  /** Busca la posición en el audio según el valor de la seekbar. */
  function onSeekbarInput() {
    if (!DOM.audioEl || !DOM.seekbar) return;
    const { duration } = DOM.audioEl;
    if (!isFinite(duration)) return;
    DOM.audioEl.currentTime = (DOM.seekbar.value / 100) * duration;
  }

  function skip(seconds) {
    if (!DOM.audioEl) return;
    const { currentTime, duration } = DOM.audioEl;
    DOM.audioEl.currentTime = Math.max(0, Math.min(duration, currentTime + seconds));
  }

  function toggleMute() {
    if (!DOM.audioEl) return;
    STATE.playerMuted = !STATE.playerMuted;
    DOM.audioEl.muted = STATE.playerMuted;
    if (DOM.iconVolOn) DOM.iconVolOn.style.display = STATE.playerMuted ? 'none' : '';
    if (DOM.iconVolOff) DOM.iconVolOff.style.display = STATE.playerMuted ? '' : 'none';
    if (DOM.btnMute) DOM.btnMute.setAttribute('aria-label', STATE.playerMuted ? 'Activar sonido' : 'Silenciar');
  }

  // ──────────────────────────────────────────────────────────
  //  WAVEFORM SIMULADO (Canvas 2D)
  // ──────────────────────────────────────────────────────────

  /**
   * Genera y dibuja un waveform pseudo-aleatorio pero visualmente coherente.
   * En producción, reemplazar con datos reales del servidor (amplitudes por segundo).
   */
  function renderWaveform(canvas) {
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const bars = CONFIG.WAVEFORM_BARS;
    const barW = (W / bars) * 0.6;
    const gap = (W / bars) * 0.4;
    const midY = H / 2;
    const accent = '#FF6600';
    const dim = '#1c2e44';

    // Generar datos del waveform si no existen aún
    if (!STATE.waveformData.length) {
      STATE.waveformData = Array.from({ length: bars }, (_, i) => {
        // Curva de energía: más alta en el centro, más baja en los extremos
        const envelope = Math.sin((i / bars) * Math.PI);
        const noise = 0.4 + Math.random() * 0.6;
        return envelope * noise;
      });
    }

    ctx.clearRect(0, 0, W, H);

    // Posición actual del audio para colorear el waveform
    const progress = DOM.audioEl
      ? (DOM.audioEl.currentTime / (DOM.audioEl.duration || 1))
      : 0;

    STATE.waveformData.forEach((amp, i) => {
      const x = i * (barW + gap) + gap / 2;
      const barH = Math.max(3, amp * (midY - 4));
      const past = i / bars < progress;

      // Barra superior
      ctx.fillStyle = past ? accent : dim;
      ctx.fillRect(Math.round(x), midY - barH, Math.ceil(barW), barH);

      // Reflejo inferior (más oscuro)
      ctx.fillStyle = past ? `rgba(255,102,0,0.3)` : `rgba(28,46,68,0.5)`;
      ctx.fillRect(Math.round(x), midY, Math.ceil(barW), barH * 0.4);
    });
  }

  // ──────────────────────────────────────────────────────────
  //  BÚSQUEDA INTERNA EN EL TOP 40
  // ──────────────────────────────────────────────────────────

  /**
   * Filtra STATE.top40All según el query y actualiza STATE.top40Filtered.
   * Busca en título y artista, insensible a mayúsculas y diacríticos.
   */
  function handleSearch(query) {
    const q = normalizeStr(query.trim());

    if (!q) {
      STATE.top40Filtered = [...STATE.top40All];
    } else {
      STATE.top40Filtered = STATE.top40All.filter((item) =>
        normalizeStr(item.titulo).includes(q) ||
        normalizeStr(item.artista).includes(q)
      );
    }

    STATE.top40Page = 1;
    renderTop40(STATE.top40Filtered);

    if (DOM.searchCount) {
      DOM.searchCount.textContent = q
        ? `${STATE.top40Filtered.length} resultado${STATE.top40Filtered.length !== 1 ? 's' : ''}`
        : '';
    }
  }

  /** Normaliza un string: minúsculas + sin diacríticos. */
  function normalizeStr(str) {
    return String(str ?? '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }

  // ──────────────────────────────────────────────────────────
  //  CAMBIO DE PERÍODO
  // ──────────────────────────────────────────────────────────

  const PERIOD_LABELS = {
    day: 'Hoy',
    week: 'Esta semana',
    month: 'Este mes',
  };

  /**
   * Cambia el período activo, actualiza el indicador visual
   * y recarga el Top 40 con los nuevos datos.
   */
  async function changePeriod(newPeriod) {
    if (newPeriod === STATE.period) return;
    STATE.period = newPeriod;

    // Actualizar botones
    DOM.periodBtns?.forEach((btn, idx) => {
      const isActive = btn.dataset.period === newPeriod;
      btn.classList.toggle('stats-period-btn--active', isActive);
      btn.setAttribute('aria-pressed', String(isActive));

      // Mover el slider al botón activo
      if (isActive && DOM.periodSlider) {
        DOM.periodSlider.style.transform = `translateX(${idx * 100}%)`;
      }
    });

    // Etiqueta del panel
    if (DOM.top40PeriodLabel) {
      DOM.top40PeriodLabel.textContent = PERIOD_LABELS[newPeriod] ?? newPeriod;
    }

    // Mostrar skeleton durante la carga
    if (DOM.top40List) {
      DOM.top40List.innerHTML = buildSkeletonRows(8, false);
      DOM.top40List.setAttribute('aria-busy', 'true');
    }

    const data = await fetchTop40(newPeriod);
    if (!data) return;

    STATE.top40All = data;
    STATE.top40Filtered = [...data];
    STATE.top40Page = 1;

    // Limpiar la búsqueda al cambiar de período
    if (DOM.searchInput) DOM.searchInput.value = '';
    if (DOM.searchCount) DOM.searchCount.textContent = '';

    renderTop40(STATE.top40Filtered);
  }

  // ──────────────────────────────────────────────────────────
  //  DOBLE CLIC CON TIMER MANUAL
  // ──────────────────────────────────────────────────────────

  /**
   * Adjunta el manejador de doble clic a una fila.
   * Se usa un timer manual porque `dblclick` nativo tiene latencia
   * perceptible en algunos navegadores y además necesitamos ignorar
   * el primer clic sencillo (sin selección de texto).
   */
  function attachDoubleClickHandler(row, id) {
    row.addEventListener('click', (e) => {
      e.preventDefault();
      if (STATE.clickTimer) {
        // Segundo clic dentro de la ventana → doble clic confirmado
        clearTimeout(STATE.clickTimer);
        STATE.clickTimer = null;
        openAuditModal(id);
      } else {
        // Primer clic: esperar para ver si hay un segundo
        STATE.clickTimer = setTimeout(() => {
          STATE.clickTimer = null;
          // Clic sencillo: sólo resaltar visualmente (opcional)
          document.querySelectorAll('.stats-row--selected')
            .forEach(r => r.classList.remove('stats-row--selected'));
          row.classList.add('stats-row--selected');
        }, CONFIG.DOUBLE_CLICK_MS);
      }
    });
  }

  // ──────────────────────────────────────────────────────────
  //  TRAMPA DE FOCO PARA ACCESIBILIDAD DEL MODAL
  // ──────────────────────────────────────────────────────────

  function trapFocus(modal) {
    const focusables = modal.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusables.length) return;

    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    first.focus();

    modal.addEventListener('keydown', function onKey(e) {
      if (e.key === 'Tab') {
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
      if (e.key === 'Escape') {
        closeAuditModal();
        modal.removeEventListener('keydown', onKey);
      }
    }, { capture: true });
  }

  // ──────────────────────────────────────────────────────────
  //  EXPORTAR PDF (stub — integrable con jsPDF o backend)
  // ──────────────────────────────────────────────────────────

  function exportAuditPDF() {
    const data = STATE.currentItemData;
    if (!data) return;

    // Si existe un endpoint del backend, usarlo directamente:
    // window.open(`${CONFIG.API_BASE}/certificado/${data.id}.pdf`, '_blank');

    // Fallback stub: genera una ventana de impresión formateada
    const ventana = window.open('', '_blank', 'width=800,height=600');
    if (!ventana) return;

    const rows = (data.emisiones ?? []).map((e, i) => `
      <tr>
        <td>${i + 1}</td>
        <td>${formatDate(e.fecha_hora)}</td>
        <td>${formatTimeOfDay(e.fecha_hora)}</td>
        <td>${escapeHTML(e.canal ?? '—')}</td>
      </tr>`).join('');

    ventana.document.write(`
      <!DOCTYPE html>
      <html lang="es">
      <head>
        <meta charset="UTF-8"/>
        <title>Certificado de Auditoría — ${escapeHTML(data.titulo)}</title>
        <style>
          body { font-family: Arial, sans-serif; padding: 40px; color: #111; }
          h1 { font-size: 20px; margin-bottom: 4px; }
          .subtitle { color: #555; font-size: 13px; margin-bottom: 24px; }
          table { width: 100%; border-collapse: collapse; font-size: 13px; }
          th { background: #111; color: #fff; padding: 8px 10px; text-align: left; }
          td { padding: 7px 10px; border-bottom: 1px solid #ddd; }
          tr:nth-child(even) td { background: #f5f5f5; }
          .meta { display: flex; gap: 40px; margin-bottom: 24px; font-size: 13px; }
          .meta span { color: #555; }
          .meta strong { color: #111; display: block; font-size: 15px; }
          @media print { body { padding: 0; } }
        </style>
      </head>
      <body>
        <h1>Certificado de Auditoría de Emisión</h1>
        <p class="subtitle">Sistema de Automatización de Radio — Módulo de Estadísticas</p>
        <div class="meta">
          <div><span>Título</span><strong>${escapeHTML(data.titulo)}</strong></div>
          <div><span>Artista / Cliente</span><strong>${escapeHTML(data.artista)}</strong></div>
          <div><span>Tipo</span><strong>${escapeHTML(data.tipo)}</strong></div>
          <div><span>Total de emisiones</span><strong>${data.total_toques}</strong></div>
          ${data.cumplimiento_pct != null
        ? `<div><span>Cumplimiento</span><strong>${data.cumplimiento_pct}%</strong></div>`
        : ''}
        </div>
        <table>
          <thead>
            <tr><th>#</th><th>Fecha</th><th>Hora</th><th>Canal</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
        <script>window.print();<\/script>
      </body>
      </html>`);
    ventana.document.close();
  }

  // ──────────────────────────────────────────────────────────
  //  EXPORTAR LISTA DE OLVIDO
  // ──────────────────────────────────────────────────────────

  function exportForgottenCSV() {
    const rows = STATE.forgottenAll.map((item) =>
      [
        `"${item.tipo}"`,
        `"${item.titulo.replace(/"/g, '""')}"`,
        `"${item.artista.replace(/"/g, '""')}"`,
        item.dias_sin_sonar,
        `"${formatDate(item.ultima_emision)}"`,
      ].join(',')
    );
    const csv = ['Tipo,Titulo,Artista,Dias Sin Sonar,Ultima Emision', ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `reporte-olvido-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  // ──────────────────────────────────────────────────────────
  //  HELPERS DE CONSTRUCCIÓN HTML
  // ──────────────────────────────────────────────────────────

  function buildTypeBadge(tipo) {
    const cls = tipo === 'PAUTA' ? 'badge-type--pauta' : 'badge-type--tema';
    return `<span class="badge-type ${cls}">${escapeHTML(tipo)}</span>`;
  }

  function buildEmptyState(msg) {
    return `
      <div class="stats-empty-state" role="status">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5"/>
          <path d="M12 7v5M12 15v1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <p>${escapeHTML(msg)}</p>
      </div>`;
  }

  function buildSkeletonRows(count, slim) {
    const cls = slim ? 'stats-skeleton__row--slim' : '';
    const inner = Array.from({ length: count }, (_, i) =>
      `<div class="stats-skeleton__row ${cls}" style="--delay:${i * 60}ms"></div>`
    ).join('');
    return `<div class="stats-skeleton" aria-hidden="true">${inner}</div>`;
  }

  // ──────────────────────────────────────────────────────────
  //  REGISTRO DE EVENT LISTENERS
  // ──────────────────────────────────────────────────────────

  function bindEvents() {
    // Filtros de período
    DOM.periodBtns?.forEach((btn) => {
      btn.addEventListener('click', () => changePeriod(btn.dataset.period));
    });

    // Búsqueda en Top 40
    if (DOM.searchInput) {
      DOM.searchInput.addEventListener(
        'input',
        debounce((e) => handleSearch(e.target.value), CONFIG.DEBOUNCE_SEARCH)
      );
    }

    // Cargar más en el Top 40
    DOM.btnLoadMore?.addEventListener('click', () => {
      STATE.top40Page++;
      renderTop40(STATE.top40Filtered);
    });

    // Exportar reporte de olvido
    DOM.btnExportForgotten?.addEventListener('click', exportForgottenCSV);

    // Modal: cerrar con el botón X
    DOM.auditClose?.addEventListener('click', closeAuditModal);

    // Modal: cerrar al hacer clic en el backdrop
    DOM.auditBackdrop?.addEventListener('click', closeAuditModal);

    // Reproductor: play/pause
    DOM.btnPlay?.addEventListener('click', togglePlayPause);

    // Reproductor: rewind / forward
    DOM.btnRewind?.addEventListener('click', () => skip(-CONFIG.SEEK_SKIP_SEC));
    DOM.btnForward?.addEventListener('click', () => skip(CONFIG.SEEK_SKIP_SEC));

    // Reproductor: mute
    DOM.btnMute?.addEventListener('click', toggleMute);

    // Reproductor: seekbar (arrastre)
    DOM.seekbar?.addEventListener('input', onSeekbarInput);

    // Reproductor: volumen
    DOM.volSlider?.addEventListener('input', (e) => {
      if (DOM.audioEl) DOM.audioEl.volume = parseFloat(e.target.value);
    });

    // Reproductor: eventos del elemento <audio>
    if (DOM.audioEl) {
      DOM.audioEl.addEventListener('timeupdate', () => {
        onAudioTimeUpdate();
        // Re-dibujar el waveform en cada actualización para mostrar el progreso
        renderWaveform(DOM.waveformCanvas);
      });
      DOM.audioEl.addEventListener('loadedmetadata', onAudioLoaded);
      DOM.audioEl.addEventListener('ended', () => {
        STATE.playerPlaying = false;
        updatePlayPauseUI();
        DOM.btnPlay?.classList.remove('audit-player__play--playing');
      });
    }

    // Log: filtro por fecha
    DOM.logDateFilter?.addEventListener('change', (e) => {
      filterLogByDate(e.target.value);
    });
    DOM.btnClearDate?.addEventListener('click', () => {
      if (DOM.logDateFilter) DOM.logDateFilter.value = '';
      filterLogByDate('');
    });

    // Exportar PDF
    DOM.btnExportPdf?.addEventListener('click', exportAuditPDF);

    // Waveform: clic para posicionar
    DOM.waveformCanvas?.parentElement?.addEventListener('click', (e) => {
      if (!DOM.audioEl || !isFinite(DOM.audioEl.duration)) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const ratio = (e.clientX - rect.left) / rect.width;
      DOM.audioEl.currentTime = ratio * DOM.audioEl.duration;
    });
  }

  // ──────────────────────────────────────────────────────────
  //  INICIALIZACIÓN PRINCIPAL
  // ──────────────────────────────────────────────────────────

  /**
   * init() es llamado por admin-app.js tras inyectar el fragmento HTML.
   * Debe ser idempotente: si se llama dos veces, el módulo se reinicia.
   */
  async function init() {
    resolveDOM();
    bindEvents();

    // Posicionar el slider en el período inicial (HOY → índice 0)
    if (DOM.periodSlider) {
      DOM.periodSlider.style.transform = 'translateX(0%)';
    }

    // Carga inicial: Top 40 y Reporte de Olvido en paralelo
    const [top40Data, forgottenData] = await Promise.all([
      fetchTop40(STATE.period),
      fetchForgotten(),
    ]);

    if (top40Data) {
      STATE.top40All = top40Data;
      STATE.top40Filtered = [...top40Data];
      renderTop40(STATE.top40Filtered);
    }

    if (forgottenData) {
      STATE.forgottenAll = forgottenData;
      renderForgotten(forgottenData);
    }

    // Etiqueta inicial del período
    if (DOM.top40PeriodLabel) {
      DOM.top40PeriodLabel.textContent = PERIOD_LABELS[STATE.period];
    }
  }

  /** Limpia los recursos del módulo (listeners, audio, timers). */
  function destroy() {
    if (STATE.clickTimer) clearTimeout(STATE.clickTimer);
    if (STATE.abortController) STATE.abortController.abort();
    resetPlayer();
  }

  // ──────────────────────────────────────────────────────────
  //  EXPOSICIÓN PÚBLICA PARA admin-app.js
  // ──────────────────────────────────────────────────────────

  global.EstadisticasModule = {
    /** Llama a init() después de inyectar el HTML en el DOM. */
    init,
    /** Llama a destroy() antes de desmontar la sección. */
    destroy,
    /** Acceso de sólo lectura al estado (para depuración / integración). */
    getState: () => ({ ...STATE }),
  };

})(window);
