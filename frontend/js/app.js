/**
 * app.js — Solís FM Digital
 * Consola de radio profesional. JavaScript ES6+ puro, sin frameworks.
 *
 * MÓDULOS INTERNOS:
 *   Clock          — Reloj de sistema en tiempo real.
 *   SolisWS        — WebSocket con reconexión exponencial. Sistema de eventos
 *                    extensible: SolisWS.onEvent("nombre", handler).
 *   UIState        — Estado global reactivo (track actual, modo, historial).
 *   NowPlaying     — Actualiza el panel superior con track_started/track_ended.
 *   VUMeter        — Animación de vúmetros (simulada; reemplazable con datos reales).
 *   ProgressBar    — Barra de progreso y contadores de tiempo.
 *   Library        — Carga y renderiza las tablas de biblioteca por tab.
 *   HubTabs        — Lógica de cambio de pestañas sin recarga.
 *   Playlist       — Gestión visual de la cola de reproducción.
 *   CartucheraDual — Toggle Jingles/Publicidad + disparo de efectos.
 *   Autocomplete   — Predicción de artista con debounce y teclado.
 *   IngestaForm    — Formulario de carga de audio con drag-and-drop.
 *   ConfigManager  — Lectura y escritura de configuración del motor.
 *   Toast          — Notificaciones de esquina.
 *
 * EXTENSIÓN PARA MÓDULO DE PUBLICIDAD (futuro):
 *   SolisWS.onEvent("ad_scheduled", (data) => { ... })
 *   El backend emitirá ese evento. Este archivo solo necesita escucharlo.
 */

"use strict";

const API = "";                              // mismo origen que FastAPI
const WS_URL = `ws://${location.host}/ws`;

/* ════════════════════════════════════════════════════════════
   UTILIDADES
════════════════════════════════════════════════════════════ */
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// Convierte segundos a "M:SS"
function fmtTime(sec) {
  if (!sec || isNaN(sec)) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

// Convierte segundos a "H:MM:SS" para duraciones largas (playlist total)
function fmtDuration(sec) {
  if (!sec || isNaN(sec)) return "0:00:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60).toString().padStart(2, "0");
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${h}:${m}:${s}`;
}

/* ════════════════════════════════════════════════════════════
   RELOJ DEL SISTEMA
════════════════════════════════════════════════════════════ */
(function Clock() {
  const el = $("#systemClock");
  const tick = () => { el.textContent = new Date().toTimeString().slice(0, 8); };
  tick();
  setInterval(tick, 1000);
})();

/* ════════════════════════════════════════════════════════════
   TOASTS
════════════════════════════════════════════════════════════ */
function showToast(msg, type = "info", ms = 3500) {
  const container = $("#toastContainer");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

/* ════════════════════════════════════════════════════════════
   UIState — Estado global de la interfaz
════════════════════════════════════════════════════════════ */
const UIState = {
  currentTrack:  null,
  nextTrack:     null,
  mode:          "manual",
  elapsedSec:    0,
  playbackTimer: null,
  library:       { Musica: [], Efecto: [], Cuña: [], Tips: [] },
  queue:         [],
  history:       [],

  setTrack(track) {
    // Mueve el track actual al historial antes de reemplazarlo
    if (this.currentTrack) {
      this.history.unshift({
        titulo: this.currentTrack.titulo,
        artista: this.currentTrack.artista,
        ts: new Date().toTimeString().slice(0, 8),
      });
      if (this.history.length > 20) this.history.pop();
      Playlist.renderHistory();
    }
    this.currentTrack = track;
    this.elapsedSec = 0;
    clearInterval(this.playbackTimer);
    if (track) {
      // Timer de 1 segundo que alimenta la barra de progreso
      this.playbackTimer = setInterval(() => {
        this.elapsedSec++;
        ProgressBar.update();
      }, 1000);
    }
  },
};

/* ════════════════════════════════════════════════════════════
   SolisWS — WebSocket con reconexión automática
════════════════════════════════════════════════════════════ */
const SolisWS = (() => {
  let ws = null;
  let retryDelay = 1500;
  const handlers = {};

  function connect() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      retryDelay = 1500;
      setStatus("connected");
      showToast("Motor de audio conectado", "success", 2500);
    };

    ws.onmessage = ({ data }) => {
      try {
        const { event, data: payload } = JSON.parse(data);
        // Despachar a suscriptores específicos y al comodín "*"
        (handlers[event] || []).forEach(fn => fn(payload));
        (handlers["*"]   || []).forEach(fn => fn(event, payload));
      } catch (e) {
        console.error("[WS] Error al parsear mensaje:", e);
      }
    };

    ws.onclose = () => {
      setStatus("error");
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 20000);
    };

    ws.onerror = () => setStatus("error");
  }

  function send(payload) {
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }

  /**
   * Suscribirse a un evento del motor.
   * El módulo de publicidad futuro usará:
   *   SolisWS.onEvent("ad_scheduled", handler)
   */
  function onEvent(event, fn) {
    handlers[event] = handlers[event] || [];
    handlers[event].push(fn);
  }

  function setStatus(state) {
    const el = $("#wsStatus");
    el.className = "ws-indicator " + state;
  }

  connect();
  return { send, onEvent };
})();

/* ════════════════════════════════════════════════════════════
   MANEJADORES DE EVENTOS DEL MOTOR
   Cada evento emitido por el EventBus de Python llega aquí
   y actualiza la interfaz correspondiente.
════════════════════════════════════════════════════════════ */
SolisWS.onEvent("track_started", (data) => {
  UIState.setTrack(data);
  NowPlaying.render(data);
  showToast(`▶  ${data.artista} — ${data.titulo}`, "info", 4000);
});

SolisWS.onEvent("track_ended", () => {
  clearInterval(UIState.playbackTimer);
  $("#trackArt")?.classList.remove("spinning");
  VUMeter.stop();
});

SolisWS.onEvent("crossfade_start", (data) => {
  const marker = $("#crossfadeZone");
  if (!marker) return;
  const pct = (data.duracion_crossfade / (UIState.currentTrack?.duracion_seg || 1)) * 100;
  marker.style.width = `${Math.min(pct, 25)}%`;
  marker.classList.add("visible");
  $("#crossfadeLabel").textContent = `⇄ CF ${data.duracion_crossfade}s`;
  showToast("⇄ Crossfade iniciado", "info", 2000);
});

SolisWS.onEvent("mode_changed", (data) => {
  UIState.mode = data.modo;
  $("#modeToggle").checked = data.modo === "automatico";
  showToast(`Modo: ${data.modo.toUpperCase()}`, "warning");
});

SolisWS.onEvent("effect_fired", (data) => {
  $("#duckingIndicator").classList.add("active");
  setTimeout(() => $("#duckingIndicator").classList.remove("active"), 8000);
  CartucheraDual.logDisparo(data.audio_id);
});

SolisWS.onEvent("engine_stopped", () => {
  VUMeter.stop();
  showToast("Motor detenido", "warning");
});

// Estado inicial al conectar
SolisWS.onEvent("status", (data) => {
  UIState.mode = data.modo;
  $("#modeToggle").checked = data.modo === "automatico";
  if (data.current_track) {
    UIState.currentTrack = data.current_track;
    NowPlaying.render(data.current_track);
  }
});

// ── Sincronización en tiempo real: Admin → Studio ─────────────────────────
// Este listener es la pieza central de la sincronización en tiempo real.
// Cuando el Admin sube una nueva canción, el backend emite "library_updated"
// a todos los clientes WebSocket conectados. Al recibirlo aquí, se invalida
// la caché del tipo afectado y se recarga la pestaña activa si corresponde.
// Resultado: la canción aparece en el Studio sin que nadie tenga que recargar.
SolisWS.onEvent("library_updated", (data) => {
  const tipo   = data.tipo || null;
  const action = data.action || "created";

  // Invalidar el tipo afectado (o todos si no se especificó)
  if (tipo) {
    Library.invalidate(tipo);
  } else {
    ["Musica", "Efecto", "Cuña", "Tips"].forEach(t => Library.invalidate(t));
  }

  // Recargar el tab activo si muestra el tipo que cambió
  const activeTab  = $(".hub-tab.active");
  const activeTipo = activeTab?.dataset.tipo;
  if (!tipo || activeTipo === tipo) {
    // force=true fuerza la recarga aunque el tab esté "cargado"
    Library.loadTab(activeTab?.dataset.tab, activeTipo, true);
  }

  // También recargar la cartuchera si cambió Efecto o Cuña
  if (!tipo || tipo === "Efecto" || tipo === "Cuña") {
    CartucheraDual.reload();
  }

  if (action === "created") showToast("📥 Nueva pista en biblioteca", "success", 2500);
  if (action === "deleted")  showToast("🗑 Pista eliminada", "info", 2000);
  if (action === "moved")    showToast("📂 Archivo movido", "info", 2000);
});

// ── Error de reproducción: archivo faltante o codec no soportado ──────────
SolisWS.onEvent("playback_error", (data) => {
  const msg = data.reason === "archivo_no_encontrado"
    ? `⚠ Archivo no encontrado: ${data.path || "desconocido"}`
    : `⚠ Error de reproducción (ID ${data.audio_id || "?"})`;
  showToast(msg, "error", 5000);
});

// ── Watchdog: recuperación automática del motor ───────────────────────────
SolisWS.onEvent("watchdog_recovery", (data) => {
  const tipo = data.tipo === "silence" ? "Silencio" : "Fallo";
  showToast(`🔄 Motor: ${tipo} detectado — recuperando...`, "warning", 4000);
});

SolisWS.onEvent("watchdog_recovered", (data) => {
  const methods = {
    resume:        "Stream reanudado",
    next_track:    "Saltando a siguiente pista",
    stall_restart: "Stream reiniciado (stall)",
    stall_skip:    "Stall: saltando pista",
  };
  const msg = methods[data.method] || "Motor recuperado";
  showToast(`✅ Motor OK — ${msg}`, "success", 3000);
});

SolisWS.onEvent("watchdog_stall", (data) => {
  showToast(`⚠ Stall detectado (${data.stall_seg?.toFixed(1)}s) — reiniciando...`, "warning", 4000);
});

/* ════════════════════════════════════════════════════════════
   NowPlaying — Panel superior: título, artista, badges
════════════════════════════════════════════════════════════ */
const NowPlaying = {
  GENERO_ICONS: { Hombre: "♂", Mujer: "♀", Dueto: "⚤", Instrumental: "♪" },

  render(track) {
    if (!track) return;

    $("#trackTitle").textContent  = track.titulo  || "—";
    $("#trackArtist").textContent = track.artista || "—";

    const gIcon = this.GENERO_ICONS[track.genero_vocal] || "";
    $("#badgeGenero").textContent  = `${gIcon} ${track.genero_vocal || "—"}`;
    $("#badgeOrigen").textContent  = track.origen || "—";
    $("#badgeEnergia").textContent = `E${track.energia ?? "—"}`;

    // Activar ON AIR
    const badge = $("#onAirBadge");
    badge.classList.add("active");
    badge.querySelector(".on-air-text").textContent = "ON AIR";

    VUMeter.start();
  },
};

/* ════════════════════════════════════════════════════════════
   VUMeter — Animación de vúmetros (simulada)
   Para usar niveles reales del motor, reemplaza tick() con
   datos enviados por WebSocket desde el backend de audio.
════════════════════════════════════════════════════════════ */
const VUMeter = (() => {
  let timer = null;

  function tick() {
    // Simula niveles de audio con algo de naturalidad física
    const base = 35 + Math.random() * 45;
    const peak = Math.random() > 0.93 ? base + 25 : 0;
    const L = Math.min(base + peak + (Math.random() - .5) * 12, 100);
    const R = Math.min(base      + (Math.random() - .5) * 18, 100);
    setBar("vuBarL", L);
    setBar("vuBarR", R);
  }

  function setBar(id, pct) {
    const el = document.getElementById(id);
    if (el) el.style.height = `${pct}%`;
  }

  return {
    start() {
      if (timer) return;
      timer = setInterval(tick, 85);
    },
    stop() {
      clearInterval(timer);
      timer = null;
      setBar("vuBarL", 0);
      setBar("vuBarR", 0);
    },
  };
})();

/* ════════════════════════════════════════════════════════════
   ProgressBar — Barra de progreso y contadores de tiempo
════════════════════════════════════════════════════════════ */
const ProgressBar = {
  update() {
    const track = UIState.currentTrack;
    if (!track) return;
    const dur = track.duracion_seg || 0;
    const el  = UIState.elapsedSec;
    const pct = dur > 0 ? Math.min((el / dur) * 100, 100) : 0;

    $("#progressFill").style.width     = `${pct}%`;
    $("#timeElapsed").textContent      = fmtTime(el);
    $("#timeRemainingBar").textContent = fmtTime(Math.max(0, dur - el));
    $("#timeRemaining").textContent    = fmtTime(Math.max(0, dur - el));
  },
};

/* ════════════════════════════════════════════════════════════
   HubTabs — Cambio de pestañas del buscador central
════════════════════════════════════════════════════════════ */
const HubTabs = (() => {
  const searchEl = $("#hubSearch");

  function activate(tab) {
    // Desactivar todos los tabs y paneles
    $$(".hub-tab").forEach(t => t.classList.remove("active"));
    $$(".tab-panel").forEach(p => p.classList.remove("active"));

    // Activar el tab solicitado
    const btn   = $(`.hub-tab[data-tab="${tab}"]`);
    const panel = $(`#tab-${tab}`);
    btn?.classList.add("active");
    panel?.classList.add("active");

    // El tab de ingesta oculta el buscador
    if (tab === "ingesta") {
      searchEl.classList.add("hidden");
    } else {
      searchEl.classList.remove("hidden");
      // Cargar datos del tab si no están cargados aún
      Library.loadTab(tab, btn?.dataset.tipo || "");
    }
  }

  $$(".hub-tab").forEach(btn => {
    btn.addEventListener("click", () => activate(btn.dataset.tab));
  });

  // Activar el tab inicial
  activate("musica");

  return { activate };
})();

/* ════════════════════════════════════════════════════════════
   Library — Carga y renderiza las tablas de biblioteca
════════════════════════════════════════════════════════════ */
const Library = (() => {
  const loaded = {}; // cache flag por tipo

  // Mapeo tipo → tbody DOM ID (incluyendo Tips)
  const TBODY_IDS = {
    Musica: "libraryList",
    Efecto: "jingleList",
    Cuña:   "adList",
    Tips:   "tipsList",
  };

  // Estado combinado de filtros — todos activos simultáneamente
  const filters = {
    q:       "",     // texto de búsqueda libre
    origen:  null,   // "Nacional" | "Internacional" | null (todos)
    genero:  null,   // "Hombre" | "Mujer" | "Dueto" | "Instrumental" | null (todos)
    energia: null,   // 1-5 | null (todos)
  };

  // Carga datos del servidor y los mete en UIState.library[tipo]
  // force=true omite la caché y siempre hace fetch (usado por library_updated)
  async function loadTab(tab, tipo, force = false) {
    if (!tipo) return;
    // Usar caché si está disponible y no se fuerza recarga
    if (loaded[tipo] && !force) {
      return applyFiltersAndRender(tipo);
    }
    try {
      const params = new URLSearchParams({ tipo, limit: 500 });
      const res    = await fetch(`${API}/api/v1/audios?${params}`);
      const tracks = await res.json();
      UIState.library[tipo] = tracks;
      loaded[tipo] = true;
      applyFiltersAndRender(tipo);
      $("#searchCount").textContent = `${tracks.length} registros`;
    } catch (err) {
      console.error("[Library] Error cargando tipo", tipo, err);
    }
  }

  // Aplica los filtros activos sobre UIState.library[tipo] y renderiza
  // Esta función es el núcleo del sistema de filtrado:
  // combina búsqueda de texto, origen, género y energía en un solo paso.
  function applyFiltersAndRender(tipo) {
    if (!tipo || !UIState.library[tipo]) return;
    const all = UIState.library[tipo];

    const filtered = all.filter(t => {
      // Filtro de texto: busca en título y artista (case-insensitive)
      if (filters.q) {
        const q = filters.q;
        const match = t.titulo.toLowerCase().includes(q) ||
                      t.artista.toLowerCase().includes(q);
        if (!match) return false;
      }
      // Filtro de origen
      if (filters.origen && t.origen !== filters.origen) return false;
      // Filtro de género vocal (solo aplica a Música)
      if (filters.genero && t.genero_vocal !== filters.genero) return false;
      // Filtro de energía
      if (filters.energia && t.energia !== filters.energia) return false;
      return true;
    });

    renderTbody(tipo, filtered);
    $("#searchCount").textContent = `${filtered.length} de ${all.length} registros`;
  }

  function renderTbody(tipo, tracks) {
    const tbodyId = TBODY_IDS[tipo];
    const tbody   = $(`#${tbodyId}`);
    if (!tbody) return;

    if (!tracks.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">Sin registros${filters.q || filters.origen || filters.genero ? " (prueba limpiar filtros)" : " para este tipo"}</td></tr>`;
      return;
    }

    tbody.innerHTML = tracks.map(t => `
      <tr data-id="${t.id}" class="${UIState.currentTrack?.id === t.id ? "row-playing" : ""}">
        <td class="td-main">
          <span class="td-title-text">${esc(t.titulo)}</span>
          <span class="td-artist-text">${esc(t.artista)}</span>
        </td>
        <td class="td-dur">${fmtTime(t.duracion_seg)}</td>
        ${tipo === "Musica" ? `
          <td class="td-tag">${esc(t.genero_vocal || "—")}</td>
          <td class="td-tag ${t.origen === "Nacional" ? "tag-nac" : "tag-int"}">${t.origen === "Nacional" ? "NAC" : "INT"}</td>
          <td class="td-e td-e--${t.energia ?? 0}">${t.energia ?? "—"}</td>
        ` : `<td colspan="3" class="td-tag-empty"></td>`}
        <td class="td-actions">
          <button class="btn-row-play"  data-id="${t.id}" title="Reproducir ahora">▶</button>
          <button class="btn-row-queue" data-id="${t.id}" title="Añadir a cola">+</button>
        </td>
      </tr>
    `).join("");

    // Delegación de eventos para play y queue
    tbody.querySelectorAll(".btn-row-play").forEach(btn => {
      btn.addEventListener("click", e => {
        e.stopPropagation();
        playTrackById(parseInt(btn.dataset.id));
      });
    });
    tbody.querySelectorAll(".btn-row-queue").forEach(btn => {
      btn.addEventListener("click", e => {
        e.stopPropagation();
        queueTrackById(parseInt(btn.dataset.id), tipo);
      });
    });
  }

  // ── Listeners de filtros ─────────────────────────────────────────────────
  // Búsqueda de texto con debounce (180ms para no spamear mientras se escribe)
  const searchInput = $("#searchInput");
  searchInput?.addEventListener("input", debounce(() => {
    filters.q = searchInput.value.trim().toLowerCase();
    const tipo = $(".hub-tab.active")?.dataset.tipo;
    applyFiltersAndRender(tipo);
  }, 180));

  // Pills de origen: Nacional / Internacional / Todos
  $$(".filter-pill[data-origen]").forEach(pill => {
    pill.addEventListener("click", () => {
      $$(".filter-pill[data-origen]").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      filters.origen = pill.dataset.origen || null;
      const tipo = $(".hub-tab.active")?.dataset.tipo;
      applyFiltersAndRender(tipo);
    });
  });

  // Pills de género: Todos / Hombre / Mujer / Dueto / Instrumental
  // (estos elementos deben existir en index.html con data-genero="...")
  $$(".filter-pill[data-genero]").forEach(pill => {
    pill.addEventListener("click", () => {
      $$(".filter-pill[data-genero]").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      filters.genero = pill.dataset.genero || null;
      const tipo = $(".hub-tab.active")?.dataset.tipo;
      applyFiltersAndRender(tipo);
    });
  });

  // Pills de energía: 1-5
  $$(".filter-pill[data-energia]").forEach(pill => {
    pill.addEventListener("click", () => {
      $$(".filter-pill[data-energia]").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      const v = pill.dataset.energia;
      filters.energia = v ? parseInt(v) : null;
      const tipo = $(".hub-tab.active")?.dataset.tipo;
      applyFiltersAndRender(tipo);
    });
  });

  // ── Botón limpiar filtros ────────────────────────────────────────────────
  $("#btnClearFilters")?.addEventListener("click", () => {
    filters.q = "";
    filters.origen = null;
    filters.genero = null;
    filters.energia = null;
    if (searchInput) searchInput.value = "";
    // Desmarcar todas las pills
    $$(".filter-pill").forEach(p => p.classList.remove("active"));
    // Marcar la pill "Todos" de origen si existe
    $(".filter-pill[data-origen='']")?.classList.add("active");
    $(".filter-pill[data-genero='']")?.classList.add("active");
    const tipo = $(".hub-tab.active")?.dataset.tipo;
    applyFiltersAndRender(tipo);
  });

  // ── Invalidar caché ──────────────────────────────────────────────────────
  // Llamado por el handler de library_updated y por la ingesta local
  function invalidate(tipo) {
    if (tipo) {
      loaded[tipo] = false;
      UIState.library[tipo] = [];
    } else {
      // Sin tipo específico: invalidar todo
      Object.keys(TBODY_IDS).forEach(t => {
        loaded[t] = false;
        UIState.library[t] = [];
      });
    }
  }

  return { loadTab, invalidate, applyFiltersAndRender };
})();

/* ════════════════════════════════════════════════════════════
   Reproducción y cola
════════════════════════════════════════════════════════════ */
function playTrackById(id) {
  SolisWS.send({ cmd: "play", audio_id: id });
  fetch(`${API}/api/v1/play/${id}`, { method: "POST" }).catch(() => {});
}

function queueTrackById(id, tipo) {
  // Buscar el track en la biblioteca para tenerlo disponible en la cola visual
  const track = UIState.library[tipo]?.find(t => t.id === id);
  if (!track) return;

  UIState.queue.push(track);
  SolisWS.send({ cmd: "queue", audio_id: id });
  Playlist.render();
  showToast(`+ Cola: ${track.titulo}`, "info", 2000);
}

/* ════════════════════════════════════════════════════════════
   Playlist — Cola visual y historial
════════════════════════════════════════════════════════════ */
const Playlist = {
  render() {
    const el = $("#playlistList");
    if (!UIState.queue.length) {
      el.innerHTML = `<div class="empty-state"><span class="empty-icon">♪</span><span>Cola vacía</span></div>`;
      $("#playlistDuration").textContent = "—";
      return;
    }

    const totalSec = UIState.queue.reduce((acc, t) => acc + (t.duracion_seg || 0), 0);
    $("#playlistDuration").textContent = fmtDuration(totalSec);

    el.innerHTML = UIState.queue.map((t, i) => `
      <div class="pl-item" data-index="${i}" data-id="${t.id}">
        <span class="pl-pos">${i + 1}</span>
        <div class="pl-info">
          <div class="pl-title">${esc(t.titulo)}</div>
          <div class="pl-artist">${esc(t.artista)}</div>
        </div>
        <span class="pl-dur">${fmtTime(t.duracion_seg)}</span>
      </div>
    `).join("");

    // Doble click reproduce de inmediato
    el.querySelectorAll(".pl-item").forEach(item => {
      item.addEventListener("dblclick", () => playTrackById(parseInt(item.dataset.id)));
    });
  },

  renderHistory() {
    const el = $("#historyList");
    if (!UIState.history.length) {
      el.innerHTML = `<div class="empty-state-sm">Sin historial aún</div>`;
      return;
    }
    el.innerHTML = UIState.history.map(h => `
      <div class="hist-item">
        <span class="hist-time">${h.ts}</span>
        <span class="hist-title">${esc(h.titulo)} — ${esc(h.artista)}</span>
      </div>
    `).join("");
  },
};

// Limpiar cola
$("#btnClearQueue")?.addEventListener("click", () => {
  UIState.queue = [];
  Playlist.render();
});

/* ════════════════════════════════════════════════════════════
   CartucheraDual — Toggle Jingles/Publicidad + disparo
════════════════════════════════════════════════════════════ */
const CartucheraDual = (() => {
  let currentMode = "jingles";

  async function loadEffects() {
    try {
      const [efectos, cuñas] = await Promise.all([
        fetch(`${API}/api/v1/audios?tipo=Efecto&limit=8`).then(r => r.json()),
        fetch(`${API}/api/v1/audios?tipo=Cuña&limit=6`).then(r  => r.json()),
      ]);
      renderGrid("effectsGrid", efectos, "jingle");
      renderGrid("adsGrid",     cuñas,  "ad");

      // Actualizar la biblioteca en cache
      UIState.library["Efecto"] = efectos;
      UIState.library["Cuña"]   = cuñas;
    } catch (err) {
      console.warn("[Cartuchera] Error cargando efectos:", err);
    }
  }

  function renderGrid(gridId, tracks, colorClass) {
    const grid  = $(`#${gridId}`);
    if (!grid) return;
    const slots = Math.max(tracks.length, gridId === "effectsGrid" ? 8 : 6);
    grid.innerHTML = "";

    for (let i = 0; i < slots; i++) {
      const t   = tracks[i];
      const btn = document.createElement("button");
      btn.className = `effect-btn ${colorClass === "ad" ? "effect-ad " : ""}${t ? "" : "empty"}`;
      btn.dataset.slot = i + 1;

      const numLabel = colorClass === "ad" ? `A${i + 1}` : String(i + 1).padStart(2, "0");
      btn.innerHTML = `
        <span class="eff-num">${numLabel}</span>
        <span class="eff-name">${t ? esc(t.titulo) : "vacío"}</span>
      `;

      if (t) {
        btn.dataset.audioId = t.id;
        btn.addEventListener("click", () => fireEffect(t.id, btn, i));
      }
      grid.appendChild(btn);
    }
  }

  function fireEffect(audioId, btnEl, slotIndex) {
    btnEl.classList.add("firing");
    setTimeout(() => btnEl.classList.remove("firing"), 350);

    SolisWS.send({ cmd: "fire_effect", audio_id: audioId, canal_offset: slotIndex % 6 });
    fetch(`${API}/api/v1/effect/${audioId}?canal=${slotIndex % 6}`, { method: "POST" }).catch(() => {});
  }

  function logDisparo(audioId) {
    const list  = $("#effectsLogList");
    const track = [...UIState.library["Efecto"], ...UIState.library["Cuña"]]
                    .find(t => t.id === audioId);
    const name  = track?.titulo || `ID ${audioId}`;
    const now   = new Date().toTimeString().slice(0, 8);

    const li = document.createElement("li");
    li.innerHTML = `<span class="log-time">${now}</span><span>${esc(name)}</span>`;

    const empty = list.querySelector(".log-empty");
    if (empty) empty.remove();

    list.prepend(li);
    while (list.children.length > 8) list.lastChild?.remove();
  }

  // Toggle Jingles / Publicidad
  $$(".cart-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.mode;
      if (mode === currentMode) return;
      currentMode = mode;

      $$(".cart-mode-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");

      // Mostrar el panel correspondiente
      $("#cartPanelJingles").classList.toggle("active", mode === "jingles");
      $("#cartPanelAds").classList.toggle("active",     mode === "publicidad");
    });
  });

  loadEffects();

  return { logDisparo, reload: loadEffects };
})();

/* ════════════════════════════════════════════════════════════
   Autocomplete — Campo Artista del formulario de ingesta
════════════════════════════════════════════════════════════ */
const Autocomplete = (() => {
  const input    = $("#inputArtista");
  const dropdown = $("#artistaDropdown");
  if (!input) return {};

  let suggestions = [];
  let activeIdx   = -1;

  function normalize(s) { return s.trim().toLowerCase().replace(/\s+/g, " "); }

  function highlight(text, q) {
    if (!q) return esc(text);
    const idx = normalize(text).indexOf(normalize(q));
    if (idx === -1) return esc(text);
    return esc(text.slice(0, idx)) +
           `<span class="ac-highlight">${esc(text.slice(idx, idx + q.length))}</span>` +
           esc(text.slice(idx + q.length));
  }

  function render(q) {
    if (!suggestions.length) { close(); return; }
    dropdown.innerHTML = suggestions.map((s, i) =>
      `<li role="option" data-idx="${i}">${highlight(s, q)}</li>`
    ).join("");
    dropdown.classList.add("open");
    activeIdx = -1;

    dropdown.querySelectorAll("li").forEach(li => {
      li.addEventListener("mousedown", e => {
        e.preventDefault();
        select(parseInt(li.dataset.idx));
      });
    });
  }

  function close() { dropdown.classList.remove("open"); activeIdx = -1; }

  function select(idx) { input.value = suggestions[idx]; close(); }

  function setActive(idx) {
    const items = $$("li", dropdown);
    items.forEach(li => li.classList.remove("ac-active"));
    if (idx >= 0 && idx < items.length) {
      items[idx].classList.add("ac-active");
      items[idx].scrollIntoView({ block: "nearest" });
      activeIdx = idx;
    }
  }

  const fetch_ = debounce(async (q) => {
    if (q.length < 2) { close(); return; }
    try {
      const res = await fetch(`${API}/api/v1/audios/autocomplete?q=${encodeURIComponent(q)}`);
      suggestions = await res.json();
      render(q);

      if (suggestions.some(s => normalize(s) === normalize(q)) && suggestions.length === 1) {
        showToast("✓ Artista ya existe en la biblioteca", "success", 2000);
      }
    } catch {}
  }, 220);

  input.addEventListener("input",   () => fetch_(input.value.trim()));
  input.addEventListener("blur",    () => setTimeout(close, 160));
  input.addEventListener("keydown", e => {
    if (!dropdown.classList.contains("open")) return;
    const items = $$("li", dropdown);
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(activeIdx + 1, items.length - 1)); }
    if (e.key === "ArrowUp")   { e.preventDefault(); setActive(Math.max(activeIdx - 1, 0)); }
    if (e.key === "Enter"   )  { if (activeIdx >= 0) { e.preventDefault(); select(activeIdx); } }
    if (e.key === "Escape"  )  { close(); }
  });
})();

/* ════════════════════════════════════════════════════════════
   IngestaForm — Formulario de carga con drag-and-drop
════════════════════════════════════════════════════════════ */
const IngestaForm = (() => {
  const form      = $("#ingestaForm");
  const dropZone  = $("#fileDropZone");
  const fileInput = $("#fileInput");
  const feedback  = $("#ingestaFeedback");
  const eSlider   = $("#sliderEnergia");
  const eVal      = $("#energiaValue");

  if (!form) return;

  // Slider de energía
  eSlider?.addEventListener("input", () => { eVal.textContent = eSlider.value; });

  // Zona de drop
  dropZone.addEventListener("click", () => fileInput.click());

  dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.classList.add("dragover"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", e => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (file) { applyFile(file); }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) applyFile(fileInput.files[0]);
  });

  function applyFile(file) {
    // Crear una DataTransfer para asignar el archivo al input (workaround para drop)
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;

    // Mostrar nombre en la zona
    $("#dropContent").innerHTML = `
      <span class="drop-icon">✓</span>
      <span class="drop-text">
        <strong>${esc(file.name)}</strong>
        <small>archivo listo</small>
      </span>
    `;
    dropZone.style.borderColor = "var(--green-on)";

    // Auto-rellenar título/artista desde el nombre de archivo
    // Formato esperado: "Artista - Título.mp3"
    const name  = file.name.replace(/\.[^.]+$/, "");
    const parts = name.split(/\s*[-–—]\s*/);
    const artistaInput = $("#inputArtista");
    const tituloInput  = $("#inputTitulo");
    if (parts.length >= 2) {
      if (!artistaInput.value) artistaInput.value = parts[0].trim();
      if (!tituloInput.value)  tituloInput.value  = parts.slice(1).join(" - ").trim();
    } else if (!tituloInput.value) {
      tituloInput.value = name;
    }
  }

  // Submit
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = $("#btnIngestar");
    btn.disabled   = true;
    btn.textContent = "Procesando…";
    feedback.className  = "ingesta-feedback";
    feedback.style.display = "none";

    try {
      const res = await fetch(`${API}/api/v1/audios`, {
        method: "POST",
        body:   new FormData(form),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Error ${res.status}`);
      }
      const data = await res.json();
      feedback.className   = "ingesta-feedback success";
      // Construir la notificación con botón "Reproducir Ahora"
      // El botón es inline y llama directamente al motor de audio.
      // Esto permite estrenar una canción sin abrir la biblioteca.
      feedback.innerHTML = `
        <span class="fb-check">✓</span>
        <span class="fb-info">
          <strong>${esc(data.titulo)}</strong> de ${esc(data.artista)} — ID #${data.id}
        </span>
        <button class="btn-play-now" data-id="${data.id}" title="Reproducir ahora en el motor de audio">
          ▶ Reproducir Ahora
        </button>
      `;
      // Conectar el botón recién creado
      feedback.querySelector(".btn-play-now")?.addEventListener("click", () => {
        playTrackById(data.id);
        showToast(`▶ Iniciando estreno: ${data.titulo}`, "info", 3000);
      });

      showToast(`✓ Ingresado: ${data.titulo}`, "success");

      form.reset();
      eVal.textContent = "3";
      dropZone.style.borderColor = "";
      $("#dropContent").innerHTML = `
        <span class="drop-icon">🎙</span>
        <span class="drop-text">Arrastra el archivo aquí<small>MP3 · WAV · FLAC · OGG</small></span>
      `;

      // Invalidar caches de biblioteca y recargar
      // library_updated via WebSocket también lo hará, pero esta invalidación
      // local garantiza la actualización incluso si el WS está desconectado.
      const tipo = $("#selTipo").value;
      Library.invalidate(tipo);
      CartucheraDual.reload();

    } catch (err) {
      feedback.className   = "ingesta-feedback error";
      feedback.textContent = `✗ ${err.message}`;
      showToast(`Error: ${err.message}`, "error");
    } finally {
      btn.disabled    = false;
      btn.textContent = "⊕ INGESTAR AUDIO";
    }
  });
})();

/* ════════════════════════════════════════════════════════════
   ConfigManager — Configuración en caliente del motor
════════════════════════════════════════════════════════════ */
const ConfigManager = (() => {
  async function load() {
    try {
      const res = await fetch(`${API}/api/v1/config`);
      const cfg = await res.json();
      if (cfg.crossfade_seg)       $("#cfgCrossfade").value      = cfg.crossfade_seg;
      if (cfg.ventana_artista_min) $("#cfgVentana").value        = cfg.ventana_artista_min;
      if (cfg.max_mismo_genero)    $("#cfgMaxGenero").value      = cfg.max_mismo_genero;
      if (cfg.forzar_nacional)     $("#cfgForzarNacional").checked = cfg.forzar_nacional === "true";
    } catch {}
  }

  async function save() {
    const configs = {
      crossfade_seg:       $("#cfgCrossfade").value,
      ventana_artista_min: $("#cfgVentana").value,
      max_mismo_genero:    $("#cfgMaxGenero").value,
      forzar_nacional:     String($("#cfgForzarNacional").checked),
    };

    try {
      await Promise.all(
        Object.entries(configs).map(([clave, valor]) =>
          fetch(`${API}/api/v1/config/${clave}`, {
            method:  "PUT",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ valor }),
          })
        )
      );
      showToast("✓ Configuración guardada", "success");
    } catch {
      showToast("Error al guardar configuración", "error");
    }
  }

  $("#btnSaveConfig")?.addEventListener("click", save);
  load();
})();

/* ════════════════════════════════════════════════════════════
   Toggle de modo Manual / Automático
════════════════════════════════════════════════════════════ */
$("#modeToggle")?.addEventListener("change", e => {
  const mode = e.target.checked ? "automatico" : "manual";
  SolisWS.send({ cmd: "set_mode", mode });
  fetch(`${API}/api/v1/mode/${mode}`, { method: "POST" }).catch(() => {});
});

/* ════════════════════════════════════════════════════════════
   Controles de transporte del header
════════════════════════════════════════════════════════════ */
$("#btnStop")?.addEventListener("click", () => {
  SolisWS.send({ cmd: "stop" });
});
$("#btnSkip")?.addEventListener("click", () => {
  SolisWS.send({ cmd: "skip" });
});
$("#btnFadeNext")?.addEventListener("click", () => {
  SolisWS.send({ cmd: "fade_next" });
});

/* ════════════════════════════════════════════════════════════
   UTILIDAD: escape HTML para prevenir XSS al insertar texto
   de la base de datos en el DOM con innerHTML.
════════════════════════════════════════════════════════════ */
function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ════════════════════════════════════════════════════════════
   CSS dinámico — filtros y botón "Reproducir Ahora"
   Inyectado en runtime para no modificar index.html.
   Paleta Deep Sea: --choc-5 (#005f73 teal), --gold-0 (#ee9b00 amber)
════════════════════════════════════════════════════════════ */
(function injectStyles() {
  const style = document.createElement("style");
  style.textContent = `
    /* Botón Reproducir Ahora en notificación de ingesta */
    .btn-play-now {
      display: inline-flex;
      align-items: center;
      gap: .4em;
      padding: 4px 14px;
      background: var(--gold-0, #ee9b00);
      color: var(--choc-0, #001219);
      border: none;
      border-radius: 4px;
      font-family: var(--font-display, 'Bebas Neue', sans-serif);
      font-size: .85rem;
      letter-spacing: .07em;
      cursor: pointer;
      transition: 120ms ease;
      flex-shrink: 0;
    }
    .btn-play-now:hover { background: var(--gold-1, #f4a820); transform: scale(1.04); }

    /* Layout del feedback de ingesta con botón */
    .ingesta-feedback.success {
      display: flex;
      align-items: center;
      gap: .75rem;
      flex-wrap: wrap;
    }
    .fb-check { font-size: 1.2rem; flex-shrink: 0; }
    .fb-info  { flex: 1; min-width: 0; }

    /* Pills de género — misma paleta que las pills de origen */
    .filter-row { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
    .filter-row-label {
      font-size: .65rem;
      letter-spacing: .1em;
      color: var(--text-lo, #4a8f87);
      text-transform: uppercase;
      margin-right: .25rem;
    }
    .filter-pill[data-genero].active,
    .filter-pill[data-energia].active {
      background: var(--choc-5, #005f73);
      border-color: var(--gold-0, #ee9b00);
      color: var(--gold-0, #ee9b00);
    }

    /* Colorear las celdas de NAC/INT */
    .tag-nac { color: var(--mint, #94d2bd); }
    .tag-int { color: var(--amber, #ca6702); }

    /* Energía con color semántico */
    .td-e--1 { color: #52b788; }
    .td-e--2 { color: #7cd5a0; }
    .td-e--3 { color: var(--text-mono, #7cbfb5); }
    .td-e--4 { color: var(--amber, #ca6702); }
    .td-e--5 { color: var(--gold-0, #ee9b00); }

    /* Botón limpiar filtros */
    #btnClearFilters {
      font-size: .65rem;
      padding: 3px 10px;
      border: 1px dashed var(--choc-5, #005f73);
      border-radius: 3px;
      color: var(--text-lo, #4a8f87);
      background: none;
      cursor: pointer;
      transition: 120ms ease;
    }
    #btnClearFilters:hover { border-color: var(--gold-0, #ee9b00); color: var(--gold-0, #ee9b00); }
  `;
  document.head.appendChild(style);
})();

/* ════════════════════════════════════════════════════════════
   Ping periódico para mantener la conexión WebSocket viva
════════════════════════════════════════════════════════════ */
setInterval(() => SolisWS.send({ cmd: "ping" }), 25000);
