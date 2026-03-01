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
        const { event, data: payload } = JSON.parse(data);
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
    _startedAt = Date.now();

    // Campos de texto en la UI
    _setText("#studioNowTitle",  `${escHtml(data.artista)} — ${escHtml(data.titulo)}`);
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
      const bar = $s("#studioProgressBar");
      if (bar) bar.style.width = `${pct.toFixed(1)}%`;
      // Actualizar tiempo transcurrido
      _setText("#studioNowElapsed", fmtSec(elapsed));
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

  return { setTrack, clearTrack };
})();


/* ============================================================
   MÓDULO: LIBRARY
   Carga y mantiene la lista de tracks disponibles en el Studio.
   Permite reproducir, encolar y escuchar preescucha sin salir.
   ============================================================ */
const StudioLibrary = (() => {
  let _tracks   = [];
  let _filtered = [];
  let _previewAudio = null;
  let _previewId    = null;

  /** Carga todos los tracks activos desde la API */
  async function load(force = false) {
    try {
      const params = new URLSearchParams({ limit: "3000" });
      const res = await fetch(`${STUDIO_CONFIG.apiBase}/api/v1/audios?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      _tracks = await res.json();
      // Aplicar filtros actuales al nuevo set
      applyFilters();
    } catch (err) {
      console.error("[StudioLibrary] Error al cargar biblioteca:", err);
      studioToast("Error al cargar la biblioteca", "error");
    }
  }

  /** Filtra en memoria según el texto del buscador */
  function applyFilters() {
    const q = ($s("#studioLibSearch")?.value ?? "").toLowerCase().trim();
    const tipo = $s("#studioLibFilterTipo")?.value ?? "";

    _filtered = _tracks.filter(t => {
      if (tipo && t.tipo_audio !== tipo) return false;
      if (q && !(
        t.titulo.toLowerCase().includes(q)  ||
        t.artista.toLowerCase().includes(q) ||
        (t.genero_vocal ?? "").toLowerCase().includes(q)
      )) return false;
      return true;
    });

    render();
    const counter = $s("#studioLibCount");
    if (counter) counter.textContent = `${_filtered.length} / ${_tracks.length}`;
  }

  /** Renderiza la tabla de la biblioteca en el Studio */
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
        <td class="lib-col-meta">${escHtml(t.genero_vocal ?? "")}</td>
        <td class="lib-col-dur">${fmtSec(t.duracion_seg)}</td>
        <td class="lib-col-energy">${"★".repeat(t.energia ?? 0)}</td>
        <td class="lib-col-prev">
          <button class="lib-btn-prev" data-prev-id="${t.id}" title="Preescuchar">▶</button>
        </td>
        <td class="lib-col-actions">
          <button class="lib-btn-play"  data-play-id="${t.id}"  title="Reproducir ahora">▶ AIRE</button>
          <button class="lib-btn-queue" data-queue-id="${t.id}" title="Añadir a cola">+ COLA</button>
        </td>
      </tr>
    `).join("");

    // Botones de reproducción
    tbody.querySelectorAll("[data-play-id]").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.playId);
        StudioWS.send({ cmd: "play", audio_id: id });
      });
    });

    // Botones de cola
    tbody.querySelectorAll("[data-queue-id]").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.queueId);
        StudioWS.send({ cmd: "queue", audio_id: id });
        const t = _tracks.find(x => x.id === id);
        studioToast(`+ Cola: ${t?.titulo ?? id}`, "info");
      });
    });

    // Botones de preescucha
    tbody.querySelectorAll("[data-prev-id]").forEach(btn => {
      btn.addEventListener("click", () => togglePreview(parseInt(btn.dataset.prevId), btn));
    });
  }

  /** Obtiene el ID del track actualmente al aire (desde el strip) */
  function _getNowId() {
    return parseInt($s(".row-now")?.dataset?.audioId ?? "0") || null;
  }

  /** Alterna la preescucha en línea de un track */
  function togglePreview(id, btnEl) {
    // Mismo track: pausar
    if (_previewId === id && _previewAudio) {
      _previewAudio.pause();
      _clearPreviewBtn(btnEl);
      _previewAudio = null; _previewId = null;
      return;
    }
    // Otro track activo: detenerlo primero
    if (_previewAudio) {
      _previewAudio.pause();
      const oldBtn = document.querySelector(".lib-btn-prev.previewing");
      if (oldBtn) _clearPreviewBtn(oldBtn);
    }
    // El endpoint /stream soporta Range headers — el <audio> puede saltar
    _previewAudio = new Audio(`${STUDIO_CONFIG.apiBase}/api/v1/audios/${id}/stream`);
    _previewId    = id;
    btnEl.classList.add("previewing");
    btnEl.textContent = "■";

    _previewAudio.play().catch(err => {
      studioToast(`No se pudo preescuchar: ${err.message}`, "warning");
      _clearPreviewBtn(btnEl);
      _previewAudio = null; _previewId = null;
    });
    _previewAudio.onended = () => {
      _clearPreviewBtn(btnEl);
      _previewAudio = null; _previewId = null;
    };
  }

  function _clearPreviewBtn(btn) {
    if (!btn) return;
    btn.classList.remove("previewing");
    btn.textContent = "▶";
  }

  // ── Filtros en tiempo real ─────────────────────────────────
  $s("#studioLibSearch")?.addEventListener("input", debounce(applyFilters, 150));
  $s("#studioLibFilterTipo")?.addEventListener("change", applyFilters);

  return { load, applyFilters, render };
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
    const btn = $s("#btnModeToggle") ?? $s(".btn-mode-toggle");
    if (!btn) return;
    btn.textContent = mode === "automatico" ? "⏸ MODO AUTO" : "▶ MODO MANUAL";
    btn.classList.toggle("mode-auto", mode === "automatico");
    btn.classList.toggle("mode-manual", mode === "manual");
  }

  // Click en el botón envía el comando de cambio de modo
  ($s("#btnModeToggle") ?? $s(".btn-mode-toggle"))?.addEventListener("click", () => {
    const newMode = _currentMode === "manual" ? "automatico" : "manual";
    StudioWS.send({ cmd: "set_mode", mode: newMode });
  });

  return { setMode };
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

// ── Motor detenido ────────────────────────────────────────────
StudioWS.onEvent("engine_stopped", () => {
  NowPlaying.clearTrack();
  studioToast("Motor de audio detenido", "warning");
});

// ── Modo cambiado ─────────────────────────────────────────────
StudioWS.onEvent("mode_changed", (data) => {
  ModeToggle.setMode(data?.mode ?? "manual");
  studioToast(`Modo: ${data?.mode?.toUpperCase() ?? "?"}`, "info", 2000);
});

// ── Efecto disparado ─────────────────────────────────────────
StudioWS.onEvent("effect_fired", (data) => {
  studioToast(`🎵 Efecto: ${data?.titulo ?? data?.audio_id}`, "info", 1800);
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
  ]);

  // Inicializar panel de efectos (si existe en el HTML)
  EffectPanel.init();

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
  mode:  (m)           => StudioWS.send({ cmd: "set_mode",    mode: m }),
};
