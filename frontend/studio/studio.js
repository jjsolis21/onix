/**
 * studio.js — ONIX FM · Studio (pantalla del operador, estilo Jazler SoHo)
 *
 * PRINCIPIO: la pantalla nunca "adivina". Todo sale del servidor:
 *   · /api/v1/status cada segundo (la cuenta regresiva se interpola entre medio)
 *   · /api/v1/programa al cambiar algo (evento programa_actualizado) y cada 15 s
 *   · eventos del WebSocket solo para reaccionar al instante
 * Si se pierde la conexión aparece un aviso rojo fijo; lo que suena no se corta
 * (el motor es otro proceso).
 */
"use strict";

/* ─────────────────────────── utilidades ─────────────────────────── */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = s => {
  s = Math.max(0, Math.round(Number(s) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
};
const norm = s => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();
const TIPO_NOMBRE = { Musica: "Música", Efecto: "Jingle", Sweeper: "Sweeper", "Cuña": "Cuña", Programa: "Programa", Tips: "Tips" };

function toast(msg, tipo = "info", ms = 5000) {
  const el = document.createElement("div");
  el.className = `toast ${tipo}`;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new Error(Array.isArray(b.detail) ? b.detail.map(d => d.msg).join("; ") : (b.detail || `HTTP ${r.status}`));
  }
  return r.status === 204 ? null : r.json();
}

/* ─────────────────────────── conexión ─────────────────────────── */
const WS = (() => {
  let ws = null, espera = 1000;
  const handlers = {};
  function conectar() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { espera = 1000; Estado.refrescarTodo(); };
    ws.onmessage = ({ data }) => {
      let m; try { m = JSON.parse(data); } catch { return; }
      (handlers[m.event ?? m.cmd] ?? []).forEach(fn => fn(m.data ?? {}));
    };
    ws.onclose = () => { setTimeout(conectar, espera); espera = Math.min(espera * 2, 15000); };
  }
  function send(p) {
    if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(p)); return true; }
    toast("Sin conexión con ONIX: el comando no se envió", "error");
    return false;
  }
  setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send('{"cmd":"ping"}'), 25000);
  conectar();
  return {
    send,
    on: (ev, fn) => (handlers[ev] ??= []).push(fn),
    off: (ev, fn) => { handlers[ev] = (handlers[ev] ?? []).filter(f => f !== fn); },
  };
})();

/* ─────────────────────────── reloj ─────────────────────────── */
(function reloj() {
  const tick = () => {
    const d = new Date();
    $("relojHora").textContent = d.toTimeString().slice(0, 8);
    $("relojFecha").textContent = d.toLocaleDateString("es", { weekday: "short", day: "numeric", month: "short" }).replace(/\./g, "");
  };
  tick(); setInterval(tick, 500);
})();

/* ─────────────────────────── estado del motor ─────────────────────────── */
const Estado = (() => {
  let st = null, recibido = 0, fallos = 0, programa = null;

  async function poll() {
    try {
      st = await api("/api/v1/status");
      recibido = performance.now();
      fallos = 0;
      $("avisoConexion").hidden = true;
      pintarEstatico();
    } catch {
      if (++fallos >= 2) $("avisoConexion").hidden = false;
      $("motorEstado").className = "motor-estado mal";
      $("motorEstado").querySelector(".txt").textContent = "SIN CONEXIÓN";
    }
  }

  async function cargarPrograma() {
    try {
      programa = await api("/api/v1/programa");
      Decks.pintar(programa);
      Derecha.tanda(programa, st);
      pintarSiguiente();
    } catch { /* el aviso de conexión ya lo muestra */ }
  }

  /** Posición actual interpolada entre dos respuestas del servidor */
  function posicion() {
    if (!st?.current_track || !st.is_playing || st.pausado) return st?.position_sec ?? 0;
    return (st.position_sec ?? 0) + (performance.now() - recibido) / 1000;
  }

  // La API y el motor (proceso aparte) informan su versión: si no coinciden,
  // el motor siguió corriendo con código viejo tras una actualización.
  // Sin version_api es el servidor el que quedó viejo (ventana anterior aún abierta).
  const motorViejo = () => !!st && (st.version_api === undefined || st.version !== st.version_api);

  /** Carátula del disco en un cuadro; sin carátula (o sin pista) queda la nota ♪ */
  function tapa(el, id) {
    const clave = id ? String(id) : "";
    if (el.dataset.id === clave) return;
    el.dataset.id = clave;
    el.textContent = "♪";
    if (!id) return;
    const img = new Image();
    img.onload = () => { if (el.dataset.id === clave) { el.textContent = ""; el.appendChild(img); } };
    img.src = `/api/v1/audios/${id}/caratula`;
  }

  function pintarEstatico() {
    $("avisoVersion").hidden = !motorViejo();
    if (motorViejo()) {
      $("avisoVersionTxt").textContent = st.version_api === undefined
        ? "el servidor sigue con el código anterior"
        : `motor ${st.version ?? "sin versión"} · servidor ${st.version_api}`;
    }
    // Barra superior
    const me = $("motorEstado");
    me.className = `motor-estado ${st.running ? "ok" : "mal"}`;
    me.querySelector(".txt").textContent = st.running ? "EN AIRE" : `MOTOR DETENIDO${st.last_error ? " · " + st.last_error : ""}`;
    me.title = st.running ? `Motor en marcha · ${String(st.backend).toUpperCase()}${st.remoto ? " · proceso aparte" : ""}` : "Motor de audio detenido";
    document.querySelectorAll(".modo-btn").forEach(b => b.classList.toggle("active", b.dataset.modo === st.modo));

    // Al aire
    const ct = st.current_track;
    const tipo = ct ? (ct.origen === "pauta" ? "Cuña" : ct.tipo_audio) : null;
    $("nowTipo").textContent = ct ? (TIPO_NOMBRE[tipo] ?? tipo) : "—";
    $("nowTipo").dataset.tipo = tipo ?? "";
    $("nowReloj").textContent = ct?.reloj ? `🕐 ${ct.reloj}` : (ct?.origen === "cola" ? "COLA" : "");
    // Memo de la lista para el locutor (Jazler "Memos")
    $("nowMemo").hidden = !ct?.nota;
    $("nowMemo").textContent = ct?.nota ? `📝 ${ct.nota}` : "";
    tapa($("nowTapa"), ct && ct.origen !== "pauta" ? ct.id : null);
    $("nowTitulo").textContent = ct ? ct.titulo : (st.retenido ? "Aire libre" : "Sin reproducción");
    $("nowArtista").textContent = ct ? (ct.cliente ? `📢 ${ct.cliente}` : ct.artista) : " ";
    const total = ct ? (ct.fin ?? st.duration_sec) : 0;
    $("nowTotal").textContent = fmt(total);
    const ini = ct?.cue_inicio ?? 0, largo = Math.max(1, total - ini);
    const marca = (el, seg) => {
      el.hidden = !(ct && seg && seg > ini && seg < total);
      if (!el.hidden) el.style.left = `${((seg - ini) / largo) * 100}%`;
    };
    marca($("marcaIntro"), ct?.cue_intro);
    marca($("marcaMezcla"), ct?.mezcla);
    marca($("marcaOutro"), ct?.cue_outro);

    // Controles y avisos
    $("btnDetener").classList.toggle("activo", !!st.detener_al_terminar);
    const bp = $("btnPausa"), pausa = !!st.pausado;
    bp.classList.toggle("activo", pausa);
    bp.disabled = !ct;
    bp.querySelector("small").textContent = pausa ? "REANUDAR" : "PAUSA";
    const ico = bp.querySelector(".ctl-ico");
    if (ico) ico.src = `/static/studio/icons/${pausa ? "play2" : "pause2"}.svg`;
    bp.querySelector(".glifo").textContent = pausa ? "▶" : "❚❚";
    document.body.classList.toggle("en-pausa", pausa);
    $("avisoRetenido").hidden = !st.retenido;
    const n = st.tanda_en_espera ?? 0;
    $("avisoTanda").hidden = n === 0;
    if (n) $("avisoTandaTxt").textContent = `📢 TANDA EN ESPERA — ${n} cuña(s). En manual sale cuando tú la lances.`;
    const cue = st.preescucha;
    $("avisoCue").hidden = !cue;
    if (cue) $("avisoCueTxt").textContent = `🎧 PREESCUCHA (no sale al aire): ${cue.titulo} · ${fmt(cue.posicion)} / ${fmt(cue.duracion)}`;
    Biblioteca.marcar(ct?.id, cue?.audio_id);
    Biblioteca.estadoCue(cue);
    Derecha.tanda(programa, st);
    pintarSiguiente();
  }

  let sinSiguiente = false;        // manual y sin nada en cola: al terminar queda aire libre
  function pintarSiguiente() {
    if (!st) return;
    const q = programa?.cola?.[0];
    const it = q ? null : (programa?.items ?? [])[0];
    const auto = st.modo === "automatico" || !!q;
    let tipo = "—", titulo = "—", artista = " ";
    if (q) { tipo = "COLA"; titulo = q.titulo; artista = q.artista; }
    else if (it?.tipo === "tanda") { tipo = "Tanda"; titulo = `TANDA :${String(it.minuto).padStart(2, "0")}`; artista = `${it.cunas} cuña(s) · ${fmt(it.duracion)}`; }
    else if (it) { tipo = it.tipo_audio; titulo = it.titulo; artista = it.artista; }
    else if (st.modo === "automatico") { titulo = "Rotación automática"; }
    sinSiguiente = st.modo === "manual" && !q && !it;
    if (sinSiguiente) { titulo = "Nada en cola"; artista = "Al terminar queda aire libre"; }
    tapa($("nextTapa"), q?.id ?? (it && it.tipo !== "tanda" ? it.audio_id : null));
    $("nextTipo").textContent = TIPO_NOMBRE[tipo] ?? tipo;
    $("nextTipo").dataset.tipo = tipo;
    $("nextTitulo").textContent = titulo;
    $("nextArtista").textContent = artista;
    const entra = $("nextEntra").parentElement;
    entra.classList.toggle("detenido", !!st.detener_al_terminar || !auto);
    $("nextEntraLbl").textContent = st.detener_al_terminar ? "NO ENTRA" : (sinSiguiente ? "TERMINA EN" : (auto ? "ENTRA EN" : "MANUAL"));
  }

  /** Cuentas regresivas: 4 veces por segundo, sin esperar al servidor */
  function animar() {
    if (st) {
      const ct = st.current_track;
      const pos = posicion();
      const total = ct ? (ct.fin ?? st.duration_sec) : 0;
      const restante = Math.max(0, total - pos);
      const cuenta = $("nowRestante");
      cuenta.textContent = ct ? fmt(restante) : "0:00";
      cuenta.classList.toggle("ultimos", !!ct && restante <= 10 && restante > 0);
      $("nowTranscurrido").textContent = fmt(pos - (ct?.cue_inicio ?? 0));
      const ini = ct?.cue_inicio ?? 0;
      $("nowFill").style.width = ct ? `${Math.min(100, ((pos - ini) / Math.max(1, total - ini)) * 100)}%` : "0%";

      // Intro: cuánto le queda al locutor para hablar encima
      const intro = ct?.cue_intro ?? 0, caja = $("nowIntro");
      if (ct && ct.tipo_audio === "Musica" && intro > 0 && pos < intro + 3) {
        const falta = intro - pos;
        caja.hidden = false;
        caja.classList.toggle("voz", falta <= 0);
        caja.querySelector("span").textContent = falta > 0 ? "INTRO" : "VOZ";
        $("nowIntroSeg").textContent = falta > 0 ? Math.ceil(falta) : "▶";
      } else {
        caja.hidden = true;
      }

      // Outro: cuánto falta para que termine la voz (30 s antes) y, ya
      // terminada, "HABLA" hasta la mezcla: el locutor puede entrar encima
      const outro = ct?.cue_outro ?? 0, cajaO = $("nowOutro");
      const finHabla = ct?.mezcla ?? total;
      if (ct && ct.tipo_audio === "Musica" && outro > 0 && pos >= outro - 30 && pos < Math.max(outro + 3, finHabla)
          && pos >= intro) {
        const falta = outro - pos;
        cajaO.hidden = false;
        cajaO.classList.toggle("voz", falta <= 0);
        cajaO.querySelector("span").textContent = falta > 0 ? "OUTRO" : "HABLA";
        $("nowOutroSeg").textContent = falta > 0 ? Math.ceil(falta) : "▶";
      } else {
        cajaO.hidden = true;
      }

      // Cuándo entra lo siguiente: en el punto de mezcla
      const mezcla = ct?.mezcla ?? total;
      $("nextEntra").textContent = st.detener_al_terminar ? "—"
        : (ct && (st.modo === "automatico" || programa?.cola?.length) ? fmt(Math.max(0, mezcla - pos))
          : (ct && sinSiguiente ? fmt(Math.max(0, total - pos)) : "—"));
      // Manual sin nada en cola: avisa en rojo que se acerca el aire libre
      $("nextEntra").parentElement.classList.toggle("alerta", !!(ct && sinSiguiente && !st.detener_al_terminar && total - pos <= 20));
    }
    Derecha.cuentaTanda();
    setTimeout(animar, 250);
  }

  async function refrescarTodo() {
    await Promise.allSettled([poll(), cargarPrograma(), Derecha.historial(), Derecha.cartuchera()]);
  }

  function iniciar() {
    setInterval(poll, 1000);
    setInterval(cargarPrograma, 15000);
    animar();
    refrescarTodo();
  }
  return { poll, cargarPrograma, refrescarTodo, iniciar, motorViejo, get st() { return st; } };
})();

/* ─────────────────────── adelantar lo que suena ───────────────────────
   Dos pasos a propósito: un clic accidental en la barra no debe saltar el
   audio que escucha la gente. Clic = marcar el punto · botón = confirmar.
   No se permite con una cuña de pauta (se acortaría lo que paga el cliente). */
const Salto = (() => {
  const barra = $("nowBarra"), hover = $("barraHover"), marca = $("marcaSalto"), boton = $("btnSaltar");
  let destino = null, vence = null;

  function geometria() {
    const st = Estado.st, ct = st?.current_track;
    if (!ct) return null;
    const ini = ct.cue_inicio ?? 0, fin = ct.fin ?? st.duration_sec;
    return { ct, ini, fin, largo: Math.max(1, fin - ini) };
  }
  const bloqueada = g => !g || g.ct.origen === "pauta" || g.ct.tipo_audio === "Cuña";
  function segundoEn(e, g) {
    const r = barra.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    return g.ini + f * g.largo;
  }
  function cancelar() {
    destino = null; clearTimeout(vence);
    marca.hidden = true; boton.hidden = true;
  }

  barra.addEventListener("mousemove", e => {
    const g = geometria();
    barra.classList.toggle("bloqueada", bloqueada(g));
    barra.title = !g ? "" : (bloqueada(g) ? "No se puede adelantar una cuña" : "Clic para elegir a dónde adelantar");
    if (bloqueada(g) || e.target === boton) { hover.hidden = true; return; }
    const seg = segundoEn(e, g);
    hover.hidden = false;
    hover.style.left = `${((seg - g.ini) / g.largo) * 100}%`;
    hover.querySelector("span").textContent = fmt(seg - g.ini);
  });
  barra.addEventListener("mouseleave", () => { hover.hidden = true; });
  barra.addEventListener("click", e => {
    if (e.target === boton) return;
    const g = geometria();
    if (bloqueada(g)) {
      if (g) toast("No se puede adelantar una cuña de pauta", "aviso", 3000);
      return;
    }
    destino = segundoEn(e, g);
    marca.hidden = false;
    marca.style.left = `${((destino - g.ini) / g.largo) * 100}%`;
    boton.textContent = `⏩ IR A ${fmt(destino - g.ini)}`;
    boton.hidden = false;
    clearTimeout(vence);
    vence = setTimeout(cancelar, 5000);
  });
  boton.addEventListener("click", e => {
    e.stopPropagation();
    if (destino === null) return;
    const objetivo = destino, pista = Estado.st?.current_track?.id;
    cancelar();
    saltar(objetivo, pista);
  });

  // Por REST y no por WebSocket: así la respuesta (o el error exacto) vuelve aquí.
  async function saltar(objetivo, pista) {
    let confirmado = false;
    const alConfirmar = () => { confirmado = true; };
    WS.on("seek", alConfirmar);
    try {
      await api("/api/v1/player/seek", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ posicion: objetivo }),
      });
    } catch (e) {
      WS.off("seek", alConfirmar);
      toast(`⚠ Salto rechazado: ${e.message}`, "error", 10000);
      return;
    }
    // Comprobar que el audio de verdad saltó: nunca fallar callado
    setTimeout(async () => {
      WS.off("seek", alConfirmar);
      await Estado.poll();
      const st = Estado.st;
      if (st?.current_track?.id !== pista || Math.abs((st.position_sec ?? 0) - objetivo) <= 4) return;
      if (Estado.motorViejo()) {
        toast("⚠ El salto no se aplicó: ONIX sigue con código anterior. Cierra TODAS las ventanas de ONIX y ábrelo con «Actualizar ONIX.bat».", "error", 12000);
      } else if (confirmado) {
        toast("⚠ El motor dice que saltó pero la posición no cambió. Copia las últimas líneas de la ventana del motor y envíalas.", "error", 12000);
      } else {
        toast("⚠ El motor no confirmó el salto. Revisa la ventana minimizada del motor (líneas «[Engine]» o «[BASS]»).", "error", 12000);
      }
    }, 1500);
  }
  document.addEventListener("keydown", e => { if (e.key === "Escape" && destino !== null) cancelar(); });
  return { cancelar };
})();

/* ─────────────────────────── controles ─────────────────────────── */
document.querySelectorAll(".modo-btn").forEach(b =>
  b.addEventListener("click", () => WS.send({ cmd: "set_mode", mode: b.dataset.modo })));
$("btnPlay").addEventListener("click", () =>
  WS.send(Estado.st?.pausado ? { cmd: "pausar", activo: false } : { cmd: "play" }));
$("btnPausa").addEventListener("click", () => {
  if (!Estado.st?.current_track) return;
  WS.send({ cmd: "pausar", activo: !Estado.st.pausado });
  setTimeout(Estado.poll, 150);
});
$("btnNext").addEventListener("click", () => WS.send({ cmd: "skip" }));
$("btnDetener").addEventListener("click", () => {
  const activo = !Estado.st?.detener_al_terminar;
  WS.send({ cmd: "detener_al_terminar", activo });
  setTimeout(Estado.poll, 150);
});
$("btnStop").addEventListener("click", () => {
  if (!Estado.st?.current_track) return;
  if (confirm("¿Cortar el aire AHORA?\n\nSi solo quieres hablar al final de la canción, usa DETENER AL TERMINAR.")) {
    WS.send({ cmd: "stop" });
  }
});
$("btnTandaAhora").addEventListener("click", () => WS.send({ cmd: "lanzar_tanda", ahora: true }));
$("btnTandaLuego").addEventListener("click", () => {
  WS.send({ cmd: "lanzar_tanda", ahora: false });
  toast("La tanda sale al terminar la canción", "aviso", 3000);
});
$("btnCueStop").addEventListener("click", () => WS.send({ cmd: "cue_stop" }));
$("btnRegenerar").addEventListener("click", async () => {
  if (!confirm("¿Volver a planificar el programa desde ahora? Se pierden los cambios hechos a mano.")) return;
  await api("/api/v1/programa/regenerar", { method: "POST" });
  setTimeout(Estado.cargarPrograma, 400);
});

/* ─────────────────────────── decks (programa) ─────────────────────────── */
const Decks = (() => {
  const lista = $("decksLista");
  let items = [], sel = null;     // sel = uid de la tarjeta elegida

  function pintar(prog) {
    items = prog?.items ?? [];
    const cola = prog?.cola ?? [];
    // Duración total de lo que está en cola + programa (como "Duración del Playlist")
    const total = [...cola, ...items].reduce((s, it) => s + (+(it.duracion ?? it.duracion_total) || 0), 0);
    const hms = t => `${Math.floor(t / 3600)}:${String(Math.floor(t % 3600 / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
    $("decksFin").textContent = (prog?.fin ? `hasta ${prog.fin.slice(0, 5)}` : "") + (total ? ` · ${hms(total)}` : "");
    if (!cola.length && !items.length) {
      lista.innerHTML = Estado.st?.modo === "manual"
        ? `<div class="vacio"><b>MODO MANUAL</b> — la automatización se borró.<br>Arma tu programa: doble clic o arrastra canciones desde la biblioteca.</div>`
        : `<div class="vacio">Nada programado.<br>Arrastra canciones aquí desde la biblioteca.</div>`;
      return;
    }
    let html = "";
    if (cola.length) {
      html += `<div class="deck-sep"><i style="background:var(--cyan)"></i>Cola del operador</div>`;
      html += cola.map(q => `<div class="deck cola">
        <span class="deck-hora">${esc(q.hora.slice(0, 5))}</span>
        <div class="deck-txt"><div class="deck-tit">${esc(q.titulo)}</div><div class="deck-art">${esc(q.artista)}</div>
          ${q.nota ? `<div class="deck-memo" title="Memo para el locutor">📝 ${esc(q.nota)}</div>` : ""}</div>
        <span class="deck-dur">COLA</span></div>`).join("");
    }
    let reloj;
    items.forEach((it, i) => {
      // Separador al cambiar de reloj o al empezar/terminar un evento a hora fija
      const grupo = it.evento_key || it.reloj;
      if (it.tipo === "audio" && grupo !== reloj && !it.operador) {
        reloj = grupo;
        html += it.evento
          ? `<div class="deck-sep evento"><i style="background:${esc(it.color || "var(--txt-lo)")}"></i>⏰ ${esc((it.evento_hora || "").slice(11, 16))} · ${esc(it.evento)}${it.inmediato ? " · ⚡ EN PUNTO" : ""}</div>`
          : `<div class="deck-sep"><i style="background:${esc(it.color || "var(--txt-lo)")}"></i>${it.reloj ? esc(it.reloj) : "Solo música (sin reloj)"}</div>`;
      }
      if (it.tipo === "tanda") {
        const marcas = (it.inmediata ? " ⚡ EN PUNTO" : "") + (it.excede ? " ⚠ LARGA" : "");
        html += `<div class="deck tanda${it.uid === sel ? " sel" : ""}" draggable="true" data-i="${i}" data-uid="${it.uid}" data-tipo="Tanda">
          <span class="deck-hora">${esc(it.hora.slice(0, 5))}</span>
          <div class="deck-txt"><div class="deck-tit">📢 TANDA :${String(it.minuto).padStart(2, "0")}${marcas}</div>
            <div class="deck-art">${it.cunas} cuña(s)</div></div>
          <span class="deck-dur">${fmt(it.duracion)}</span></div>`;
        return;
      }
      const marcas = [it.compensacion ? "⚖" : "", it.cortada ? "✂" : "", it.operador ? "✋" : ""].join(" ").trim();
      html += `<div class="deck${it.uid === sel ? " sel" : ""}" draggable="true" data-i="${i}" data-uid="${it.uid}" data-tipo="${esc(it.tipo_audio)}"
          title="${it.compensacion ? "⚖ elegida para que la tanda caiga a su hora. " : ""}${it.cortada ? "✂ se corta para que la tanda o el evento salga en punto. " : ""}${it.operador ? "✋ agregada por el operador." : ""}">
        <span class="deck-hora">${esc(it.hora.slice(0, 5))}</span>
        <div class="deck-txt"><div class="deck-tit">${esc(it.titulo)}</div>
          <div class="deck-art">${esc(TIPO_NOMBRE[it.tipo_audio] && it.tipo_audio !== "Musica" ? TIPO_NOMBRE[it.tipo_audio] + " · " : "")}${esc(it.artista)}</div></div>
        <span class="deck-dur">${fmt(it.duracion_total)}<br><span class="deck-marcas">${marcas}</span></span>
      </div>`;
    });
    lista.innerHTML = html;
    if (sel !== null && !items.some(x => x.uid === sel)) sel = null;   // ya sonó o se quitó
    barra();
  }

  // ── Selección + barra de herramientas fija (como Jazler SoHo) ──
  const indiceSel = () => items.findIndex(x => x.uid === sel);
  function barra() {
    const i = indiceSel(), it = items[i];
    $("dkSubir").disabled = !it || i === 0;
    $("dkBajar").disabled = !it || i === items.length - 1;
    $("dkPrimero").disabled = !it || i === 0;
    $("dkCue").disabled = !it || it.tipo !== "audio";
    $("dkQuitar").disabled = !it;
  }
  function elegir(uid, enfocar = true) {
    sel = uid;
    lista.querySelectorAll(".deck.sel").forEach(d => d.classList.remove("sel"));
    const d = lista.querySelector(`.deck[data-uid="${uid}"]`);
    d?.classList.add("sel");
    if (d && enfocar) d.scrollIntoView({ block: "nearest" });
    barra();
  }
  function mover(posicion) {
    const i = indiceSel();
    if (i < 0) return;
    posicion = Math.max(0, Math.min(items.length - 1, posicion));
    if (posicion === i) return;
    // Respuesta inmediata en pantalla; el servidor confirma con programa_actualizado
    const [it] = items.splice(i, 1);
    items.splice(posicion, 0, it);
    WS.send({ cmd: "plan_mover", uid: it.uid, posicion });
  }
  function quitar() {
    const i = indiceSel();
    if (i < 0) return;
    WS.send({ cmd: "plan_quitar", uid: sel });
    sel = items[i + 1]?.uid ?? items[i - 1]?.uid ?? null;   // la selección pasa a la vecina
  }

  lista.addEventListener("click", e => {
    const d = e.target.closest(".deck[data-uid]");
    if (d) { elegir(+d.dataset.uid, false); lista.focus({ preventScroll: true }); }
  });
  lista.addEventListener("dblclick", e => {
    const d = e.target.closest(".deck[data-uid]");
    const it = d && items.find(x => x.uid === +d.dataset.uid);
    if (it?.tipo === "audio") WS.send({ cmd: "cue", audio_id: it.audio_id });
  });
  $("dkSubir").addEventListener("click", () => mover(indiceSel() - 1));
  $("dkBajar").addEventListener("click", () => mover(indiceSel() + 1));
  $("dkPrimero").addEventListener("click", () => mover(0));
  $("dkCue").addEventListener("click", () => {
    const it = items[indiceSel()];
    if (it?.tipo === "audio") WS.send({ cmd: "cue", audio_id: it.audio_id });
  });
  $("dkQuitar").addEventListener("click", quitar);
  // Tras usar la barra, el foco vuelve al programa para seguir con el teclado
  document.querySelectorAll(".decks-barra button").forEach(b =>
    b.addEventListener("click", () => lista.focus({ preventScroll: true })));

  // Teclado (con el foco en el panel del programa, también tras usar su barra):
  // ↑/↓ elegir · Alt+↑/↓ mover · Supr quitar · Esc soltar la selección
  lista.closest(".decks").addEventListener("keydown", e => {
    if (!items.length) return;
    const i = indiceSel();
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const delta = e.key === "ArrowUp" ? -1 : 1;
      if (e.altKey) { mover(i + delta); return; }
      const j = i < 0 ? 0 : Math.max(0, Math.min(items.length - 1, i + delta));
      elegir(items[j].uid);
    } else if (e.key === "Delete" && i >= 0) {
      e.preventDefault();
      quitar();
    } else if (e.key === "Escape") {
      sel = null; lista.querySelectorAll(".deck.sel").forEach(d => d.classList.remove("sel")); barra();
    }
  });

  // ── Arrastrar y soltar ──
  let arrastrado = null;
  lista.addEventListener("dragstart", e => {
    const d = e.target.closest(".deck[data-uid]");
    if (!d) return;
    arrastrado = d;
    d.classList.add("arrastrando");
    e.dataTransfer.setData("onix/uid", d.dataset.uid);
    e.dataTransfer.effectAllowed = "move";
  });
  lista.addEventListener("dragend", () => { arrastrado?.classList.remove("arrastrando"); arrastrado = null; limpiar(); });

  function limpiar() {
    lista.querySelectorAll(".drop-antes,.drop-despues").forEach(x => x.classList.remove("drop-antes", "drop-despues"));
    lista.classList.remove("drop-final");
  }
  /** Posición (índice del programa) donde caería lo que se suelta */
  function destino(e) {
    const d = e.target.closest(".deck[data-i]");
    if (!d) return { idx: items.length, el: null, antes: false };
    const r = d.getBoundingClientRect();
    const antes = e.clientY < r.top + r.height / 2;
    return { idx: +d.dataset.i + (antes ? 0 : 1), el: d, antes };
  }
  lista.addEventListener("dragover", e => {
    const tipos = e.dataTransfer.types;
    if (!tipos.includes("onix/uid") && !tipos.includes("onix/audio")) return;
    e.preventDefault();
    limpiar();
    const { el, antes } = destino(e);
    if (el) el.classList.add(antes ? "drop-antes" : "drop-despues");
    else lista.classList.add("drop-final");
  });
  lista.addEventListener("dragleave", e => { if (!lista.contains(e.relatedTarget)) limpiar(); });
  lista.addEventListener("drop", e => {
    e.preventDefault();
    const { idx } = destino(e);
    limpiar();
    const uid = e.dataTransfer.getData("onix/uid");
    const audio = e.dataTransfer.getData("onix/audio");
    if (uid) {
      const desde = items.findIndex(x => String(x.uid) === uid);
      const pos = desde >= 0 && desde < idx ? idx - 1 : idx;   // al sacarlo, lo de abajo sube uno
      if (pos !== desde) WS.send({ cmd: "plan_mover", uid: +uid, posicion: pos });
    } else if (audio) {
      WS.send({ cmd: "plan_insertar", audio_id: +audio, posicion: idx });
    }
  });

  return { pintar };
})();

/* ─────────────────────────── biblioteca ─────────────────────────── */
const Biblioteca = (() => {
  // Jingles, sweepers y tips comparten pestaña ("SEPARADORES"): se separan con el filtro Tipo
  const IMAGEN = ["Efecto", "Sweeper", "Tips"];
  const NOMBRE_TIPO = { Efecto: "JINGLE", Sweeper: "SWEEPER", Tips: "TIP" };
  const TABS = [["Musica", "MÚSICA"], ["Imagen", "SEPARADORES"], ["Cuña", "CUÑAS"],
                ["Programa", "PROGRAMAS"], ["Playlist", "LISTAS"]];
  const enTab = (a, t) => t === "Imagen" ? IMAGEN.includes(a.tipo_audio) : a.tipo_audio === t;
  // La biblioteca NO se baja entera: el servidor busca y filtra (hay decenas de miles de audios)
  // y el Studio pide 100 filas por vez, y más al llegar al final de la lista.
  const PAGINA = 100;
  let filas = [], totalFiltro = 0, conteos = {}, gen = 0, cargandoMas = false;
  const cache = new Map();                       // todo audio visto: para títulos de cola/aire y el reproductor
  let tab = "Musica", alAire = null, enCue = null, playlists = [];
  const num = n => n.toLocaleString("es");
  /** Contador de dos líneas: el número arriba y debajo "ARCHIVOS" (o "DE 15.000" si hay un filtro) */
  function cuenta(n, total, nombre) {
    $("libCuenta").innerHTML = `<b>${num(n)}</b><small>${n === total ? nombre : `de ${num(total)}`}</small>`;
  }
  const P = { id: null, pos: 0, activo: false, recibido: 0, dur: 0, pausado: false };

  // Playlists generadas en Admin › Relojes desde el patrón de un reloj
  async function cargarPlaylists() {
    try { playlists = await api("/api/v1/playlists"); } catch (_) { playlists = []; }
  }
  const plAbiertas = new Set(), plItems = {};
  function filasDeLista(id) {
    const items = plItems[id];
    if (!items) return `<tr class="pl-det"><td colspan="6" class="vacio">Cargando…</td></tr>`;
    if (!items.length) return `<tr class="pl-det"><td colspan="6" class="vacio">La lista está vacía</td></tr>`;
    return items.map((i, n) => `
      <tr class="pl-det" data-id="${i.audio_id}" draggable="true">
        <td><div class="lib-tit"><span class="pl-n">${n + 1}</span>${esc(i.titulo)}</div><div class="lib-art">${esc(i.artista)}${i.nota ? ` · <em class="pl-nota">📝 ${esc(i.nota)}</em>` : ""}</div></td>
        <td>${esc(i.rotacion ?? "")}</td><td></td>
        <td class="num">${i.cue_intro ? Math.round(i.cue_intro) + "s" : ""}</td>
        <td class="num">${fmt(i.duracion)}</td>
        <td class="lib-acc">
          <button data-cue="${i.audio_id}" class="${i.audio_id === enCue ? "cue-on" : ""}" title="Preescuchar">${i.audio_id === enCue ? "■" : "🎧"}</button>
          <button data-cola="${i.audio_id}" title="Agregar a la cola">+ COLA</button>
        </td></tr>`).join("");
  }
  async function alternarLista(id) {
    if (plAbiertas.has(id)) { plAbiertas.delete(id); pintar(); return; }
    plAbiertas.add(id); pintar();
    if (!plItems[id]) {
      try { plItems[id] = (await api(`/api/v1/playlists/${id}`)).items; } catch (_) { plItems[id] = []; }
      pintar();
    }
  }
  function pintarPlaylists(q) {
    const lista = playlists.filter(p => !q || norm(`${p.nombre} ${p.reloj ?? ""}`).includes(q));
    $("libFilas").innerHTML = lista.length ? lista.map(p => `
      <tr data-pl="${p.id}" class="pl-cab ${plAbiertas.has(p.id) ? "abierta" : ""}" title="Clic para ver los archivos de la lista">
        <td><div class="lib-tit"><span class="pl-flecha">${plAbiertas.has(p.id) ? "▾" : "▸"}</span>${esc(p.nombre)}</div><div class="lib-art">${esc(p.reloj ? `Reloj ${p.reloj}` : "")} · ${esc((p.created_at || "").slice(0, 10))}</div></td>
        <td>${p.horas ? `${p.horas} h` : ""}</td>
        <td>${p.audios} audios</td>
        <td class="num"></td>
        <td class="num">${Math.floor(p.duracion / 3600)}:${String(Math.round(p.duracion % 3600 / 60)).padStart(2, "0")} h</td>
        <td class="lib-acc">
          <button data-plcola="${p.id}" title="Agregar todos sus audios al final de la cola">+ COLA</button>
          <button data-plreemp="${p.id}" class="aire" title="Vaciar la cola y poner esta lista">▶ LANZAR</button>
        </td></tr>${plAbiertas.has(p.id) ? filasDeLista(p.id) : ""}`).join("")
      : `<tr><td colspan="6" class="vacio">${playlists.length ? "Sin resultados" : "No hay listas. Créalas en Admin › Listas."}</td></tr>`;
    cuenta(lista.length, playlists.length, "listas");
    $("libInfo").textContent = "Clic en una lista para ver sus archivos";
  }
  async function lanzarPlaylist(id, reemplazar) {
    const p = playlists.find(x => x.id === id);
    if (reemplazar && !confirm(`¿Lanzar "${p?.nombre}"?\n\nSe vacía la cola del operador y entran sus ${p?.audios} audios.` +
                               `\nSonarán antes que el programa automático (las tandas siguen a su hora).`)) return;
    try {
      const r = await api(`/api/v1/playlists/${id}/lanzar`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reemplazar }) });
      toast(`▶ ${p?.nombre}: ${r.encoladas} audios a la cola`, "info", 3000);
    } catch (err) { toast(`No se pudo lanzar: ${err.message}`, "error"); }
  }

  /** Totales por tipo y valores de los filtros (poco datos: no depende del tamaño de la biblioteca) */
  async function cargar() {
    try {
      const [c, f] = await Promise.all([api("/api/v1/biblioteca/conteos"), api("/api/v1/biblioteca/filtros?tipo=Musica")]);
      conteos = c;
      const opciones = (sel, valores, nombre) => {
        const prev = $(sel).value;
        $(sel).innerHTML = `<option value="">${nombre}</option>` + valores.map(v => `<option>${esc(v)}</option>`).join("");
        $(sel).value = valores.includes(prev) ? prev : "";
      };
      opciones("libCat1", f.cat1 ?? [], "Género");
      opciones("libCat2", f.cat2 ?? [], "Rotación");
      opciones("libVoz", f.voz ?? [], "Voz");
    } catch (err) {
      $("libFilas").innerHTML = `<tr><td colspan="6" class="vacio">No se pudo cargar la biblioteca: ${esc(err.message)}</td></tr>`;
      return;
    }
    await cargarPlaylists();
    pintarTabs();
    pintar();
  }

  /** Parámetros de la consulta según la pestaña y los filtros */
  function consulta(offset) {
    const p = new URLSearchParams({ limit: PAGINA, offset });
    const q = $("libBuscar").value.trim();
    if (q) p.set("q", q);
    if (tab === "Imagen") p.set("tipo", $("libTipo").value || IMAGEN.join(","));
    else p.set("tipo", tab);
    if (tab === "Musica") {
      if ($("libCat1").value) p.set("cat1", $("libCat1").value);
      if ($("libCat2").value) p.set("cat2", $("libCat2").value);
      if ($("libVoz").value) p.set("voz", $("libVoz").value);
    }
    return `/api/v1/audios?${p}`;
  }
  const totalDeTab = () => tab === "Imagen" ? IMAGEN.reduce((s, t) => s + (conteos[t] ?? 0), 0) : (conteos[tab] ?? 0);

  /** Pide la primera página de lo que se está buscando (las respuestas viejas se descartan) */
  async function recargar() {
    const mia = ++gen;
    $("libFilas").innerHTML = `<tr><td colspan="6" class="vacio">Buscando…</td></tr>`;
    try {
      const r = await api(consulta(0));
      if (mia !== gen) return;
      filas = r.data ?? []; totalFiltro = r.total ?? filas.length;
      filas.forEach(a => cache.set(a.id, a));
    } catch (err) {
      if (mia === gen) $("libFilas").innerHTML = `<tr><td colspan="6" class="vacio">No se pudo buscar: ${esc(err.message)}</td></tr>`;
      return;
    }
    $("libFilas").closest(".lib-tabla-wrap").scrollTop = 0;
    pintarFilas();
  }
  async function masFilas() {
    if (cargandoMas || tab === "Playlist" || filas.length >= totalFiltro) return;
    cargandoMas = true;
    const mia = gen;
    try {
      const r = await api(consulta(filas.length));
      if (mia !== gen) return;
      const nuevas = r.data ?? [];
      nuevas.forEach(a => cache.set(a.id, a));
      filas = filas.concat(nuevas);
      $("libFilas").querySelector(".lib-mas")?.remove();
      $("libFilas").insertAdjacentHTML("beforeend", nuevas.map(htmlFila).join(""));
      pintarCuenta();
    } catch { /* se reintenta al seguir bajando */ }
    finally { cargandoMas = false; }
  }

  function pintarTabs() {
    $("libTabs").innerHTML = TABS.map(([t, n]) =>
      `<button class="lib-tab ${t === tab ? "active" : ""}" data-tab="${t}">${n}</button>`).join("");
    const esMusica = tab === "Musica";
    ["libCat1", "libCat2", "libVoz"].forEach(id => { $(id).hidden = !esMusica; });
    $("libTipo").hidden = tab !== "Imagen";
  }

  function pintar() {
    const q = norm($("libBuscar").value.trim());
    if (tab === "Playlist") { pintarPlaylists(q); return; }
    recargar();
  }

  function htmlFila(a) {
    return `
      <tr draggable="true" data-id="${a.id}" class="${a.id === alAire ? "al-aire" : ""} ${a.id === P.id ? "elegida" : ""}">
        <td><div class="lib-tit">${esc(a.titulo)}</div><div class="lib-art">${tab === "Imagen" ? `<em class="lib-tipo">${NOMBRE_TIPO[a.tipo_audio] ?? ""}</em> ` : ""}${esc(a.artista)}</div></td>
        <td>${esc(a.cat2 ?? "")}</td>
        <td>${esc(a.voz ?? "")}</td>
        <td class="num">${a.cue_intro ? Math.round(a.cue_intro) + "s" : ""}</td>
        <td class="num">${fmt(a.duracion)}</td>
        <td class="lib-acc">
          <button data-cue="${a.id}" class="${a.id === enCue ? "cue-on" : ""}" title="Preescuchar (salida interna)">${a.id === enCue ? "■" : "🎧"}</button>
          <button data-cola="${a.id}" title="Agregar a la cola">+ COLA</button>
          <button data-aire="${a.id}" class="aire" title="Poner al aire ahora">▶ AIRE</button>
        </td></tr>`;
  }
  function pintarFilas() {
    $("libFilas").innerHTML = filas.length ? filas.map(htmlFila).join("")
      : `<tr><td colspan="6" class="vacio">Sin resultados</td></tr>`;
    pintarCuenta();
  }
  /** El total va dentro de la sección, junto al buscador (no en las pestañas) */
  function pintarCuenta() {
    const deTab = totalDeTab();
    cuenta(totalFiltro, deTab, "archivos");
    $("libInfo").textContent = filas.length < totalFiltro ? `Mostrando ${num(filas.length)}: sigue bajando para ver más` : "";
  }
  // Al llegar casi al final de la lista se piden las siguientes 100
  $("libFilas").closest(".lib-tabla-wrap").addEventListener("scroll", e => {
    const w = e.currentTarget;
    if (w.scrollTop + w.clientHeight >= w.scrollHeight - 300) masFilas();
  });

  /** Marca la fila al aire y la que suena en preescucha, sin rehacer la lista */
  function marcar(aireId, cueId) {
    if (aireId === alAire && cueId === enCue) return;
    alAire = aireId ?? null; enCue = cueId ?? null;
    document.querySelectorAll("#libFilas tr[data-id]").forEach(tr => {
      const id = +tr.dataset.id;
      tr.classList.toggle("al-aire", id === alAire);
      const b = tr.querySelector("button[data-cue]");
      if (b) { b.classList.toggle("cue-on", id === enCue); b.textContent = id === enCue ? "■" : "🎧"; }
    });
  }

  $("libTabs").addEventListener("click", e => {
    const b = e.target.closest(".lib-tab");
    if (!b) return;
    tab = b.dataset.tab;
    if (tab === "Playlist") cargarPlaylists().then(() => { pintarTabs(); pintar(); });
    else { pintarTabs(); pintar(); }
  });
  let t;
  $("libBuscar").addEventListener("input", () => { clearTimeout(t); t = setTimeout(pintar, 250); });
  ["libCat1", "libCat2", "libVoz", "libTipo"].forEach(id => $(id).addEventListener("change", pintar));

  $("libFilas").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.plcola) { lanzarPlaylist(+b.dataset.plcola, false); return; }
    if (b.dataset.plreemp) { lanzarPlaylist(+b.dataset.plreemp, true); return; }
    const a = cache.get(+(b.dataset.cue || b.dataset.cola || b.dataset.aire));
    if (b.dataset.cue) WS.send(enCue === +b.dataset.cue ? { cmd: "cue_stop" } : { cmd: "cue", audio_id: +b.dataset.cue });
    if (b.dataset.cola) { WS.send({ cmd: "queue", audio_id: +b.dataset.cola }); toast(`+ Cola: ${a?.titulo ?? ""}`, "info", 2000); }
    if (b.dataset.aire) {
      const sonando = Estado.st?.current_track;
      if (!sonando || confirm(`¿Poner "${a?.titulo}" al aire AHORA?\n\nLo que suena sale con crossfade.`)) {
        WS.send({ cmd: "play", audio_id: +b.dataset.aire });
      }
    }
  });
  $("libFilas").addEventListener("dblclick", e => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr || e.target.closest("button")) return;
    WS.send({ cmd: "queue", audio_id: +tr.dataset.id });
    toast(`+ Cola: ${cache.get(+tr.dataset.id)?.titulo ?? ""}`, "info", 2000);
  });
  $("libFilas").addEventListener("dragstart", e => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    e.dataTransfer.setData("onix/audio", tr.dataset.id);
    e.dataTransfer.effectAllowed = "copy";
  });

  /* ── Reproductor de preescucha: manda al motor (salida interna, nunca al aire) ── */
  const mmss = t => { t = Math.max(0, Math.floor(t || 0)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };
  const audioDe = id => cache.get(id);
  function elegir(id, titulo) {
    if (P.id === id) return;
    P.id = id; P.pos = 0; P.pausado = false;
    const a = audioDe(id);
    P.dur = a?.duracion ?? 0;
    $("spTitulo").textContent = a?.titulo ?? titulo ?? "";
    $("spArtista").textContent = a?.artista ?? "";
    document.querySelectorAll("#libFilas tr[data-id]").forEach(tr => tr.classList.toggle("elegida", +tr.dataset.id === id));
    pintarPlayer();
  }
  const posP = () => P.activo ? P.pos + (performance.now() - P.recibido) / 1000 : P.pos;
  function pintarPlayer() {
    const pos = Math.min(posP(), P.dur || Infinity);
    $("spPos").textContent = mmss(pos);
    $("spDur").textContent = `/ ${mmss(P.dur)}`;
    $("spResta").textContent = P.dur ? `−${mmss(P.dur - pos)}` : "";
    $("spFill").style.width = P.dur ? `${Math.min(100, pos / P.dur * 100)}%` : "0";
    $("spPlayer").classList.toggle("sonando", P.activo);
    const img = `/static/studio/icons/${P.activo ? "pause2" : "play2"}.svg`;
    const ico = $("spPlay").querySelector("img");
    if (!ico.src.endsWith(img)) ico.src = img;
  }
  /** Lo llama el sondeo del estado con st.preescucha */
  function estadoCue(cue) {
    if (cue) {
      if (P.id !== cue.audio_id) elegir(cue.audio_id, cue.titulo);
      Object.assign(P, { activo: true, pausado: false, pos: cue.posicion, dur: cue.duracion || P.dur, recibido: performance.now() });
    } else if (P.activo) {
      // Terminó solo (o lo paró otro): si no fue la pausa del operador, vuelve al inicio
      P.activo = false;
      if (!P.pausado) P.pos = 0;
    }
    pintarPlayer();
  }
  function irA(seg) {
    if (!P.id) return;
    seg = Math.max(0, Math.min(seg, Math.max(0, (P.dur || seg) - 0.5)));
    P.pos = seg; P.recibido = performance.now();
    if (P.activo) WS.send({ cmd: "cue", audio_id: P.id, desde: seg });
    pintarPlayer();
  }
  function reproducirOPausar() {
    if (!P.id) { toast("Elige un audio de la lista (clic en la fila)", "info", 2000); return; }
    if (P.activo) {
      P.pos = posP(); P.pausado = true; P.activo = false;
      WS.send({ cmd: "cue_stop" });
    } else {
      P.pausado = false;
      WS.send({ cmd: "cue", audio_id: P.id, desde: P.pos >= (P.dur || Infinity) - 0.5 ? 0 : P.pos });
    }
    pintarPlayer();
  }
  $("spPlay").addEventListener("click", reproducirOPausar);
  $("spStop").addEventListener("click", () => {
    P.pos = 0; P.pausado = false;
    if (P.activo) { P.activo = false; WS.send({ cmd: "cue_stop" }); }
    pintarPlayer();
  });
  $("spAtras").addEventListener("click", () => irA(posP() - 5));
  $("spAdelante").addEventListener("click", () => irA(posP() + 5));
  $("spProg").addEventListener("click", e => {
    const r = $("spProg").getBoundingClientRect();
    irA((e.clientX - r.left) / r.width * (P.dur || 0));
  });
  setInterval(() => { if (P.activo) pintarPlayer(); }, 250);
  // Un clic en la fila la deja lista en el reproductor
  $("libFilas").addEventListener("click", e => {
    if (e.target.closest("button")) return;
    const tr = e.target.closest("tr[data-id]");
    if (tr) { elegir(+tr.dataset.id); return; }
    const cab = e.target.closest("tr[data-pl]");
    if (cab) alternarLista(+cab.dataset.pl);
  });

  cargar();
  return { cargar, marcar, estadoCue };
})();

/* ─────────────────────────── derecha ─────────────────────────── */
const Derecha = (() => {
  let proxima = null, bancos = {}, slots = {}, nombres = {}, banco = "jingles";
  // Qué se ve en el panel de la derecha: "hist" (historial) o la clave de una paleta
  let vista = "hist";
  try { vista = localStorage.getItem("onix_vista_der") || "hist"; } catch { /* sin almacenamiento */ }
  function pintarVista() {
    const claves = Object.keys(bancos);
    if (vista !== "hist") banco = vista;
    $("derTabs").innerHTML = `<button class="der-tab ${vista === "hist" ? "active" : ""}" data-vista="hist">HISTORIAL</button>`
      + claves.map(b => `<button class="der-tab ${b === vista ? "active" : ""}" data-vista="${esc(b)}" title="Paleta de botones">${esc((nombres[b] || b).toUpperCase())}</button>`).join("");
    $("historial").hidden = vista !== "hist";
    $("cartuchera").hidden = vista === "hist";
    if (vista !== "hist") pintarCart();
  }

  function tanda(prog, st) {
    const it = (prog?.items ?? []).find(i => i.tipo === "tanda");
    const espera = st?.tanda_en_espera ?? 0;
    $("tandaBox").classList.toggle("espera", espera > 0);
    if (espera) {
      proxima = null;
      $("tandaHora").textContent = "";
      $("tandaCuenta").textContent = "EN ESPERA · LÁNZALA ↑";
      $("tandaDet").textContent = `${espera} cuña(s) esperando al operador`;
      return;
    }
    if (!it) {
      proxima = null;
      $("tandaHora").textContent = "";
      $("tandaCuenta").textContent = "—";
      $("tandaDet").textContent = "Sin tandas en lo planificado";
      return;
    }
    const [h, m, s] = it.hora.split(":").map(Number);
    const d = new Date(); d.setHours(h, m, s || 0, 0);
    if (d.getTime() < Date.now() - 12 * 3600e3) d.setDate(d.getDate() + 1);
    proxima = d;
    $("tandaHora").textContent = `· ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}${it.inmediata ? " ⚡" : ""}`;
    $("tandaDet").textContent = `${it.cunas} cuña(s) · ${fmt(it.duracion)}${it.excede ? " · ⚠ supera el máximo" : ""}${it.inmediata ? " · en punto" : ""}`;
  }

  function cuentaTanda() {
    if (!proxima) return;
    const falta = (proxima.getTime() - Date.now()) / 1000;
    // Cuenta regresiva (como "Siguiente Break" de Jazler)
    $("tandaCuenta").textContent = falta > 0 ? fmt(falta) : "AHORA";
    $("tandaBox").classList.toggle("cerca", falta > 0 && falta <= 60);
  }

  function lineaHist(h) {
    const cls = h.tipo === "pauta" ? "pauta" : (h.tipo === "efecto" ? "efecto" : "");
    return `<div class="hist ${cls}"><span class="hist-h">${esc(h.hora)}</span><span class="hist-t" title="${esc(h.titulo)} — ${esc(h.quien)}">${esc(h.titulo)} — ${esc(h.quien)}</span></div>`;
  }

  async function historial() {
    try {
      const rows = await api("/api/v1/historial?limit=25");
      $("historial").innerHTML = rows.map(r => lineaHist({
        hora: (r.iniciado_en ?? "").slice(11, 16), titulo: r.titulo, quien: r.artista, tipo: r.tipo,
      })).join("") || `<div class="vacio">Aún no sale nada al aire</div>`;
    } catch { /* aviso de conexión */ }
  }
  function agregarHistorial(d) {
    const div = document.createElement("div");
    div.innerHTML = lineaHist({
      hora: new Date().toTimeString().slice(0, 5), titulo: d.titulo, quien: d.cliente ?? d.artista,
      tipo: d.origen === "pauta" ? "pauta" : "musica",
    });
    $("historial").querySelector(".vacio")?.remove();
    $("historial").prepend(div.firstChild);
    while ($("historial").children.length > 25) $("historial").lastChild.remove();
  }

  async function cartuchera() {
    try {
      const d = await api("/api/v1/cartuchera");
      bancos = d.bancos ?? {}; slots = d.slots ?? {}; nombres = d.nombres ?? {};
      if (!(banco in bancos)) banco = Object.keys(bancos)[0];
      if (vista !== "hist" && !(vista in bancos)) vista = "hist";
      pintarVista();
    } catch { /* aviso de conexión */ }
  }
  function pintarCart() {
    const n = bancos[banco] ?? 0;
    $("cartuchera").innerHTML = Array.from({ length: n }, (_, i) => {
      const s = (slots[banco] ?? []).find(x => x.slot === i + 1);
      const num = i + 1;
      return s
        ? `<button class="cart-btn cart-${s.tipo_audio === "Cuña" ? "cuna" : (i % 2 ? "b" : "a")}" data-audio="${s.audio_id}" data-slot="${num}" data-n="${num}" title="${esc(s.titulo)}">
             <span class="cart-n">${esc(s.etiqueta || s.titulo)}</span><small><b>${num}</b>${fmt(s.duracion)}</small></button>`
        : `<button class="cart-btn vacio-btn" data-n="${num}" title="Vacío — arrastra aquí una canción de la biblioteca"><span class="cart-n">·</span><small><b>${num}</b></small></button>`;
    }).join("");
  }
  $("derTabs").addEventListener("click", e => {
    const t = e.target.closest(".der-tab");
    if (!t) return;
    vista = t.dataset.vista;
    try { localStorage.setItem("onix_vista_der", vista); } catch { /* sin almacenamiento */ }
    pintarVista();
  });
  // El operador carga sus botones: arrastra una canción de la biblioteca sobre un botón
  const botonDe = e => e.target.closest(".cart-btn[data-slot], .cart-btn.vacio-btn");
  $("cartuchera").addEventListener("dragover", e => {
    if (botonDe(e) && e.dataTransfer.types.includes("onix/audio")) { e.preventDefault(); botonDe(e).classList.add("soltar"); }
  });
  $("cartuchera").addEventListener("dragleave", e => botonDe(e)?.classList.remove("soltar"));
  $("cartuchera").addEventListener("drop", async e => {
    const b = botonDe(e), id = +e.dataTransfer.getData("onix/audio");
    if (!b || !id) return;
    e.preventDefault(); b.classList.remove("soltar");
    const slot = +b.dataset.n;
    if (b.dataset.audio && !confirm(`El botón ${slot} ya tiene «${b.title}». ¿Reemplazarlo?`)) return;
    try {
      await api(`/api/v1/cartuchera/${banco}/${slot}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ audio_id: id }) });
      toast(`Botón ${slot} cargado`, "info", 1500);
    } catch (err) { toast(`No se pudo cargar el botón: ${err.message}`, "error", 3000); }
  });
  // Clic derecho sobre un botón cargado = vaciarlo
  $("cartuchera").addEventListener("contextmenu", async e => {
    const b = e.target.closest(".cart-btn[data-audio]");
    if (!b) return;
    e.preventDefault();
    if (!confirm(`¿Vaciar el botón ${b.dataset.slot} («${b.title}»)?`)) return;
    try { await api(`/api/v1/cartuchera/${banco}/${b.dataset.slot}`, { method: "DELETE" }); }
    catch (err) { toast(`No se pudo vaciar: ${err.message}`, "error", 3000); }
  });
  function disparar(b) {
    WS.send({ cmd: "fire_effect", audio_id: +b.dataset.audio, canal_offset: +b.dataset.slot - 1 });
    b.classList.add("disparado");
    setTimeout(() => b.classList.remove("disparado"), 500);
  }
  $("cartuchera").addEventListener("click", e => {
    const b = e.target.closest(".cart-btn[data-audio]");
    if (b) disparar(b);
  });
  /** Atajo 1–8: botón n del banco que se está viendo */
  function dispararSlot(n) {
    if (vista === "hist") { toast("Abre una paleta para usar los números 1–9", "info", 2000); return; }
    const b = $("cartuchera").querySelector(`.cart-btn[data-slot="${n}"]`);
    if (b) disparar(b);
    else toast(`El botón ${n} de la cartuchera está vacío`, "info", 2000);
  }

  return { tanda, cuentaTanda, historial, agregarHistorial, cartuchera, dispararSlot };
})();

/* ─────────────────────────── vúmetro ───────────────────────────
   El motor manda el pico de la salida del AIRE 10 veces por segundo.
   Se dibuja en dBFS (-40..0) con caída suave y retención del pico. */
const VU = (() => {
  const lados = { l: { fill: $("vuL"), pico: $("vuPL") }, r: { fill: $("vuR"), pico: $("vuPR") } };
  const estado = { l: { obj: 0, ver: 0, pico: 0, picoT: 0 }, r: { obj: 0, ver: 0, pico: 0, picoT: 0 } };
  let ultimo = 0, antes = performance.now();
  const aPct = v => v <= 0.01 ? 0 : Math.max(0, Math.min(100, ((20 * Math.log10(v)) + 40) / 40 * 100));

  function recibir(d) {
    ultimo = performance.now();
    estado.l.obj = aPct(d.l); estado.r.obj = aPct(d.r);
  }
  function dibujar(t) {
    const dt = (t - antes) / 1000; antes = t;
    const sinSenal = t - ultimo > 1500;
    $("vu").classList.toggle("sin-senal", sinSenal);
    for (const k of ["l", "r"]) {
      const e = estado[k];
      if (sinSenal) e.obj = 0;
      e.ver = Math.max(e.obj, e.ver - 60 * dt);            // cae ~60 %/s, sube al instante
      if (e.ver >= e.pico || t - e.picoT > 1500) { e.pico = e.ver; e.picoT = t; }
      lados[k].fill.style.clipPath = `inset(0 ${100 - e.ver}% 0 0)`;
      lados[k].pico.style.left = `calc(${e.pico}% - 2px)`;
      lados[k].pico.style.opacity = e.pico > 1 ? 1 : 0;
    }
    requestAnimationFrame(dibujar);
  }
  requestAnimationFrame(dibujar);
  return { recibir };
})();

/* ─────────────────────────── atajos de teclado ───────────────────────────
   Sin F1–F12: el navegador los usa (F5 recarga, F11 pantalla completa…). */
const Atajos = (() => {
  const ayuda = $("atajos");
  const abrir = () => { ayuda.hidden = false; };
  const cerrar = () => { ayuda.hidden = true; };
  $("btnAtajos").addEventListener("click", abrir);
  $("btnCerrarAtajos").addEventListener("click", cerrar);
  ayuda.addEventListener("click", e => { if (e.target === ayuda) cerrar(); });

  const escribiendo = el => el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !ayuda.hidden) { cerrar(); return; }
    // Ctrl+F: buscar en la biblioteca (en vez del buscador del navegador)
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
      e.preventDefault(); $("libBuscar").focus(); $("libBuscar").select(); return;
    }
    if (escribiendo(e.target)) {
      if (e.key === "Escape") e.target.blur();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    const st = Estado.st;

    if (e.code === "Space") {
      e.preventDefault();
      // Si el foco quedó en un botón, el navegador también lo "presionaría"
      // al soltar Espacio: se quita el foco para que la acción no se repita.
      if (e.target instanceof HTMLButtonElement) e.target.blur();
      const nada = !st?.current_track || st?.retenido;
      WS.send({ cmd: nada ? "play" : "skip" });
      toast(nada ? "▶ REPRODUCIR (Espacio)" : "⏭ SIGUIENTE (Espacio)", "info", 1500);
    } else if (/^Digit[1-9]$/.test(e.code)) {
      e.preventDefault();
      Derecha.dispararSlot(+e.code.slice(5));
    } else if (e.key.toLowerCase() === "p") {
      $("btnPausa").click();
    } else if (e.key.toLowerCase() === "d") {
      $("btnDetener").click();
    } else if (e.key.toLowerCase() === "l") {
      if (st?.tanda_en_espera) WS.send({ cmd: "lanzar_tanda", ahora: true });
      else toast("No hay ninguna tanda en espera", "info", 2000);
    } else if (e.key === "/") {
      e.preventDefault(); $("libBuscar").focus();
    } else if (e.key === "?") {
      abrir();
    } else if (e.key.toLowerCase() === "p") {
      Problemas.abrir();
    } else if (e.key === "Escape" && st?.preescucha) {
      WS.send({ cmd: "cue_stop" });
    }
  });
  return { abrir };
})();

/* ─────────────────────── problemas y aire muerto ───────────────────────
   El motor avisa "aire_muerto" si lo que suena queda en silencio (o si en
   automático no hay nada que poner). El aviso rojo se queda hasta que vuelve
   el sonido unos segundos o el operador presiona ENTENDIDO. El botón
   PROBLEMAS junta lo que está mal ahora y lo que pasó (GET /api/v1/problemas). */
const Problemas = (() => {
  const panel = $("problemas"), boton = $("btnProblemas"), aviso = $("avisoMuerto");
  const UMBRAL = 0.003, VUELVE_MS = 3000;
  let sonandoDesde = null, datos = null;
  const NIVEL = { critico: "CRÍTICO", error: "ERROR", aviso: "AVISO" };
  // "Admin › Relojes" → enlace a esa sección del Admin
  const SECCION = { Biblioteca: "02-biblioteca-musical", Pautas: "03-programacion-pautas",
                    "Motor de audio": "06-motor-audio", Relojes: "08-relojes" };
  function enlace(donde) {
    const nombre = Object.keys(SECCION).find(k => donde.includes(`› ${k}`));
    return nombre ? `<a href="/admin/#${SECCION[nombre]}" target="onix-admin">→ ${esc(donde)}</a>` : `→ ${esc(donde)}`;
  }

  // ── Aire muerto ──
  function aireMuerto(d) {
    $("avisoMuertoTxt").textContent = `⛔ AIRE MUERTO · ${d.msg ?? ""}`;
    aviso.hidden = false;
    sonandoDesde = null;
    cargar();
  }
  function nivel(d) {
    if (aviso.hidden) return;
    if (Math.max(d.l ?? 0, d.r ?? 0) < UMBRAL) { sonandoDesde = null; return; }
    sonandoDesde ??= performance.now();
    if (performance.now() - sonandoDesde > VUELVE_MS) aviso.hidden = true;   // volvió el sonido
  }
  $("btnMuertoOk").addEventListener("click", () => { aviso.hidden = true; });

  // ── Panel ──
  function pintarBoton() {
    if (!datos) return;
    const graves = datos.activos.filter(p => p.nivel !== "aviso").length;
    const n = graves + datos.no_vistos;
    const critico = datos.activos.some(p => p.nivel === "critico")
      || datos.recientes.some(r => !r.visto && r.nivel === "critico");
    $("problemasN").hidden = !n && !datos.activos.length;
    $("problemasN").textContent = n || datos.activos.length;
    boton.classList.toggle("hay-critico", critico);
    boton.classList.toggle("hay-error", !critico && n > 0);
    boton.title = `Problemas (P): ${datos.activos.length} ahora · ${datos.no_vistos} sin ver`;
  }
  function pintarPanel() {
    if (!datos) return;
    $("problemasActivos").innerHTML = datos.activos.length ? datos.activos.map(p => `
      <div class="prob n-${p.nivel}">
        <div class="prob-tit"><i>${NIVEL[p.nivel]}</i>${esc(p.titulo)}</div>
        ${p.detalle ? `<div class="prob-det">${esc(p.detalle)}</div>` : ""}
        ${p.ejemplos.length ? `<details><summary>Ver ${p.ejemplos.length === 15 ? "los primeros 15" : p.ejemplos.length}</summary>
          <ul>${p.ejemplos.map(x => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
        ${p.donde ? `<div class="prob-donde">${enlace(p.donde)}</div>` : ""}
      </div>`).join("")
      : `<div class="prob-ok">✓ No hay nada pendiente.</div>`;
    $("problemasRecTxt").textContent = datos.recientes.length ? `(${datos.no_vistos} sin ver)` : "";
    $("problemasRecientes").innerHTML = datos.recientes.length ? datos.recientes.slice(0, 60).map(r => `
      <div class="rec n-${r.nivel}${r.visto ? " visto" : ""}"><time>${esc(r.ts.slice(11, 19))}</time><span>${esc(r.msg)}</span></div>`).join("")
      : `<div class="prob-ok">✓ Nada desde que arrancó ONIX.</div>`;
  }
  async function cargar(forzar = false) {
    try {
      datos = await api(`/api/v1/problemas${forzar ? "?forzar=true" : ""}`);
      pintarBoton();
      if (!panel.hidden) pintarPanel();
    } catch { /* sin conexión: ya lo dice el aviso de conexión */ }
  }
  let _t;
  const pronto = () => { clearTimeout(_t); _t = setTimeout(cargar, 800); };

  const abrir = () => { panel.hidden = false; pintarPanel(); cargar(); };
  const cerrar = () => { panel.hidden = true; };
  boton.addEventListener("click", abrir);
  $("btnCerrarProblemas").addEventListener("click", cerrar);
  panel.addEventListener("click", e => { if (e.target === panel) cerrar(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !panel.hidden) cerrar(); });
  $("btnProblemasVistos").addEventListener("click", async () => {
    await api("/api/v1/problemas/vistos", { method: "POST" }).catch(() => {});
    cargar();
  });
  $("btnProblemasRevisar").addEventListener("click", () => cargar(true));

  cargar();
  setInterval(cargar, 30000);
  return { abrir, aireMuerto, nivel, pronto };
})();

/* ── Usuario: desde otra PC se muestra SALIR; el operador no ve el Admin ── */
(async () => {
  const yo = await api("/api/v1/auth/yo").catch(() => null);
  if (!yo || yo.local) return;
  const b = $("btnSalir");
  b.hidden = false;
  b.textContent = `SALIR · ${(yo.nombre || yo.usuario).toUpperCase()}`;
  b.addEventListener("click", async () => {
    await fetch("/api/v1/auth/logout", { method: "POST" });
    location.href = "/login";
  });
  if (yo.rol === "operador") {
    $("lnkAdmin").hidden = true;
  }
})();

/* ─────────────────────────── eventos del motor ─────────────────────────── */
let _tProg;
const programaPronto = () => { clearTimeout(_tProg); _tProg = setTimeout(Estado.cargarPrograma, 250); };

WS.on("track_started", d => { Salto.cancelar(); Estado.poll(); programaPronto(); Derecha.agregarHistorial(d); });
WS.on("seek", () => Estado.poll());
WS.on("vu", d => { VU.recibir(d); Problemas.nivel(d); });
WS.on("aire_muerto", d => Problemas.aireMuerto(d));
["playback_error", "pauta_missed", "respaldo_activado", "tanda_excedida", "watchdog_stall", "engine_stopped"]
  .forEach(ev => WS.on(ev, Problemas.pronto));
WS.on("playback_stopped", () => Estado.poll());
WS.on("programa_actualizado", programaPronto);
WS.on("queue_updated", programaPronto);
WS.on("mode_changed", () => { Estado.poll(); programaPronto(); });
WS.on("detener_al_terminar", () => Estado.poll());
WS.on("pausa", () => Estado.poll());
WS.on("cue_started", () => Estado.poll());
WS.on("cue_stopped", () => Estado.poll());
WS.on("cartuchera_updated", () => Derecha.cartuchera());
let _libT;     // al subir muchos audios llegan avisos seguidos: se juntan en una sola recarga
WS.on("library_updated", () => { clearTimeout(_libT); _libT = setTimeout(() => Biblioteca.cargar(), 1000); });
WS.on("effect_fired", () => {});
WS.on("engine_stopped", () => toast("⚠ Motor de audio detenido o desconectado", "error", 8000));
WS.on("engine_connected", () => { toast("✓ Motor de audio conectado", "info", 3000); Estado.refrescarTodo(); });
WS.on("playback_error", d => toast(`⚠ ${d.msg ?? "Error de reproducción"}`, "error", 8000));
WS.on("respaldo_activado", d => toast(`⚠ Sonando lista de respaldo: ${d.archivo ?? ""}`, "aviso", 8000));
WS.on("tanda_scheduled", d => {
  Estado.poll(); programaPronto();
  if (d.requiere_operador) toast(`📢 Tanda :${String(d.minuto).padStart(2, "0")} lista. Lánzala cuando quieras.`, "aviso", 8000);
});
WS.on("evento_inmediato", d => toast(`⏰ ${d.hora} · ${d.evento ?? "Evento"} entra en punto: se cortó la canción`, "aviso", 5000));
WS.on("tanda_inmediata", d => toast(`⚡ Tanda :${String(d.minuto).padStart(2, "0")} en punto: se cortó la canción`, "aviso", 5000));
WS.on("tanda_excedida", d => toast(`⚠ La tanda :${String(d.minuto).padStart(2, "0")} dura ${fmt(d.duracion)} (máximo ${fmt(d.maximo)})`, "aviso", 8000));
WS.on("watchdog_stall", () => toast("⚠ El motor se trabó y se está recuperando", "error", 8000));

/* ─────────────────────────── arranque ─────────────────────────── */
// Al final: todos los módulos ya están definidos.
Estado.iniciar();
