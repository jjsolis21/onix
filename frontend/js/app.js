/**
 * app.js — Ónix FM Digital  v3.0  ·  Studio Sync Module
 * ============================================================
 * Módulo de sincronización en tiempo real para la interfaz del
 * operador (Studio). Se conecta vía WebSocket al api_server.py
 * y mantiene la UI actualizada sin necesidad de recargar la página.
 *
 * ARQUITECTURA:
 *   WebSocket(/ws)  ←→  api_server.py  ←→  audio_engine.py
 *                              ↓
 *                         app.js (este archivo)
 *                              ↓
 *                     Studio UI (index.html)
 *
 * EVENTOS QUE ESCUCHA:
 *   track_started    → Actualiza strip "Al aire" + barra de progreso
 *   track_ended      → Limpia la UI del track activo
 *   crossfade_start  → Muestra indicador de transición
 *   mode_changed     → Refleja cambio Manual/Automático en la UI
 *   effect_fired     → Feedback visual del jingle/efecto
 *   engine_stopped   → Estado de pausa total
 *   playback_error   → Muestra error con detalles
 *   watchdog_recovery→ Alerta de recuperación automática
 *   library_updated  → Recarga la biblioteca de tracks
 *   stats_updated    → Refresca contadores de inventario
 *   bloques_updated  → Recarga la pauta/bloques horarios
 *
 * COMANDOS QUE ENVÍA:
 *   play        { cmd:"play",       audio_id }
 *   queue       { cmd:"queue",      audio_id }
 *   fire_effect { cmd:"fire_effect", audio_id, canal_offset }
 *   set_mode    { cmd:"set_mode",   mode:"manual"|"automatico" }
 *   lanzar_tanda{ cmd:"lanzar_tanda", ahora }   (modo manual: el operador lanza la tanda)
 *   cue         { cmd:"cue", audio_id }         (preescucha por la salida interna)
 *   cue_stop    { cmd:"cue_stop" }
 *   ping        { cmd:"ping" }                 (keep-alive cada 25s)
 *
 * USO:
 *   En index.html:
 *     <script src="app.js"></script>
 *   El módulo se inicializa solo (IIFE) y expone StudioApp al global.
 */

"use strict";

/* ============================================================
   CONFIGURACIÓN
   ============================================================ */
const STUDIO_CONFIG = {
  wsUrl:         `ws://${location.host}/ws`,
  apiBase:       "",                    // mismo host; ajustar si hay proxy
  reconnectBase: 1500,                  // ms de espera inicial para reconexión
  reconnectMax:  20000,                 // ms máximo entre intentos
  pingInterval:  25000,                 // keep-alive WebSocket
  libReloadDelay: 400,                  // ms tras library_updated antes de recargar
};

/* ============================================================
   UTILIDADES GLOBALES
   ============================================================ */

/** Alias rápido para querySelector */
const $s  = (sel, ctx = document) => ctx.querySelector(sel);
/** Alias para querySelectorAll → Array */
const $$s = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

/** Escapa HTML para inserción segura */
function escHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Formatea segundos → m:ss */
function fmtSec(sec) {
  if (!sec || isNaN(sec)) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

/** Mayúsculas sin tildes, para comparar "Dúo" con "DUO" */
function normTxt(s) {
  return String(s ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().trim();
}

/** Anti-rebote: ejecuta fn sólo si no la llamaron de nuevo en `ms` ms */
function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

/**
 * Sistema de notificaciones toast.
 * Busca un contenedor #studioToastContainer; si no existe, lo crea.
 * @param {string} msg    Texto a mostrar
 * @param {"info"|"success"|"warning"|"error"} type
 * @param {number} ms     Milisegundos antes de desaparecer
 */
function studioToast(msg, type = "info", ms = 3500) {
  let container = $s("#studioToastContainer");
  if (!container) {
    container = document.createElement("div");
    container.id = "studioToastContainer";
    // Estilos inline para no depender de una hoja externa
    Object.assign(container.style, {
      position: "fixed", bottom: "20px", right: "20px",
      display: "flex", flexDirection: "column-reverse", gap: "8px",
      zIndex: "9999", pointerEvents: "none",
    });
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  el.className = `studio-toast studio-toast--${type}`;
  el.textContent = msg;
  // Estilos base del toast (también se pueden mover a CSS)
  const colorMap = { info: "#0a9396", success: "#52b788", warning: "#ee9b00", error: "#ae2012" };
  Object.assign(el.style, {
    background: "#001e2b", border: `1px solid ${colorMap[type] || colorMap.info}`,
    color: "#e9f5f3", fontFamily: "monospace", fontSize: "13px",
    padding: "8px 14px", borderRadius: "4px",
    boxShadow: `0 0 10px rgba(0,0,0,.5)`,
    animation: "studioSlideIn .2s ease",
    pointerEvents: "auto",
  });
  container.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// Inyectar la keyframe de animación una sola vez
(function injectToastStyle() {
  if ($s("#studioToastStyle")) return;
  const style = document.createElement("style");
  style.id = "studioToastStyle";
  style.textContent = `
    @keyframes studioSlideIn {
      from { transform: translateX(40px); opacity: 0; }
      to   { transform: translateX(0);    opacity: 1; }
    }
  `;
  document.head.appendChild(style);
})();


/* ============================================================
   MÓDULO: WEBSOCKET MANAGER
   Gestiona conexión, reconexión exponencial y despacho de eventos.
   ============================================================ */
const StudioWS = (() => {
  let ws = null;
  let retryDelay = STUDIO_CONFIG.reconnectBase;
  let pingTimer  = null;
  const handlers = {};    // { event: [fn, fn, ...] }

  /** Abre la conexión WebSocket y configura callbacks */
  function connect() {
    ws = new WebSocket(STUDIO_CONFIG.wsUrl);

    ws.onopen = () => {
      retryDelay = STUDIO_CONFIG.reconnectBase;  // resetear backoff
      _setIndicator("connected");
      studioToast("Studio conectado al servidor", "success", 2000);
      _startPing();
    };

    ws.onmessage = ({ data }) => {
      try {
        const msg = JSON.parse(data);
        // El servidor envía { event, data }; los mensajes reemitidos por
        // otros módulos del Admin solo traen { cmd, data }.
        const event = msg.event ?? msg.cmd;
        const payload = msg.data;
        // Disparar handlers específicos del evento
        (handlers[event] ?? []).forEach(fn => fn(payload));
        // Disparar handlers "catch-all" registrados con onEvent("*", fn)
        (handlers["*"]  ?? []).forEach(fn => fn(event, payload));
      } catch (err) {
        console.warn("[StudioWS] Error al parsear mensaje:", err);
      }
    };

    ws.onclose = () => {
      _setIndicator("disconnected");
      _stopPing();
      // Reconexión con backoff exponencial
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, STUDIO_CONFIG.reconnectMax);
    };

    ws.onerror = () => {
      _setIndicator("error");
      studioToast("Conexión con el servidor perdida. Reconectando…", "error");
    };
  }

  /** Envía un comando al servidor vía WebSocket */
  function send(payload) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
      return true;
    }
    studioToast("Sin conexión con el servidor", "warning");
    return false;
  }

  /**
   * Registra un listener para un evento WebSocket.
   * Usa "*" como evento para capturar todos los mensajes.
   * @param {string}   event  Nombre del evento o "*"
   * @param {Function} fn     Callback(payload) o callback(event, payload) para "*"
   */
  function onEvent(event, fn) {
    if (!handlers[event]) handlers[event] = [];
    handlers[event].push(fn);
  }

  function _setIndicator(state) {
    // Buscar el indicador visual por id o clase; es un punto/ícono en la UI
    const el = $s("#studioWsStatus") ?? $s(".ws-status-dot");
    if (!el) return;
    el.className = el.className.replace(/\b(connected|disconnected|error)\b/g, "").trim();
    el.classList.add(state);
    // Si tiene un atributo title, actualizarlo para accesibilidad
    el.title = { connected: "Conectado", disconnected: "Desconectado", error: "Error" }[state] ?? state;
  }

  function _startPing() {
    _stopPing();
    pingTimer = setInterval(() => send({ cmd: "ping" }), STUDIO_CONFIG.pingInterval);
  }

  function _stopPing() {
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
  }

  // Iniciar conexión al cargar el módulo
  connect();

  return { send, onEvent };
})();


/* ============================================================
   MÓDULO: NOW PLAYING
   Controla el strip "Al aire" con título, artista, barra de progreso.
   ============================================================ */
const NowPlaying = (() => {
  let _progressTimer = null;
  let _startedAt     = null;   // timestamp local cuando empezó el track
  let _duration      = 0;
  let _audioId       = null;

  /** Actualiza el strip con los datos del track que acaba de empezar */
  function setTrack(data) {
    _audioId  = data.audio_id;
    _duration = data.duracion_seg ?? 0;
    // La pista puede arrancar adelantada (cue_inicio): el reloj parte de ahí
    _startedAt = Date.now() - (data.posicion_seg ?? 0) * 1000;

    // Campos de texto en la UI
    _setText("#studioNowTitle",  escHtml(data.titulo));
    _setText("#studioNowArtist", escHtml(data.cliente ?? data.artista));
    _setText("#badgeGenero",     escHtml(data.genero_vocal || "—"));
    _setText("#timeRemaining",   fmtSec(_duration));
    _setOnAir(true);
    _setText("#studioNowGenre",  data.genero_vocal ?? "");
    _setText("#studioNowType",   data.tipo_audio ?? "");
    _setText("#studioNowDur",    fmtSec(_duration));

    // Resaltar fila activa en la tabla de biblioteca si existe
    $$s(".row-now").forEach(r => r.classList.remove("row-now"));
    document.querySelector(`[data-audio-id="${_audioId}"]`)?.classList.add("row-now");

    // Activar indicador parpadeante
    const dot = $s("#studioLiveDot");
    if (dot) dot.style.opacity = "1";

    // Iniciar barra de progreso
    _startProgress();
  }

  /** Limpia el strip cuando el motor se detiene */
  function clearTrack() {
    _stopProgress();
    _audioId = null;
    _setText("#studioNowTitle", "Sin reproducción activa");
    _setText("#studioNowArtist", "—");
    _setText("#badgeGenero", "—");
    ["#timeElapsed", "#timeRemaining", "#timeRemainingBar"].forEach(s => _setText(s, "0:00"));
    const fill = $s("#progressFill");
    if (fill) fill.style.width = "0%";
    _setOnAir(false);
    _setText("#studioNowGenre", "");
    _setText("#studioNowType",  "");
    _setText("#studioNowDur",   "");
    const bar = $s("#studioProgressBar");
    if (bar) bar.style.width = "0%";
    const dot = $s("#studioLiveDot");
    if (dot) dot.style.opacity = "0";
    $$s(".row-now").forEach(r => r.classList.remove("row-now"));
  }

  function _startProgress() {
    _stopProgress();
    if (!_duration) return;
    _progressTimer = setInterval(() => {
      const elapsed = (Date.now() - _startedAt) / 1000;
      const pct = Math.min((elapsed / _duration) * 100, 100);
      const bar = $s("#progressFill") ?? $s("#studioProgressBar");
      if (bar) bar.style.width = `${pct.toFixed(1)}%`;
      // Actualizar tiempo transcurrido / restante
      const remaining = Math.max(_duration - elapsed, 0);
      _setText("#studioNowElapsed", fmtSec(elapsed));
      _setText("#timeElapsed",      fmtSec(elapsed));
      _setText("#timeRemaining",    fmtSec(remaining));
      _setText("#timeRemainingBar", fmtSec(remaining));
      if (pct >= 100) _stopProgress();
    }, 1000);
  }

  function _stopProgress() {
    if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
  }

  function _setText(sel, html) {
    const el = $s(sel);
    if (el) el.innerHTML = html;
  }

  function _setOnAir(on) {
    const badge = $s("#onAirBadge");
    if (!badge) return;
    badge.classList.toggle("active", on);
    const txt = badge.querySelector(".on-air-text");
    if (txt) txt.textContent = on ? "ON AIR" : "OFF AIR";
  }

  /** Sincroniza el strip con /api/v1/status (al cargar la página a mitad de pista) */
  function syncFromStatus(st) {
    const t = st?.current_track;
    if (!t) { clearTrack(); return; }
    setTrack({
      audio_id: t.id, titulo: t.titulo, artista: t.artista, cliente: t.cliente,
      duracion_seg: st.duration_sec, posicion_seg: st.position_sec,
    });
  }

  return { setTrack, clearTrack, syncFromStatus };
})();


/* ============================================================
   MÓDULO: LIBRARY
   Carga y mantiene la lista de tracks disponibles en el Studio.
   Permite reproducir, encolar y escuchar preescucha sin salir.
   ============================================================ */
const StudioLibrary = (() => {
  let _tracks   = [];
  let _filtered = [];
  let _cueId    = null;                                   // audio en preescucha
  const _f = { origen: "", genero: "", energia: "" };     // filtros de MÚSICA

  /** Carga todos los tracks activos desde la API */
  async function load(force = false) {
    try {
      const params = new URLSearchParams({ limit: "3000" });
      const res = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/audios?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      // La API responde { total, limit, offset, data: [...] } con los nombres
      // de la Biblioteca (duracion, voz); se adaptan a los que usa esta tabla.
      _tracks = (body.data ?? []).map(a => ({
        ...a, duracion_seg: a.duracion, genero_vocal: a.voz,
      }));
      // Aplicar filtros actuales al nuevo set
      applyFilters();
      _fillArtistas();
    } catch (err) {
      console.error("[StudioLibrary] Error al cargar biblioteca:", err);
      studioToast("Error al cargar la biblioteca", "error");
    }
  }

  /** Filtra por el texto del buscador y reparte los audios por pestaña */
  function applyFilters() {
    const q = normTxt(($s("#studioLibSearch") ?? $s("#searchInput"))?.value);
    const match = t => !q ||
      normTxt(t.titulo).includes(q)  ||
      normTxt(t.artista).includes(q) ||
      normTxt(t.genero_vocal).includes(q);
    // Filtros de la pestaña MÚSICA (origen, voz, energía)
    const pasaFiltros = t =>
      (!_f.origen  || t.origen === _f.origen) &&
      (!_f.genero  || normTxt(t.genero_vocal) === _f.genero) &&
      (!_f.energia || String(t.energia ?? "") === _f.energia);

    // MÚSICA muestra todo lo que no es cuña ni efecto (música, sweepers, programas…)
    _filtered     = _tracks.filter(t => !["Cuña", "Efecto"].includes(t.tipo_audio) && match(t) && pasaFiltros(t));
    const ads     = _tracks.filter(t => t.tipo_audio === "Cuña"   && match(t));
    const jingles = _tracks.filter(t => t.tipo_audio === "Efecto" && match(t));

    render();
    renderSimple("#adList", ads, "publicidad", "Sin cuñas en la biblioteca");
    renderSimple("#jingleList", jingles, "jingles", "Sin jingles en la biblioteca");

    const counter = $s("#studioLibCount") ?? $s("#searchCount");
    if (counter) counter.textContent = `${_filtered.length + ads.length + jingles.length} / ${_tracks.length}`;
  }

  const _prevBtn = t => `<button class="lib-btn-prev${t.id === _cueId ? " previewing" : ""}"
          data-prev-id="${t.id}" title="Preescuchar por la salida interna (no sale al aire)">${t.id === _cueId ? "■" : "🎧"}</button>`;

  const _actions = t => `
          <button class="lib-btn-play"  data-play-id="${t.id}"  title="Reproducir ahora">▶ AIRE</button>
          <button class="lib-btn-queue" data-queue-id="${t.id}" title="Añadir a cola">+ COLA</button>`;

  /** Renderiza la tabla de la pestaña MÚSICA */
  function render() {
    const tbody = $s("#studioLibTableBody");
    if (!tbody) return;

    if (!_filtered.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;opacity:.5;padding:20px">Sin resultados</td></tr>`;
      return;
    }

    tbody.innerHTML = _filtered.map(t => `
      <tr data-audio-id="${t.id}" class="${t.id === _getNowId() ? "row-now" : ""}">
        <td class="lib-col-title">
          <div class="lib-title">${escHtml(t.titulo)}</div>
          <div class="lib-artist">${escHtml(t.artista)}</div>
        </td>
        <td class="lib-col-dur">${fmtSec(t.duracion_seg)}</td>
        <td class="lib-col-meta">${escHtml(t.genero_vocal ?? "")}</td>
        <td class="lib-col-meta">${escHtml(t.cat1 ?? "")}</td>
        <td class="lib-col-prev">${_prevBtn(t)}</td>
        <td class="lib-col-actions">${_actions(t)}</td>
      </tr>
    `).join("");
  }

  /** Renderiza las pestañas PUBLICIDAD y JINGLES (3 columnas) */
  function renderSimple(sel, list, banco, emptyMsg) {
    const tbody = $s(sel);
    if (!tbody) return;

    if (!list.length) {
      tbody.innerHTML = `<tr><td colspan="3" style="text-align:center;opacity:.5;padding:20px">${emptyMsg}</td></tr>`;
      return;
    }

    tbody.innerHTML = list.map(t => `
      <tr data-audio-id="${t.id}">
        <td class="lib-col-title">
          <div class="lib-title">${escHtml(t.titulo)}</div>
          <div class="lib-artist">${escHtml(t.artista)}</div>
        </td>
        <td class="lib-col-dur">${fmtSec(t.duracion_seg)}</td>
        <td class="lib-col-actions">
          ${_prevBtn(t)}${_actions(t)}
          <button class="lib-btn-queue" data-cart-id="${t.id}" data-cart-banco="${banco}" title="Asignar al primer botón libre de la cartuchera">→ CART</button>
        </td>
      </tr>
    `).join("");
  }

  // Un solo listener para las tres tablas (delegación de eventos)
  $s(".hub-content")?.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    if (btn.dataset.playId) {
      StudioWS.send({ cmd: "play", audio_id: parseInt(btn.dataset.playId) });
    } else if (btn.dataset.queueId) {
      const id = parseInt(btn.dataset.queueId);
      StudioWS.send({ cmd: "queue", audio_id: id });
      studioToast(`+ Cola: ${_tracks.find(x => x.id === id)?.titulo ?? id}`, "info");
    } else if (btn.dataset.prevId) {
      togglePreview(parseInt(btn.dataset.prevId));
    } else if (btn.dataset.cartId) {
      Cartuchera.assign(btn.dataset.cartBanco, parseInt(btn.dataset.cartId));
    }
  });

  // Pestañas MÚSICA / PUBLICIDAD / JINGLES / INGESTA
  $$s(".hub-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      $$s(".hub-tab").forEach(t => t.classList.toggle("active", t === tab));
      $$s(".tab-panel").forEach(p => p.classList.toggle("active", p.id === `tab-${tab.dataset.tab}`));
    });
  });

  /** Obtiene el ID del track actualmente al aire (desde el strip) */
  function _getNowId() {
    return parseInt($s(".row-now")?.dataset?.audioId ?? "0") || null;
  }

  /**
   * Preescucha: la hace el MOTOR por la tarjeta de preescucha (Admin → 06
   * Motor), así el operador escucha sin salir al aire. El estado vuelve por
   * los eventos cue_started / cue_stopped.
   */
  function togglePreview(id) {
    StudioWS.send(_cueId === id ? { cmd: "cue_stop" } : { cmd: "cue", audio_id: id });
  }

  /** Marca qué botón está preescuchando (llamado desde los eventos del motor) */
  function setCue(id) {
    _cueId = id;
    $$s(".lib-btn-prev").forEach(btn => {
      const on = parseInt(btn.dataset.prevId) === id;
      btn.classList.toggle("previewing", on);
      btn.textContent = on ? "■" : "🎧";
    });
  }

  function _fillArtistas() {
    const dl = $s("#artistasList");
    if (!dl) return;
    const artistas = [...new Set(_tracks.map(t => t.artista).filter(Boolean))].sort();
    dl.innerHTML = artistas.map(a => `<option value="${escHtml(a)}">`).join("");
  }

  // ── Píldoras de filtro (una activa por fila) ───────────────
  $s("#hubSearch")?.addEventListener("click", (e) => {
    const pill = e.target.closest(".filter-pill");
    if (pill) {
      const clave = ["origen", "genero", "energia"].find(k => k in pill.dataset);
      if (!clave) return;
      _f[clave] = pill.dataset[clave];
      pill.closest(".filter-row")?.querySelectorAll(".filter-pill")
        .forEach(p => p.classList.toggle("active", p === pill));
      applyFilters();
    } else if (e.target.closest("#btnClearFilters")) {
      Object.keys(_f).forEach(k => { _f[k] = ""; });
      $$s("#hubSearch .filter-row").forEach(row =>
        row.querySelectorAll(".filter-pill").forEach((p, i) => p.classList.toggle("active", i === 0)));
      applyFilters();
    }
  });

  // ── Filtros en tiempo real ─────────────────────────────────
  ($s("#studioLibSearch") ?? $s("#searchInput"))?.addEventListener("input", debounce(applyFilters, 150));

  return { load, applyFilters, render, setCue, tracks: () => _tracks };
})();


/* ============================================================
   MÓDULO: STATS BAR
   Actualiza los contadores de inventario en el encabezado del Studio.
   ============================================================ */
const StudioStats = (() => {
  async function load() {
    try {
      const data = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/stats`).then(r => r.json());
      // Mapeamos cada tipo a un elemento en el DOM
      // Ejemplo de HTML esperado: <span id="stat-Musica">0</span>
      ["Musica", "Efecto", "Cuña", "Sweeper", "Programa", "Tips"].forEach(tipo => {
        const el = $s(`#stat-${tipo}`) ?? $s(`[data-stat="${tipo}"]`);
        if (el) el.textContent = data[tipo] ?? 0;
      });
      const total = $s("#stat-total") ?? $s('[data-stat="total"]');
      if (total) total.textContent = data.total ?? 0;
    } catch (err) {
      console.warn("[StudioStats] Error al cargar stats:", err);
    }
  }
  return { load };
})();


/* ============================================================
   MÓDULO: MODE TOGGLE
   Controla el botón Manual / Automático del Studio.
   ============================================================ */
const ModeToggle = (() => {
  let _currentMode = "manual";

  function setMode(mode) {
    _currentMode = mode;
    const chk = $s("#modeToggle");
    if (chk) chk.checked = mode === "automatico";
    const btn = $s("#btnModeToggle") ?? $s(".btn-mode-toggle");
    if (!btn) return;
    btn.textContent = mode === "automatico" ? "⏸ MODO AUTO" : "▶ MODO MANUAL";
    btn.classList.toggle("mode-auto", mode === "automatico");
    btn.classList.toggle("mode-manual", mode === "manual");
  }

  const get = () => _currentMode;

  // Click en el botón envía el comando de cambio de modo
  ($s("#btnModeToggle") ?? $s(".btn-mode-toggle"))?.addEventListener("click", () => {
    const newMode = _currentMode === "manual" ? "automatico" : "manual";
    StudioWS.send({ cmd: "set_mode", mode: newMode });
  });

  // Interruptor MAN/AUTO del encabezado
  $s("#modeToggle")?.addEventListener("change", (e) => {
    StudioWS.send({ cmd: "set_mode", mode: e.target.checked ? "automatico" : "manual" });
  });

  return { setMode, get };
})();


/* ============================================================
   MÓDULO: TRANSPORTE Y COLA
   Botones Play / Stop / Skip y lista de la cola manual.
   ============================================================ */
const Transport = (() => {
  $s("#btnPlayPause")?.addEventListener("click", () => StudioWS.send({ cmd: "play" }));
  $s("#btnStop")?.addEventListener("click", () => StudioWS.send({ cmd: "stop" }));
  $s("#btnSkip")?.addEventListener("click", () => StudioWS.send({ cmd: "skip" }));
  $s("#btnFadeNext")?.addEventListener("click", () => StudioWS.send({ cmd: "skip" }));
  $s("#btnClearQueue")?.addEventListener("click", () => StudioWS.send({ cmd: "clear_queue" }));

  /** Bloque NEXT: lo primero de la cola (o lo que decida el modo automático) */
  function renderNext(queue = [], st = null) {
    const t = queue[0];
    const set = (sel, txt) => { const el = $s(sel); if (el) el.textContent = txt; };
    if (t) {
      const full = StudioLibrary.tracks().find(x => x.id === t.id) ?? {};
      set("#nextTitle", t.titulo); set("#nextArtist", t.artista);
      set("#nextGenero", full.genero_vocal || "—"); set("#nextOrigen", "COLA");
      set("#nextDuracion", fmtSec(full.duracion_seg));
    } else {
      const auto = (st?.modo ?? ModeToggle.get()) === "automatico";
      set("#nextTitle", auto ? "Rotación automática" : "—");
      set("#nextArtist", auto ? "elige el motor" : "Cola vacía");
      set("#nextGenero", "—"); set("#nextOrigen", auto ? "AUTO" : "—");
      set("#nextDuracion", "0:00");
    }
  }

  /** Lista HISTORIAL de la columna izquierda (últimas 15 salidas) */
  function addHistory(item, prepend = true) {
    const list = $s("#historyList");
    if (!list) return;
    list.querySelector(".empty-state-sm")?.remove();
    const div = document.createElement("div");
    div.className = "hist-item";
    const hora = item.hora ?? new Date().toTimeString().slice(0, 5);
    const quien = item.cliente ? `📢 ${item.cliente}` : item.artista;
    div.innerHTML = `<span class="hist-time">${escHtml(hora)}</span>
      <span class="hist-title">${escHtml(item.titulo)} — ${escHtml(quien ?? "")}</span>`;
    prepend ? list.prepend(div) : list.append(div);
    while (list.children.length > 15) list.lastChild.remove();
  }

  async function loadHistory() {
    try {
      const rows = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/historial?limit=15`).then(r => r.json());
      const list = $s("#historyList");
      if (!list || !rows.length) return;
      list.innerHTML = "";
      rows.filter(r => r.tipo !== "efecto").forEach(r => addHistory({
        titulo: r.titulo, artista: r.artista, hora: (r.iniciado_en ?? "").slice(11, 16),
      }, false));
    } catch (err) {
      console.warn("[Transport] Historial no disponible:", err);
    }
  }

  function renderQueue(queue = []) {
    renderNext(queue);
    const list = $s("#playlistList");
    if (!list) return;
    list.innerHTML = queue.length
      ? queue.map((t, i) => `
          <div class="pl-item">
            <span class="pl-pos">${i + 1}</span>
            <div class="pl-info">
              <div class="pl-title">${escHtml(t.titulo)}</div>
              <div class="pl-artist">${escHtml(t.artista)}</div>
            </div>
          </div>`).join("")
      : `<div class="empty-state"><span class="empty-icon">♪</span><span>Cola vacía</span></div>`;
  }

  /** Estado inicial desde la API (modo, pista al aire, cola) */
  async function sync() {
    try {
      const st = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/status`).then(r => r.json());
      ModeToggle.setMode(st.modo ?? "manual");
      NowPlaying.syncFromStatus(st);
      renderQueue(st.queue ?? []);
      renderNext(st.queue ?? [], st);
      EstadoMotor.apply(st);
    } catch (err) {
      console.warn("[Transport] No se pudo leer /api/v1/status:", err);
    }
  }

  return { renderQueue, renderNext, addHistory, loadHistory, sync };
})();


/* ============================================================
   MÓDULO: CARTUCHERA
   Botones de disparo. La asignación vive en la BD (/api/v1/cartuchera):
   clic = disparar · clic derecho = vaciar el botón.
   Se asignan con el botón "→ CART" de las pestañas PUBLICIDAD y JINGLES.
   ============================================================ */
const Cartuchera = (() => {
  const GRIDS = { jingles: "#effectsGrid", publicidad: "#adsGrid" };
  let _slots = { jingles: [], publicidad: [] };

  async function load() {
    try {
      const data = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/cartuchera`).then(r => r.json());
      _slots = data.slots ?? _slots;
      render();
    } catch (err) {
      console.warn("[Cartuchera] No se pudo cargar:", err);
    }
  }

  function render() {
    for (const [banco, sel] of Object.entries(GRIDS)) {
      $$s(`${sel} .effect-btn`).forEach(btn => {
        const asignado = (_slots[banco] ?? []).find(x => x.slot === parseInt(btn.dataset.slot));
        btn.classList.toggle("empty", !asignado);
        btn.dataset.audioId = asignado ? asignado.audio_id : "";
        btn.title = asignado
          ? `${asignado.titulo} — clic: disparar · clic derecho: vaciar`
          : "Vacío — asígnalo con «→ CART» en la biblioteca";
        const name = btn.querySelector(".eff-name");
        if (name) name.textContent = asignado ? (asignado.etiqueta || asignado.titulo) : "vacío";
      });
    }
  }

  /** Asigna el audio al primer botón libre del banco */
  async function assign(banco, audioId) {
    const total = $$s(`${GRIDS[banco]} .effect-btn`).length;
    const usados = new Set((_slots[banco] ?? []).map(x => x.slot));
    let slot = 1;
    while (usados.has(slot)) slot++;
    if (slot > total) {
      studioToast("No quedan botones libres: vacía uno con clic derecho", "warning");
      return;
    }
    const res = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/cartuchera/${banco}/${slot}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio_id: audioId }),
    });
    if (!res.ok) { studioToast("No se pudo asignar a la cartuchera", "error"); return; }
    studioToast(`Asignado al botón ${slot} (${banco})`, "success", 2000);
    load();
  }

  for (const [banco, sel] of Object.entries(GRIDS)) {
    const grid = $s(sel);
    grid?.addEventListener("click", (e) => {
      const btn = e.target.closest(".effect-btn");
      if (!btn?.dataset.audioId) return;
      StudioWS.send({
        cmd: "fire_effect",
        audio_id: parseInt(btn.dataset.audioId),
        canal_offset: parseInt(btn.dataset.slot) - 1,
      });
      btn.classList.add("firing");
      setTimeout(() => btn.classList.remove("firing"), 400);
    });
    grid?.addEventListener("contextmenu", async (e) => {
      const btn = e.target.closest(".effect-btn");
      if (!btn?.dataset.audioId) return;
      e.preventDefault();
      await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/cartuchera/${banco}/${btn.dataset.slot}`, { method: "DELETE" });
      load();
    });
  }

  // Conmutador JINGLES / PUBLICIDAD de la cartuchera
  $$s(".cart-mode-btn").forEach(modeBtn => {
    modeBtn.addEventListener("click", () => {
      $$s(".cart-mode-btn").forEach(b => b.classList.toggle("active", b === modeBtn));
      $s("#cartPanelJingles")?.classList.toggle("active", modeBtn.dataset.mode === "jingles");
      $s("#cartPanelAds")?.classList.toggle("active", modeBtn.dataset.mode === "publicidad");
    });
  });

  /** Añade una línea al registro "Disparos recientes" */
  function logFired(titulo) {
    const list = $s("#effectsLogList");
    if (!list) return;
    list.querySelector(".log-empty")?.remove();
    const li = document.createElement("li");
    li.textContent = `${new Date().toLocaleTimeString()} · ${titulo}`;
    list.prepend(li);
    while (list.children.length > 8) list.lastChild.remove();
  }

  return { load, assign, logFired };
})();


/* ============================================================
   MÓDULO: ESTADO DEL MOTOR (tanda en espera y preescucha)
   En MODO MANUAL las tandas no salen solas: aparece la barra
   ámbar y el operador decide cuándo lanzarla. Se sondea
   /api/v1/status cada 3 s para no depender de un evento perdido.
   ============================================================ */
const EstadoMotor = (() => {
  let _cueDur = 0;

  function apply(st) {
    if (!st) return;
    // ── Tanda en espera ──
    const strip = $s("#tandaStrip");
    const n = st.tanda_en_espera ?? 0;
    strip?.classList.toggle("active", n > 0);
    if (n > 0) {
      const txt = $s("#tandaStripTxt");
      if (txt) txt.textContent = `TANDA EN ESPERA — ${n} cuña(s). En modo manual sale cuando tú la lances.`;
    }
    // ── Preescucha ──
    const cue = st.preescucha;
    StudioLibrary.setCue(cue?.audio_id ?? null);
    $s("#cueStrip")?.classList.toggle("active", !!cue);
    if (cue) {
      _cueDur = cue.duracion || _cueDur;
      const txt = $s("#cueStripTxt");
      if (txt) txt.textContent = `PREESCUCHA (no sale al aire): ${cue.titulo} · ${fmtSec(cue.posicion)} / ${fmtSec(_cueDur)}`;
    }
  }

  async function poll() {
    try {
      apply(await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/status`).then(r => r.json()));
    } catch { /* sin servidor: el indicador WS ya lo muestra */ }
  }

  $s("#btnTandaAhora")?.addEventListener("click", () => {
    StudioWS.send({ cmd: "lanzar_tanda", ahora: true });
    $s("#tandaStrip")?.classList.remove("active");
  });
  $s("#btnTandaAlTerminar")?.addEventListener("click", () => {
    StudioWS.send({ cmd: "lanzar_tanda", ahora: false });
    $s("#tandaStrip")?.classList.remove("active");
    studioToast("📢 La tanda sale al terminar la canción", "info", 3000);
  });
  $s("#btnCueStop")?.addEventListener("click", () => StudioWS.send({ cmd: "cue_stop" }));

  setInterval(poll, 3000);
  return { apply, poll };
})();


/* ============================================================
   MÓDULO: INGESTA
   Sube un audio a la biblioteca desde el Studio (POST /api/v1/audios).
   ============================================================ */
const Ingesta = (() => {
  const form  = $s("#ingestaForm");
  const input = $s("#fileInput");
  const zone  = $s("#fileDropZone");
  const fb    = $s("#ingestaFeedback");
  let _file   = null;

  function _setFile(file) {
    _file = file;
    const txt = $s("#dropContent .drop-text");
    if (txt) txt.innerHTML = file ? `${escHtml(file.name)} <small>${(file.size / 1048576).toFixed(1)} MB</small>`
                                  : "Arrastra el archivo aquí <small>MP3 · WAV · FLAC · OGG</small>";
    // Sugerir título y artista desde el nombre "Artista - Título.mp3"
    if (file) {
      const base = file.name.replace(/\.[^.]+$/, "").replace(/_/g, " ");
      const [art, ...resto] = base.split(" - ");
      const tit = $s("#inputTitulo"), ar = $s("#inputArtista");
      if (tit && !tit.value) tit.value = (resto.length ? resto.join(" - ") : base).trim();
      if (ar && !ar.value && resto.length) ar.value = art.trim();
    }
  }

  function _feedback(msg, ok) {
    if (!fb) return;
    fb.textContent = msg;
    fb.className = `ingesta-feedback ${ok ? "ok" : "err"}`;
  }

  zone?.addEventListener("click", () => input?.click());
  input?.addEventListener("change", () => _setFile(input.files[0] ?? null));
  zone?.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("dragover"); });
  zone?.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone?.addEventListener("drop", e => {
    e.preventDefault();
    zone.classList.remove("dragover");
    if (e.dataTransfer.files[0]) _setFile(e.dataTransfer.files[0]);
  });
  $s("#sliderEnergia")?.addEventListener("input", e => {
    const v = $s("#energiaValue");
    if (v) v.textContent = e.target.value;
  });

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!_file) { _feedback("Elige un archivo de audio", false); return; }
    const fd = new FormData();
    fd.append("file", _file);
    fd.append("titulo", $s("#inputTitulo").value.trim());
    fd.append("artista", $s("#inputArtista").value.trim());
    fd.append("tipo_audio", $s("#selTipo").value);
    const voz = $s("#selGenero").value, origen = $s("#selOrigen").value;
    if (voz) fd.append("voz", voz);
    if (origen) fd.append("origen", origen);
    fd.append("energia", $s("#sliderEnergia").value);
    const intro = parseFloat($s("#inputIntro").value), mix = parseFloat($s("#inputMixPoint").value);
    if (intro > 0) fd.append("cue_intro", intro);
    if (mix > 0) fd.append("cue_mezcla", mix);

    const btn = $s("#btnIngestar");
    if (btn) btn.disabled = true;
    _feedback("Subiendo…", true);
    try {
      const res = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/audios`, { method: "POST", body: fd });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const det = Array.isArray(body.detail) ? body.detail.map(d => d.msg).join("; ") : body.detail;
        throw new Error(det || `HTTP ${res.status}`);
      }
      _feedback(`✓ Ingestado: ${body.data.artista} — ${body.data.titulo}`, true);
      form.reset();
      _setFile(null);
      const v = $s("#energiaValue"); if (v) v.textContent = "3";
    } catch (err) {
      _feedback(`✗ ${err.message}`, false);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  return {};
})();


/* ============================================================
   MÓDULO: CONFIGURACIÓN DEL MOTOR
   Panel de la pestaña INGESTA ↔ /api/v1/config/motor
   ============================================================ */
const MotorConfig = (() => {
  const FIELDS = {
    crossfade_seg:       "#cfgCrossfade",
    ventana_artista_min: "#cfgVentana",
    max_mismo_genero:    "#cfgMaxGenero",
    normalizacion_lufs:  "#cfgLufs",
  };
  const TEXT_FIELDS = { tandas_minutos: "#cfgTandas" };

  async function load() {
    try {
      const cfg = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/config/motor`).then(r => r.json());
      for (const [clave, sel] of Object.entries({ ...FIELDS, ...TEXT_FIELDS })) {
        const el = $s(sel);
        if (el && cfg[clave] != null) el.value = cfg[clave];
      }
    } catch (err) {
      console.warn("[MotorConfig] No se pudo cargar:", err);
    }
  }

  // "Forzar nacional" necesita un campo Origen que la biblioteca aún no tiene
  const nacional = $s("#cfgForzarNacional");
  if (nacional) { nacional.disabled = true; nacional.title = "Pendiente: la biblioteca no tiene campo Origen"; }

  $s("#btnSaveConfig")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const body = {};
    for (const [clave, sel] of Object.entries(FIELDS)) {
      const v = parseFloat($s(sel)?.value);
      if (!isNaN(v)) body[clave] = v;
    }
    for (const [clave, sel] of Object.entries(TEXT_FIELDS)) {
      const v = $s(sel)?.value.trim();
      if (v) body[clave] = v;
    }
    const res = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/config/motor`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (res.ok) {
      studioToast("✓ Configuración del motor guardada", "success");
      load();
    } else {
      const err = await res.json().catch(() => ({}));
      const msg = Array.isArray(err.detail) ? err.detail.map(d => d.msg).join("; ") : (err.detail || "");
      studioToast(`No se pudo guardar la configuración ${msg}`, "error");
    }
  });

  return { load };
})();


/* ============================================================
   MÓDULO: EFFECT PANEL
   Gestiona los botones de jingles/efectos sonoros.
   ============================================================ */
const EffectPanel = (() => {
  /**
   * Registra un botón de efecto en el DOM.
   * Busca todos los [data-effect-id] y les agrega el listener.
   */
  function init() {
    $$s("[data-effect-id]").forEach(btn => {
      btn.addEventListener("click", () => {
        const id     = parseInt(btn.dataset.effectId);
        const canal  = parseInt(btn.dataset.effectCanal ?? "0");
        StudioWS.send({ cmd: "fire_effect", audio_id: id, canal_offset: canal });
        btn.classList.add("effect-fired");
        setTimeout(() => btn.classList.remove("effect-fired"), 400);
      });
    });
  }
  return { init };
})();


/* ============================================================
   MANEJADORES DE EVENTOS WEBSOCKET
   Aquí conectamos los eventos del servidor con los módulos de UI.
   ============================================================ */

// ── Track empezó ──────────────────────────────────────────────
StudioWS.onEvent("track_started", (data) => {
  NowPlaying.setTrack(data);
  Transport.addHistory(data);
  studioToast(`▶ ${data.artista} — ${data.titulo}`, "info", 2500);
});

// ── Track terminó ─────────────────────────────────────────────
StudioWS.onEvent("track_ended", () => {
  // No limpiamos inmediatamente porque el siguiente track
  // debería llegar pronto con track_started. Si no llega,
  // el crossfade_start o engine_stopped lo harán.
});

// ── Crossfade iniciado ────────────────────────────────────────
StudioWS.onEvent("crossfade_start", (data) => {
  const el = $s("#studioCrossfadeIndicator");
  if (el) {
    el.style.display = "block";
    // Ocultar después de la duración del crossfade
    setTimeout(() => { el.style.display = "none"; }, (data?.duracion_seg ?? 3) * 1000);
  }
});

// ── Reproducción detenida (stop manual o cola agotada) ────────
StudioWS.onEvent("playback_stopped", () => {
  NowPlaying.clearTrack();
});

// ── Cartuchera modificada (desde este u otro puesto) ──────────
StudioWS.onEvent("cartuchera_updated", () => {
  Cartuchera.load();
});

// ── Cola manual actualizada ───────────────────────────────────
StudioWS.onEvent("queue_updated", (data) => {
  Transport.renderQueue(data?.queue ?? []);
});

// ── Tanda de pautas lanzada (sale en el próximo cambio de pista) ──
StudioWS.onEvent("tanda_scheduled", (data) => {
  const n = data?.pautas?.length ?? 0;
  const min = String(data?.minuto ?? 0).padStart(2, "0");
  if (data?.requiere_operador) {
    studioToast(`📢 Tanda :${min} lista — ${n} cuña(s). Lánzala cuando quieras.`, "warning", 6000);
    EstadoMotor.poll();
  } else {
    studioToast(`📢 Tanda :${min} — ${n} cuña(s) en cola`, "info", 4000);
  }
});

// ── Preescucha (salida interna) ───────────────────────────────
StudioWS.onEvent("cue_started", () => EstadoMotor.poll());
StudioWS.onEvent("cue_stopped", () => EstadoMotor.poll());

// ── Lista de respaldo (la rotación no tenía nada que sonar) ───
StudioWS.onEvent("respaldo_activado", (data) => {
  studioToast(`⚠ Sonando lista de respaldo: ${data?.archivo ?? ""}`, "warning", 5000);
});

// ── Motor detenido ────────────────────────────────────────────
StudioWS.onEvent("engine_stopped", () => {
  NowPlaying.clearTrack();
  studioToast("Motor de audio detenido", "warning");
});

// ── Modo cambiado ─────────────────────────────────────────────
StudioWS.onEvent("mode_changed", (data) => {
  ModeToggle.setMode(data?.mode ?? "manual");
  EstadoMotor.poll();
  studioToast(`Modo: ${data?.mode?.toUpperCase() ?? "?"}`, "info", 2000);
});

// ── Efecto disparado ─────────────────────────────────────────
StudioWS.onEvent("effect_fired", (data) => {
  studioToast(`🎵 Efecto: ${data?.titulo ?? data?.audio_id}`, "info", 1800);
  Cartuchera.logFired(data?.titulo ?? `ID ${data?.audio_id}`);
});

// ── Error de reproducción ─────────────────────────────────────
StudioWS.onEvent("playback_error", (data) => {
  const msg = data?.msg ?? "Error desconocido en reproducción";
  studioToast(`⚠ ${msg}`, "error", 5000);
  console.error("[Studio] playback_error:", data);
});

// ── Watchdog: recuperación automática ────────────────────────
StudioWS.onEvent("watchdog_recovery", (data) => {
  studioToast("⚡ Recuperación automática del motor", "warning", 4000);
  console.warn("[Studio] watchdog_recovery:", data);
});

StudioWS.onEvent("watchdog_recovered", (data) => {
  studioToast("✓ Motor recuperado", "success", 3000);
});

StudioWS.onEvent("watchdog_stall", (data) => {
  studioToast("⚠ Motor estancado — revisar servidor", "error", 6000);
  console.error("[Studio] watchdog_stall:", data);
});

// ── Pong (respuesta al ping keep-alive) ──────────────────────
StudioWS.onEvent("pong", () => {
  // Silencioso — solo para confirmar que la conexión sigue viva
});

// ── Biblioteca actualizada: recargar con pequeño delay ───────
// El delay de 400ms evita que una ráfaga de cambios simultáneos
// lance múltiples peticiones GET. El último sobrescribe los anteriores.
const _debouncedLibLoad = debounce(() => StudioLibrary.load(), STUDIO_CONFIG.libReloadDelay);
StudioWS.onEvent("library_updated", (data) => {
  _debouncedLibLoad();
  // Si la acción fue una eliminación, actualizar stats también
  if (data?.action === "deleted" || data?.action === "created") {
    StudioStats.load();
  }
});

// ── Contadores actualizados ───────────────────────────────────
StudioWS.onEvent("stats_updated", () => {
  StudioStats.load();
});

// ── Bloques horarios actualizados ────────────────────────────
StudioWS.onEvent("bloques_updated", (data) => {
  // El Studio puede mostrar la pauta activa; notificar al operador
  studioToast("Pauta actualizada desde Admin", "info", 2500);
  // Si hay un módulo de pauta del Studio, recargarlo:
  window.StudioPauta?.reload?.();
});


/* ============================================================
   INICIALIZACIÓN
   Carga inicial de datos y setup de controles UI.
   ============================================================ */
(async function init() {
  // Carga la biblioteca y estadísticas en paralelo
  await Promise.allSettled([
    StudioLibrary.load(),
    StudioStats.load(),
    Transport.sync(),
    Cartuchera.load(),
    MotorConfig.load(),
    Transport.loadHistory(),
  ]);

  // Inicializar panel de efectos (si existe en el HTML)
  EffectPanel.init();

  // Reloj del encabezado (hora de la PC del Studio)
  const reloj = $s("#systemClock");
  if (reloj) {
    const tick = () => { reloj.textContent = new Date().toTimeString().slice(0, 8); };
    tick();
    setInterval(tick, 1000);
  }

  console.info("[StudioApp] Módulos inicializados. Versión 3.0 — Ónix FM Digital.");
})();


/* ============================================================
   API PÚBLICA
   Expone los módulos al scope global para que el HTML inline
   o scripts adicionales puedan interactuar con ellos.
   ============================================================ */
window.StudioApp = {
  ws:      StudioWS,
  lib:     StudioLibrary,
  stats:   StudioStats,
  now:     NowPlaying,
  mode:    ModeToggle,
  effects: EffectPanel,
  toast:   studioToast,

  // Helpers rápidos para uso desde botones HTML
  play:  (id)          => StudioWS.send({ cmd: "play",        audio_id: id }),
  queue: (id)          => StudioWS.send({ cmd: "queue",       audio_id: id }),
  fire:  (id, canal=0) => StudioWS.send({ cmd: "fire_effect", audio_id: id, canal_offset: canal }),
  setMode: (m)         => StudioWS.send({ cmd: "set_mode",    mode: m }),
  cue:   (id)          => StudioWS.send({ cmd: "cue",         audio_id: id }),
};
